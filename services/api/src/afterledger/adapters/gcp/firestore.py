"""Case state and researched knowledge in Firestore.

The prototype runs one API instance (Cloud Run, maximum one instance), so each store keeps a write-through
cache in memory, as the local store does: a collection is read from Firestore once, then served from memory,
and every write reaches Firestore before it returns. Each record is stored as one JSON string, so Firestore's
rules on field names and nested arrays never bend the domain model.

    cases/{case_id}                          {"json": CaseFile, "title", "updated"}
    cases/{case_id}/{collection}/{id}        {"json": record}
    knowledge/{collection}/records/{id}      {"json": record}    an overlay on the knowledge in the image
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ...domain.case import COLLECTIONS, CaseFile
from ..local.stores import LocalKnowledgeStore

_BATCH = 450                                  # Firestore allows 500 writes in one batch


def _dump(record: BaseModel | dict) -> str:
    data = record.model_dump(mode="json") if isinstance(record, BaseModel) else record
    return json.dumps(data, ensure_ascii=False, default=str)


class FirestoreCaseStore:
    def __init__(self, project: str | None, database: str = "(default)", client: Any = None):
        if client is None:
            from google.cloud import firestore
            client = firestore.AsyncClient(project=project, database=database)
        self.db = client
        self._cases: dict[str, dict] = {}
        self._cache: dict[tuple[str, str], dict[str, dict]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def _case_ref(self, case_id: str) -> Any:
        return self.db.collection("cases").document(case_id)

    def _col(self, case_id: str, collection: str) -> Any:
        return self._case_ref(case_id).collection(collection)

    # ---- the case file ----------------------------------------------------------------------
    async def put_case(self, case: CaseFile) -> None:
        data = case.model_dump(mode="json")
        async with self._locks[(case.id, "__case__")]:
            await self._case_ref(case.id).set({"json": json.dumps(data, ensure_ascii=False), "title": case.title,
                                               "updated": _now()})
            self._cases[case.id] = data

    async def raw_case(self, case_id: str) -> dict | None:
        """The case file as stored (the store's own copy: never changed in place)."""
        data = self._cases.get(case_id)
        if data is None:
            snap = await self._case_ref(case_id).get()
            if not snap.exists:
                return None
            data = json.loads(snap.to_dict()["json"])
            self._cases[case_id] = data
        return data

    async def get_case(self, case_id: str) -> CaseFile | None:
        data = await self.raw_case(case_id)
        return CaseFile.model_validate(data) if data is not None else None

    async def list_cases(self) -> list[CaseFile]:
        out = []
        async for snap in self.db.collection("cases").stream():
            raw = snap.to_dict() or {}
            if "json" not in raw:
                continue
            data = json.loads(raw["json"])
            self._cases[snap.id] = data
            out.append(CaseFile.model_validate(data))
        return out

    async def delete_case(self, case_id: str) -> None:
        await self.db.recursive_delete(self._case_ref(case_id))
        self._cases.pop(case_id, None)
        for key in [k for k in self._cache if k[0] == case_id]:
            self._cache.pop(key, None)

    # ---- collections ------------------------------------------------------------------------
    async def _load(self, case_id: str, collection: str) -> dict[str, dict]:
        key = (case_id, collection)
        if key not in self._cache:
            records: dict[str, dict] = {}
            async for snap in self._col(case_id, collection).stream():
                raw = snap.to_dict() or {}
                if "json" in raw:
                    records[raw.get("id") or snap.id] = json.loads(raw["json"])
            self._cache[key] = records
        return self._cache[key]

    async def put(self, case_id: str, collection: str, record: BaseModel) -> None:
        async with self._locks[(case_id, collection)]:
            data = await self._load(case_id, collection)
            await self._col(case_id, collection).document(_doc_id(record.id)).set({"id": record.id, "json": _dump(record)})
            data[record.id] = record.model_dump(mode="json")

    async def put_many(self, case_id: str, collection: str, records: list[BaseModel]) -> None:
        if not records:
            return
        async with self._locks[(case_id, collection)]:
            data = await self._load(case_id, collection)
            col = self._col(case_id, collection)
            for i in range(0, len(records), _BATCH):
                batch = self.db.batch()
                for r in records[i:i + _BATCH]:
                    batch.set(col.document(_doc_id(r.id)), {"id": r.id, "json": _dump(r)})
                await batch.commit()
            for r in records:
                data[r.id] = r.model_dump(mode="json")

    async def raw_records(self, case_id: str, collection: str) -> dict[str, dict]:
        """A collection as stored, by id (the store's own copies: never changed in place)."""
        return await self._load(case_id, collection)

    async def get(self, case_id: str, collection: str, record_id: str) -> Any | None:
        raw = (await self._load(case_id, collection)).get(record_id)
        return COLLECTIONS[collection].model_validate(raw) if raw is not None else None

    async def list(self, case_id: str, collection: str) -> list[Any]:
        model = COLLECTIONS[collection]
        return [model.model_validate(v) for v in (await self._load(case_id, collection)).values()]

    async def delete(self, case_id: str, collection: str, record_id: str) -> None:
        async with self._locks[(case_id, collection)]:
            data = await self._load(case_id, collection)
            await self._col(case_id, collection).document(_doc_id(record_id)).delete()
            data.pop(record_id, None)

    async def delete_many(self, case_id: str, collection: str, record_ids: list[str]) -> None:
        """Many records at once, in batches (a long statement's text lines run to tens of thousands)."""
        if not record_ids:
            return
        async with self._locks[(case_id, collection)]:
            data = await self._load(case_id, collection)
            col = self._col(case_id, collection)
            for i in range(0, len(record_ids), _BATCH):
                batch = self.db.batch()
                for rid in record_ids[i:i + _BATCH]:
                    batch.delete(col.document(_doc_id(rid)))
                await batch.commit()
            for rid in record_ids:
                data.pop(rid, None)


class FirestoreKnowledgeStore(LocalKnowledgeStore):
    """The knowledge shipped in the image (read only) with an overlay in Firestore, where research results
    and newly verified rules go, so they outlive the instance."""

    def __init__(self, base: Path, overlay_dir: Path, project: str | None, database: str = "(default)",
                 client: Any = None):
        super().__init__(base, overlay_dir)
        if client is None:
            from google.cloud import firestore
            client = firestore.AsyncClient(project=project, database=database)
        self.db = client
        self._overlay_cache: dict[str, dict[str, dict]] = {}

    def _records(self, collection: str) -> Any:
        return self.db.collection("knowledge").document(collection).collection("records")

    async def _overlay(self, collection: str) -> dict[str, dict]:
        if collection not in self._overlay_cache:
            found: dict[str, dict] = {}
            async for snap in self._records(collection).stream():
                raw = snap.to_dict() or {}
                if "json" in raw:
                    found[raw.get("id") or snap.id] = json.loads(raw["json"])
            self._overlay_cache[collection] = found
        return self._overlay_cache[collection]

    async def get(self, collection: str, record_id: str) -> dict | None:
        over = await self._overlay(collection)
        if record_id in over:
            return over[record_id]
        return self._load_base(collection).get(record_id)

    async def list(self, collection: str) -> list[dict]:
        merged = dict(self._load_base(collection))
        merged.update(await self._overlay(collection))
        return list(merged.values())

    async def put(self, collection: str, record_id: str, record: dict) -> None:
        clean = json.loads(json.dumps(record, default=str))
        async with self._lock:
            over = await self._overlay(collection)
            await self._records(collection).document(_doc_id(record_id)).set(
                {"id": record_id, "json": json.dumps(clean, ensure_ascii=False)})
            over[record_id] = clean

    async def delete(self, collection: str, record_id: str) -> bool:
        """Removes a record written here (the knowledge shipped with the app cannot change); True if there was one."""
        async with self._lock:
            over = await self._overlay(collection)
            if record_id not in over:
                return False
            await self._records(collection).document(_doc_id(record_id)).delete()
            over.pop(record_id, None)
            return True


def _doc_id(record_id: str) -> str:
    """Firestore document ids cannot contain '/' (plan keys such as 'IN/rbi_deposit' do), nor be '.' or '..';
    the escaped id names the document and the record keeps its own id inside."""
    clean = record_id.replace("/", "~")
    return clean if clean not in (".", "..") else f"_{clean}"


def _now() -> Any:
    from google.cloud import firestore
    return firestore.SERVER_TIMESTAMP
