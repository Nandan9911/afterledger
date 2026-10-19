"""How work on one case shares it.

Two locks per case:
- the write lock serialises changes. A family's action holds it for the little time the action takes. Background work
  (reading papers, working out what they point to, planning, preparing papers, planning Today) takes it only for each
  write, never while it waits for a model, OCR, a translation or the web. So a family's action never waits for a model:
  until 19 October 2026 a background step held the case for up to 96 s, and an approval made meanwhile waited that long
  (or failed in the browser).
- the jobs lock keeps one background job at a time on a case, so two jobs never work out the same thing twice.

Background work can rest on what it read before a family's action changed the case. Each of its writes is therefore
merged, field by field, with the record as it now stands: a field the job did not change keeps its current value, and a
field both changed keeps the newer value, the family's. A record deleted meanwhile is not written back, nor is a record
the job meant to delete removed when someone changed it meanwhile; and a job that began before its case was deleted
writes nothing more to it.

What each case's background work is doing (working, work_now) is kept here too, for the case's header: a job is
marked from the moment it is queued, so a screen never looks finished while its change is still being worked out.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .domain.case import COLLECTIONS, CaseFile

log = logging.getLogger(__name__)

_ids = itertools.count(1)
ABSENT: Any = object()          # "the record did not exist" (as read, or as of a moment)
_NONE: Any = object()           # a field a record does not have
CASE = "__case__"               # the case file's own slot among a case's records
# appended and never changed: no merging, no history
APPEND_ONLY = frozenset({"activity", "ledger", "lines"})
HISTORY = 4                     # versions kept per record, to find what it was when a job began
Records = list                  # (the store's method named list hides the builtin in its class)


@dataclass(eq=False)
class Writer:
    """Who is changing a case now: a family's action, or a background job (whose writes are merged)."""
    kind: str                                         # "action" | "job"
    case_id: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    start: int = 0                                    # the store's change number when the writer began
    holding: set[str] = field(default_factory=set)    # cases whose write lock this writer holds now
    seen: dict[tuple, Any] = field(default_factory=dict)  # (case, collection, id) -> the record as last seen
    merged: int = 0                                   # writes merged with changes made meanwhile
    kept_theirs: int = 0                              # fields both changed, where the newer value stood

    @property
    def merges(self) -> bool:
        return self.kind == "job"


WRITER: ContextVar[Writer | None] = ContextVar("afterledger_writer", default=None)


class _Gates:
    def __init__(self) -> None:
        self.write = asyncio.Lock()
        self.jobs = asyncio.Lock()


def _gates(deps: Any, case_id: str) -> _Gates:
    g = deps.extras.setdefault("case_gates", {})
    if case_id not in g:
        g[case_id] = _Gates()
    return g[case_id]


def _counter(deps: Any) -> int:
    store = getattr(deps, "cases", None)
    return getattr(store, "n", 0)


def job_running(deps: Any, case_id: str) -> bool:
    """Whether a background job is working on the case now."""
    return _gates(deps, case_id).jobs.locked()


# What a case's header says while background work runs, the first that applies: reading new papers, working out what
# they point to, working an answer or a change into the plans, preparing a plan's papers.
WORK_STEPS = ("reading", "finding", "planning", "preparing")
QUEUED_FOR = 600.0      # seconds a queued job that never started is shown as under way (a task lost on its way)


def working(deps: Any, case_id: str, key: str, step: str | None, *, queued: bool = False) -> None:
    """What one of the case's background jobs is doing, or None when it is done. A job is marked from the moment it
    is queued (it starts a moment later), so a screen refetched straight after the action already says what is being
    worked out instead of looking finished."""
    jobs = deps.extras.setdefault("case_jobs", {}).setdefault(case_id, {})
    if step is None:
        jobs.pop(key, None)
    else:
        jobs[key] = {"step": step, "since": (jobs.get(key) or {}).get("since") or deps.now(), "queued": queued,
                     "at": time.monotonic()}
    if not jobs:
        deps.extras["case_jobs"].pop(case_id, None)
    deps.changed(case_id, "work")


def work_now(deps: Any, case_id: str) -> dict | None:
    """The step the case's background work is at ({"step", "since"}), or None when nothing is under way."""
    jobs = (deps.extras.get("case_jobs") or {}).get(case_id) or {}
    now = time.monotonic()
    for key in [k for k, j in jobs.items() if j["queued"] and now - j["at"] > QUEUED_FOR]:
        jobs.pop(key, None)
    if not jobs:
        return None
    steps = {j["step"] for j in jobs.values()}
    return {"step": next(s for s in WORK_STEPS if s in steps), "since": min(j["since"] for j in jobs.values())}


@asynccontextmanager
async def holding_write(deps: Any, case_id: str) -> AsyncIterator[None]:
    """The case's write lock, unless the current writer already holds it (a family's action, or a block of a job's
    writes)."""
    w = WRITER.get()
    if w is None:
        w = Writer(kind="action", case_id=case_id, start=_counter(deps))
        token = WRITER.set(w)
    else:
        token = None
    try:
        if case_id in w.holding:
            yield
            return
        async with _gates(deps, case_id).write:
            w.holding.add(case_id)
            try:
                yield
            finally:
                w.holding.discard(case_id)
    finally:
        if token is not None:
            WRITER.reset(token)


@asynccontextmanager
async def action(deps: Any, case_id: str) -> AsyncIterator[None]:
    """A family's action: the case's write lock for its whole (short) run. Inside a job it is a block of the job's
    writes, so a job that calls a function written for actions keeps its own rules."""
    w = WRITER.get()
    if w is not None and (w.kind == "job" or case_id in w.holding):
        async with holding_write(deps, case_id):
            yield
        return
    token = WRITER.set(Writer(kind="action", case_id=case_id, start=_counter(deps)))
    try:
        async with holding_write(deps, case_id):
            yield
    finally:
        WRITER.reset(token)


@asynccontextmanager
async def job(deps: Any, case_id: str, *, exclusive: bool = True, wait: float | None = None) -> AsyncIterator[Writer]:
    """Background work on a case (or an action that waits on a model without holding the case): a writer of its own
    whose writes are merged, the write lock only for each write, and, when exclusive, one such job at a time. With
    wait, a job already at work is waited for that many seconds at most (TimeoutError after)."""
    gates = _gates(deps, case_id)
    w = Writer(kind="job", case_id=case_id)
    token = WRITER.set(w)
    try:
        if exclusive:
            if wait is None:
                await gates.jobs.acquire()
            else:
                async with asyncio.timeout(wait):
                    await gates.jobs.acquire()
            try:
                w.start = _counter(deps)
                yield w
            finally:
                gates.jobs.release()
        else:
            w.start = _counter(deps)
            yield w
    finally:
        WRITER.reset(token)
        if w.merged:
            log.info("case %s: %d write(s) merged with changes made meanwhile; %d field(s) kept the newer value",
                     case_id, w.merged, w.kept_theirs)


class CaseLock:
    """case_lock(deps, case_id): what a family's action takes (async with), and whether a job is at work."""

    def __init__(self, deps: Any, case_id: str):
        self.deps, self.case_id = deps, case_id

    def busy(self) -> bool:
        """A background job is working on the case."""
        return job_running(self.deps, self.case_id)

    def locked(self) -> bool:
        return _gates(self.deps, self.case_id).write.locked() or self.busy()

    async def __aenter__(self) -> CaseLock:
        self._cm = action(self.deps, self.case_id)
        await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._cm.__aexit__(*exc)


def merge3(base: dict, mine: dict, theirs: dict) -> tuple[dict, list[str]]:
    """A job's record (mine), written over one someone changed since the job read it (base): the fields the job changed
    from base, on top of the record as it now stands (theirs). A field both changed keeps theirs, the newer."""
    out = dict(theirs)
    kept: list[str] = []
    for k in mine.keys() | theirs.keys() | base.keys():
        b, m, t = base.get(k, _NONE), mine.get(k, _NONE), theirs.get(k, _NONE)
        if m == b:
            continue                          # the job left it as it was: the current value stands
        if t in (b, m):
            if m is _NONE:
                out.pop(k, None)
            else:
                out[k] = m
            continue
        kept.append(k)                        # both changed it: the newer (current) value stands
    return out, kept


class GuardedCases:
    """The case store, with the rules above. Reads are served as before (a record built from the store's copy);
    writes take the case's write lock, and a job's write is merged with changes made since the job read the record."""

    def __init__(self, inner: Any):
        self.inner = inner
        self.n = 0                                            # change number: one more for every write
        self._ver: dict[tuple, tuple[int, int | None]] = {}   # key -> (change number, writer id) of its last write
        self._hist: dict[tuple, deque] = {}                    # key -> recent (change number, record) versions
        self._gone: dict[str, tuple[int, int | None]] = {}     # deleted case -> (change number, deleting writer)
        self.deps: Any = None                                  # set by build_deps (the locks live on deps)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    # ---- reading --------------------------------------------------------------------------------
    @staticmethod
    def _note(key: tuple, raw: Any) -> None:
        w = WRITER.get()
        if w is not None and w.merges and key[1] not in APPEND_ONLY:
            w.seen[key] = ABSENT if raw is None else raw

    async def get_case(self, case_id: str) -> CaseFile | None:
        raw = await self.inner.raw_case(case_id)
        self._note((case_id, CASE, case_id), raw)
        return CaseFile.model_validate(raw) if raw is not None else None

    async def get(self, case_id: str, collection: str, record_id: str) -> Any | None:
        raw = (await self.inner.raw_records(case_id, collection)).get(record_id)
        self._note((case_id, collection, record_id), raw)
        return COLLECTIONS[collection].model_validate(raw) if raw is not None else None

    async def list(self, case_id: str, collection: str) -> Records[Any]:
        recs = await self.inner.raw_records(case_id, collection)
        items = list(recs.items())
        w = WRITER.get()
        if w is not None and w.merges and collection not in APPEND_ONLY:
            for rid, raw in items:
                w.seen[(case_id, collection, rid)] = raw
        model = COLLECTIONS[collection]
        return [model.model_validate(raw) for _, raw in items]

    # ---- writing --------------------------------------------------------------------------------
    def _dropped(self, case_id: str) -> bool:
        """A writer that began before its case was deleted writes nothing more to it (the writer that deleted it may
        make it again: a sample reset)."""
        gone = self._gone.get(case_id)
        if gone is None:
            return False
        w = WRITER.get()
        return w is not None and w.id != gone[1] and w.start < gone[0]

    def _as_of(self, key: tuple, start: int) -> Any:
        """The record as it stood at change number `start`: its version then, ABSENT if it did not exist, or None
        when that is not known (nothing about it seen since this server started)."""
        hist = self._hist.get(key)
        if not hist:
            return None
        best = None
        for n, raw in hist:
            if n <= start:
                best = raw
        if best is not None:
            return best
        return None

    def _resolve(self, key: tuple, mine: dict, cur: Any) -> Any:
        """What a write puts in the store: mine, a merge of mine with the current record, or ABSENT for nothing."""
        w = WRITER.get()
        if w is None or not w.merges or key[1] in APPEND_ONLY:
            return mine
        base = w.seen.get(key)
        if base is None:
            base = self._as_of(key, w.start)
        last = self._ver.get(key)
        if cur is None:
            if base is not None and base is not ABSENT and last is not None and last[1] != w.id:
                return ABSENT                 # deleted by someone else since the job saw it: not written back
            return mine
        if base is cur or (last is not None and last[1] == w.id):
            return mine                       # unchanged since the job saw it, or last changed by the job itself
        if base is None:
            if last is None or last[0] <= w.start:
                return mine                   # not changed since the job began
            base = {}                         # changed since, from a state the job never saw: every field theirs
        merged, kept = merge3({} if base is ABSENT else base, mine, cur)
        w.merged += 1
        w.kept_theirs += len(kept)
        if kept:
            log.info("case %s: %s %s changed meanwhile; kept the newer %s", key[0], key[1], key[2], ", ".join(kept))
        return merged

    def _wrote(self, key: tuple, raw: Any) -> None:
        self.n += 1
        w = WRITER.get()
        self._ver[key] = (self.n, w.id if w is not None else None)
        if key[1] in APPEND_ONLY:
            return
        hist = self._hist.get(key)
        if hist is None:
            hist = self._hist[key] = deque(maxlen=HISTORY)
        hist.append((self.n, ABSENT if raw is None else raw))
        if w is not None and w.merges:
            w.seen[key] = ABSENT if raw is None else raw

    def _first_seen(self, key: tuple, cur: Any) -> None:
        """Before a record's first write here, what it was (so a job that began earlier finds its base)."""
        if key[1] not in APPEND_ONLY and key not in self._hist:
            self._hist[key] = deque([(0, ABSENT if cur is None else cur)], maxlen=HISTORY)

    async def put_case(self, case: CaseFile) -> None:
        async with holding_write(self.deps, case.id):
            if self._dropped(case.id):
                return
            key = (case.id, CASE, case.id)
            cur = await self.inner.raw_case(case.id)
            self._first_seen(key, cur)
            mine = case.model_dump(mode="json")
            out = self._resolve(key, mine, cur)
            if out is ABSENT:
                return
            self._gone.pop(case.id, None)     # made again (a sample reset), by a writer allowed to
            await self.inner.put_case(case if out is mine else CaseFile.model_validate(out))
            self._wrote(key, await self.inner.raw_case(case.id))

    async def put(self, case_id: str, collection: str, record: BaseModel) -> None:
        await self.put_many(case_id, collection, [record])

    async def put_many(self, case_id: str, collection: str, records: Records[Any]) -> None:
        if not records:
            return
        async with holding_write(self.deps, case_id):
            if self._dropped(case_id):
                return
            recs = await self.inner.raw_records(case_id, collection)
            model = COLLECTIONS[collection]
            out: Records[Any] = []
            for r in records:
                key = (case_id, collection, r.id)
                cur = recs.get(r.id)
                self._first_seen(key, cur)
                mine = r.model_dump(mode="json")
                res = self._resolve(key, mine, cur)
                if res is ABSENT:
                    continue
                out.append(r if res is mine else model.model_validate(res))
            if not out:
                return
            if len(out) == 1:
                await self.inner.put(case_id, collection, out[0])
            else:
                await self.inner.put_many(case_id, collection, out)
            recs = await self.inner.raw_records(case_id, collection)
            for r in out:
                self._wrote((case_id, collection, r.id), recs.get(r.id))

    async def delete(self, case_id: str, collection: str, record_id: str) -> None:
        await self.delete_many(case_id, collection, [record_id])

    async def delete_many(self, case_id: str, collection: str, record_ids: Records[str]) -> None:
        if not record_ids:
            return
        async with holding_write(self.deps, case_id):
            if self._dropped(case_id):
                return
            recs = await self.inner.raw_records(case_id, collection)
            w = WRITER.get()
            ids = []
            for rid in record_ids:
                key = (case_id, collection, rid)
                cur = recs.get(rid)
                if cur is None:
                    continue
                if w is not None and w.merges and collection not in APPEND_ONLY:
                    base = w.seen.get(key)
                    if base is None:
                        base = self._as_of(key, w.start)
                    last = self._ver.get(key)
                    if base is not cur and last is not None and last[1] != w.id and (
                            base is not None or last[0] > w.start):
                        log.info("case %s: %s %s changed meanwhile; not deleted", case_id, collection, rid)
                        continue
                self._first_seen(key, cur)
                ids.append(rid)
            if not ids:
                return
            if len(ids) == 1:
                await self.inner.delete(case_id, collection, ids[0])
            else:
                await self.inner.delete_many(case_id, collection, ids)
            for rid in ids:
                self._wrote((case_id, collection, rid), None)

    async def delete_case(self, case_id: str) -> None:
        async with holding_write(self.deps, case_id):
            await self.inner.delete_case(case_id)
            self.n += 1
            w = WRITER.get()
            self._gone[case_id] = (self.n, w.id if w is not None else None)
            for key in [k for k in self._ver if k[0] == case_id]:
                self._ver.pop(key, None)
            for key in [k for k in self._hist if k[0] == case_id]:
                self._hist.pop(key, None)

    async def list_cases(self) -> Records[CaseFile]:
        return await self.inner.list_cases()
