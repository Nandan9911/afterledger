"""Demo case snapshots: a seeded family's case and files, kept aside so a reset takes seconds instead of
replaying the family's timeline.

Two places hold them, looked at in this order:
- the state directory (demo_snapshots/<key>), written here after a family is seeded with the local store;
- the demo bundles shipped with a deployment (AL_DEMO_BUNDLES), built offline by `afterledger build-demos`.
  This is how a Google Cloud deployment gets its six demo cases without replaying six stories through a live
  model: the bundles say they were prepared with scripted responses, and the app shows that.

A snapshot uses the local store's own layout: case/ (case.json and one JSON file per collection) and blobs/
(the files under cases/<case_id>/ in the blob store). Restoring into the local store copies the folders.
Restoring into any other store (Firestore and Cloud Storage) writes every record through the store's port and
uploads every file at the same path, so the case's 'local:' file references resolve unchanged.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
from pathlib import Path
from typing import Any

from .seeder import demo_case_id

_UPLOADS_AT_ONCE = 8


def _local(deps: Any) -> bool:
    return deps.settings.store == "local"


def snapshot_dir(deps: Any, key: str) -> Path:
    return deps.settings.state_dir / "demo_snapshots" / key


def _complete(d: Path) -> bool:
    return (d / "case" / "case.json").exists()


def source_for(deps: Any, key: str) -> Path | None:
    """Where this family's snapshot is: the state directory's own, else the deployment's bundle."""
    places = [snapshot_dir(deps, key)] if _local(deps) else []
    bundles = getattr(deps.settings, "demo_bundles", None)
    if bundles:
        places.append(Path(bundles) / key)
    return next((d for d in places if _complete(d)), None)


def has_snapshot(deps: Any, key: str) -> bool:
    return source_for(deps, key) is not None


def save(deps: Any, key: str, target: Path | None = None) -> bool:
    """Copies a seeded family aside (local store only). target: somewhere else to write it, for bundles."""
    if not _local(deps):
        return False
    case_id = demo_case_id(key)
    root = deps.settings.state_dir
    d = target or snapshot_dir(deps, key)
    src_case = root / "cases" / case_id
    src_blobs = root / "blobs" / "cases" / case_id
    if not (src_case / "case.json").exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_case, d / "case")
    if src_blobs.exists():
        shutil.copytree(src_blobs, d / "blobs")
    return True


async def restore(deps: Any, key: str) -> bool:
    src = source_for(deps, key)
    if src is None:
        return False
    case_id = demo_case_id(key)
    if _local(deps):
        root = deps.settings.state_dir
        await deps.cases.delete_case(case_id)
        shutil.rmtree(root / "blobs" / "cases" / case_id, ignore_errors=True)
        shutil.copytree(src / "case", root / "cases" / case_id)
        if (src / "blobs").exists():
            shutil.copytree(src / "blobs", root / "blobs" / "cases" / case_id)
    else:
        await import_snapshot(deps, src, case_id)
    deps.changed(case_id, "case")
    return True


async def import_snapshot(deps: Any, src: Path, case_id: str) -> None:
    """Writes a snapshot into whatever stores the service uses. The case file goes last, so a case that is
    half imported is never taken for a ready one."""
    from ..domain.case import COLLECTIONS, CaseFile

    case = CaseFile.model_validate_json((src / "case" / "case.json").read_text(encoding="utf-8"))
    if case.id != case_id:
        raise ValueError(f"snapshot {src} holds case {case.id}, not {case_id}")
    await deps.cases.delete_case(case_id)
    drop = getattr(deps.blobs, "delete_prefix", None)
    if drop is not None:
        await drop(f"cases/{case_id}/")
    for f in sorted((src / "case").glob("*.json")):
        if f.stem == "case" or f.stem not in COLLECTIONS:
            continue
        records = json.loads(f.read_text(encoding="utf-8"))
        model = COLLECTIONS[f.stem]
        await deps.cases.put_many(case_id, f.stem, [model.model_validate(r) for r in records.values()])

    files = sorted(p for p in (src / "blobs").rglob("*") if p.is_file()) if (src / "blobs").exists() else []
    gate = asyncio.Semaphore(_UPLOADS_AT_ONCE)

    async def upload(f: Path) -> None:
        rel = f.relative_to(src / "blobs").as_posix()
        async with gate:
            await deps.blobs.put(f"cases/{case_id}/{rel}", f.read_bytes(),
                                 mimetypes.guess_type(f.name)[0] or "application/octet-stream")

    await asyncio.gather(*(upload(f) for f in files))
    await deps.cases.put_case(case)


def bundle_report(deps: Any) -> dict | None:
    """The evaluation report built with the bundles (offline), shown until an evaluation runs here."""
    bundles = getattr(deps.settings, "demo_bundles", None)
    f = Path(bundles) / "eval_latest.json" if bundles else None
    if f is None or not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8"))
