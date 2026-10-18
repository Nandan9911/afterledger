"""Local adapters: JSON files for case state and knowledge, the filesystem for blobs.

Used offline and in development. Writes are atomic (write to a temporary file, then rename) and
serialised per file with an asyncio lock, so concurrent workflow nodes cannot corrupt a file.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel

from ...domain.case import COLLECTIONS, CaseFile


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class SystemClock:
    """The real time, on the clock of a time zone (AL_TIMEZONE) or of this computer. Activity is shown at the time
    written, so a deployment in UTC can still show its users' own clock."""

    def __init__(self, tz: str | None = None):
        self.tz = ZoneInfo(tz) if tz else None

    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        return datetime.now(self.tz) if self.tz else datetime.now().astimezone()


class FixedClock(SystemClock):
    def __init__(self, day: date, tz: str | None = None):
        super().__init__(tz)
        self.day = day

    def today(self) -> date:
        return self.day

    def now(self) -> datetime:
        return super().now().replace(year=self.day.year, month=self.day.month, day=self.day.day)


def make_clock(spec: str, tz: str | None = None) -> SystemClock:
    if spec and spec.startswith("fixed:"):
        return FixedClock(date.fromisoformat(spec.split(":", 1)[1]), tz)
    return SystemClock(tz)


class LocalCaseStore:
    """cases/<case_id>/case.json plus one JSON file per collection, cached in memory."""

    def __init__(self, root: Path):
        self.root = root / "cases"
        self.root.mkdir(parents=True, exist_ok=True)
        self._cache: dict[tuple[str, str], dict[str, dict]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _dir(self, case_id: str) -> Path:
        return self.root / case_id

    async def put_case(self, case: CaseFile) -> None:
        async with self._locks[(case.id, "__case__")]:
            _atomic_write(self._dir(case.id) / "case.json", case.model_dump_json(indent=1))

    async def get_case(self, case_id: str) -> CaseFile | None:
        path = self._dir(case_id) / "case.json"
        if not path.exists():
            return None
        return CaseFile.model_validate_json(path.read_text(encoding="utf-8"))

    async def list_cases(self) -> list[CaseFile]:
        out = []
        for d in sorted(self.root.iterdir()) if self.root.exists() else []:
            p = d / "case.json"
            if p.exists():
                out.append(CaseFile.model_validate_json(p.read_text(encoding="utf-8")))
        return out

    async def delete_case(self, case_id: str) -> None:
        shutil.rmtree(self._dir(case_id), ignore_errors=True)
        for key in [k for k in self._cache if k[0] == case_id]:
            self._cache.pop(key, None)

    def _load(self, case_id: str, collection: str) -> dict[str, dict]:
        key = (case_id, collection)
        if key not in self._cache:
            path = self._dir(case_id) / f"{collection}.json"
            self._cache[key] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return self._cache[key]

    def _flush(self, case_id: str, collection: str) -> None:
        data = self._cache[(case_id, collection)]
        _atomic_write(self._dir(case_id) / f"{collection}.json", json.dumps(data, ensure_ascii=False, indent=1))

    async def put(self, case_id: str, collection: str, record: BaseModel) -> None:
        async with self._locks[(case_id, collection)]:
            data = self._load(case_id, collection)
            data[record.id] = record.model_dump(mode="json")
            self._flush(case_id, collection)

    async def put_many(self, case_id: str, collection: str, records: list[BaseModel]) -> None:
        if not records:
            return
        async with self._locks[(case_id, collection)]:
            data = self._load(case_id, collection)
            for r in records:
                data[r.id] = r.model_dump(mode="json")
            self._flush(case_id, collection)

    async def get(self, case_id: str, collection: str, record_id: str) -> Any | None:
        raw = self._load(case_id, collection).get(record_id)
        return COLLECTIONS[collection].model_validate(raw) if raw is not None else None

    async def list(self, case_id: str, collection: str) -> list[Any]:
        model = COLLECTIONS[collection]
        return [model.model_validate(v) for v in self._load(case_id, collection).values()]

    async def delete(self, case_id: str, collection: str, record_id: str) -> None:
        async with self._locks[(case_id, collection)]:
            data = self._load(case_id, collection)
            if data.pop(record_id, None) is not None:
                self._flush(case_id, collection)


class LocalKnowledgeStore:
    """Read-only base knowledge from data/knowledge (YAML or JSON, one file per collection or a
    folder of records) plus a writable overlay in the state directory, where research results go."""

    def __init__(self, base: Path, overlay: Path):
        self.base = base
        self.overlay = overlay / "knowledge"
        self.overlay.mkdir(parents=True, exist_ok=True)
        self._base_cache: dict[str, dict[str, dict]] = {}
        self._lock = asyncio.Lock()

    def _load_base(self, collection: str) -> dict[str, dict]:
        if collection in self._base_cache:
            return self._base_cache[collection]
        records: dict[str, dict] = {}
        for suffix in (".yaml", ".yml", ".json"):
            f = self.base / f"{collection}{suffix}"
            if f.exists():
                records.update(_records_from(f))
        folder = self.base / collection
        if folder.is_dir():
            for f in sorted(folder.rglob("*")):
                if f.suffix in (".yaml", ".yml", ".json"):
                    records.update(_records_from(f))
        self._base_cache[collection] = records
        return records

    def _overlay_file(self, collection: str) -> Path:
        return self.overlay / f"{collection}.json"

    def _load_overlay(self, collection: str) -> dict[str, dict]:
        f = self._overlay_file(collection)
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}

    async def get(self, collection: str, record_id: str) -> dict | None:
        over = self._load_overlay(collection)
        if record_id in over:
            return over[record_id]
        return self._load_base(collection).get(record_id)

    async def list(self, collection: str) -> list[dict]:
        merged = dict(self._load_base(collection))
        merged.update(self._load_overlay(collection))
        return list(merged.values())

    async def put(self, collection: str, record_id: str, record: dict) -> None:
        async with self._lock:
            over = self._load_overlay(collection)
            over[record_id] = json.loads(json.dumps(record, default=str))
            _atomic_write(self._overlay_file(collection), json.dumps(over, ensure_ascii=False, indent=1))

    async def delete(self, collection: str, record_id: str) -> bool:
        """Removes a record written here (the knowledge shipped with the app cannot change); True if there was one."""
        async with self._lock:
            over = self._load_overlay(collection)
            if record_id not in over:
                return False
            del over[record_id]
            _atomic_write(self._overlay_file(collection), json.dumps(over, ensure_ascii=False, indent=1))
            return True


def _records_from(path: Path) -> dict[str, dict]:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.suffix in (".yaml", ".yml") else json.loads(text)
    if data is None:
        return {}
    if isinstance(data, dict) and len(data) == 1 and isinstance(next(iter(data.values())), list):
        data = next(iter(data.values()))     # {"assertions": [...]} style files
    if isinstance(data, list):
        return {str(r.get("id") or r.get("key") or r.get("url")): r for r in data}
    if isinstance(data, dict) and ("id" in data or "key" in data or "url" in data) and not all(
            isinstance(v, dict) for v in data.values()):
        return {str(data.get("id") or data.get("key") or data.get("url")): data}
    return {str(k): ({"id": k} | v if isinstance(v, dict) and "id" not in v and "key" not in v else v)
            for k, v in data.items()}


class LocalBlobStore:
    """Refs look like 'local:cases/<id>/pages/p1.png' (written here) or 'data:families/verma/...'
    (read-only fixtures shipped in the repository)."""

    def __init__(self, root: Path, data_dir: Path):
        self.root = root / "blobs"
        self.data_dir = data_dir
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        scheme, _, rel = ref.partition(":")
        base = self.data_dir if scheme == "data" else self.root
        p = (base / rel).resolve()
        if not str(p).startswith(str(base.resolve())):
            raise ValueError("blob path escapes its root")
        return p

    async def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        ref = f"local:{path}"
        target = self._path(ref)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return ref

    async def get(self, ref: str) -> bytes:
        return self._path(ref).read_bytes()

    async def exists(self, ref: str) -> bool:
        try:
            return self._path(ref).exists()
        except ValueError:
            return False

    def content_type(self, ref: str) -> str:
        return mimetypes.guess_type(ref)[0] or "application/octet-stream"

    async def delete_prefix(self, prefix: str) -> int:
        """Removes every file under a prefix (a deleted case's papers and packs)."""
        if not prefix or ".." in Path(prefix).parts:
            raise ValueError(f"not a prefix to delete: {prefix!r}")
        target = self._path(f"local:{prefix}")
        if not target.exists():
            return 0
        n = sum(1 for f in target.rglob("*") if f.is_file())
        shutil.rmtree(target, ignore_errors=True)
        return n


class JsonlTrace:
    def __init__(self, root: Path):
        self.root = root / "traces"
        self.root.mkdir(parents=True, exist_ok=True)

    async def record(self, event: dict) -> None:
        day = datetime.now().strftime("%Y-%m-%d")
        with (self.root / f"{day}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
