"""Wiring: builds the adapters for the configured mode and hands them to the application."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .config import Settings, get_settings
from .domain.calibration import Calibrator
from .domain.case import Activity, LedgerEntry
from .domain.ids import new_id
from .ports import (
    BlobStore,
    CaseStore,
    Clock,
    EventBus,
    Grounding,
    KnowledgeStore,
    Ocr,
    Pages,
    Search,
    TaskQueue,
    Trace,
    Translator,
)

log = logging.getLogger(__name__)

STORY_NOW: ContextVar[datetime | None] = ContextVar("story_now", default=None)
"""While the seeder plays a family's timeline, 'now' is the moment in the story being played."""


class ActivityHub:
    """Fan-out of activity lines to live listeners (the app's activity feed over SSE)."""

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def listen(self, case_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._listeners[case_id].add(q)
        return q

    def forget(self, case_id: str, q: asyncio.Queue) -> None:
        listeners = self._listeners.get(case_id)
        if listeners is not None:
            listeners.discard(q)
            if not listeners:
                del self._listeners[case_id]

    def send(self, case_id: str, payload: dict) -> None:
        for q in list(self._listeners.get(case_id, ())):
            with contextlib.suppress(asyncio.QueueFull):   # a slow listener misses an update, never blocks
                q.put_nowait(payload)


@dataclass
class Deps:
    settings: Settings
    clock: Clock
    cases: CaseStore
    knowledge: KnowledgeStore
    blobs: BlobStore
    bus: EventBus
    tasks: TaskQueue
    ocr: Ocr
    translator: Translator
    grounding: Grounding
    search: Search
    pages: Pages
    trace: Trace
    calibrator: Calibrator
    hub: ActivityHub = field(default_factory=ActivityHub)
    models: Any = None          # afterledger.agents.factory.ModelProvider
    meter: Any = None           # afterledger.agents.meter.Meter: usage and cost of every model call
    screen: Any = None          # ports.Screen: outside text screened for planted instructions
    users: Any = None           # accounts (afterledger.accounts): local file, or Firestore with AL_STORE=gcp
    extras: dict[str, Any] = field(default_factory=dict)

    # ---- time ---------------------------------------------------------------------------------
    def now(self) -> datetime:
        return STORY_NOW.get() or self.clock.now()

    def today_for(self, case: Any = None) -> date:
        """A demo case lives on its story day; a new case lives in real time."""
        story = STORY_NOW.get()
        if story is not None:
            return story.date()
        if case is not None and getattr(case, "replay_on", None):
            return case.replay_on           # a story being replayed lives on the day it has reached
        if case is not None and getattr(case, "story_day", None):
            return case.story_day
        return self.clock.today()

    # ---- activity and the verification ledger ----------------------------------------------
    async def activity(self, case_id: str, module: str, text: str, *, kind: str = "step",
                       agent: str | None = None, run_id: str | None = None, **detail: Any) -> Activity:
        usage = self.meter.take_since_activity(case_id) if self.meter is not None else None
        if usage:                 # what the model calls since the case's previous line cost
            detail = {**detail, "usage": usage}
        item = Activity(id=new_id("act"), case_id=case_id, at=self.now(), module=module, agent=agent,
                        kind=kind, text=text, detail=detail, run_id=run_id)
        await self.cases.put(case_id, "activity", item)
        self.hub.send(case_id, {"type": "activity", "item": item.model_dump(mode="json")})
        return item

    async def ledger(self, case_id: str, subject: str, check: str, result: str, *, method: str = "",
                     detail: str = "", evidence: list[str] | None = None) -> None:
        entry = LedgerEntry(id=new_id("led"), case_id=case_id, at=self.now(), subject=subject,
                            check=check, method=method, result=result, detail=detail, evidence=evidence or [])
        await self.cases.put(case_id, "ledger", entry)

    def changed(self, case_id: str, *collections: str) -> None:
        """Tell open screens which parts of the case changed, so they refetch."""
        self.hub.send(case_id, {"type": "changed", "collections": list(collections),
                                "at": datetime.now(UTC).isoformat()})


def build_deps(settings: Settings | None = None) -> Deps:
    from .adapters.local.services import (
        FixturePages,
        FixtureSearch,
        GlossaryTranslator,
        InProcessBus,
        InProcessTaskQueue,
        LexicalGrounding,
        LocalOcr,
        NoScreen,
        PatternScreen,
    )
    from .adapters.local.stores import (
        JsonlTrace,
        LocalBlobStore,
        LocalCaseStore,
        LocalKnowledgeStore,
        make_clock,
    )

    s = settings or get_settings()
    s.state_dir.mkdir(parents=True, exist_ok=True)
    clock = make_clock(s.clock, s.timezone)
    pages = FixturePages(s.knowledge_dir)
    deps = Deps(
        settings=s,
        clock=clock,
        cases=LocalCaseStore(s.state_dir),
        knowledge=LocalKnowledgeStore(s.knowledge_dir, s.state_dir),
        blobs=LocalBlobStore(s.state_dir, s.data_dir),
        bus=InProcessBus(),
        tasks=InProcessTaskQueue(),
        ocr=LocalOcr(),
        translator=GlossaryTranslator(s.knowledge_dir),
        grounding=LexicalGrounding(),
        search=FixtureSearch(pages),
        pages=pages,
        trace=JsonlTrace(s.state_dir),
        calibrator=Calibrator.load(s.knowledge_dir / "calibration.json"),
    )
    from .accounts import LocalUserStore
    deps.users = LocalUserStore(s.state_dir)
    deps.extras["fixture_pages"] = pages
    deps.screen = NoScreen() if s.screen == "off" else PatternScreen()
    deps.extras["services"] = {"store": "local", "blobs": "local", "trace": "jsonl", "tasks": "in_process",
                               "ocr": "pdf_text_and_sidecars", "translation": "reviewed_table",
                               "grounding": "lexical", "pages": "snapshots", "search": "snapshots",
                               "screen": deps.screen.engine, "llm": s.llm}
    if s.llm == "gemini":
        deps.extras["services"]["models"] = ", ".join(dict.fromkeys([s.model_flash, s.model_pro, s.model_lite]))
    deps.extras["services"]["voice"] = "gemini_live" if s.live_voice else "browser_speech"
    from .health import health_of
    health_of(deps)
    if s.live or s.store == "gcp":
        from .adapters.gcp.wiring import apply_gcp_adapters
        apply_gcp_adapters(deps)
    if s.trace_to_cloud:
        _trace_to_cloud(s)
        deps.extras["services"]["spans"] = "cloud_trace"
    from .agents.factory import ModelProvider
    from .agents.meter import Meter
    deps.meter = Meter(deps)
    deps.models = ModelProvider(deps)
    return deps


_TRACING_ON = False


def _trace_to_cloud(s: Settings) -> None:
    """The agent kit's own spans (agents, model calls, tools) to Cloud Trace through the Telemetry API, once per
    process. The project goes on the resource, which the Telemetry API requires."""
    global _TRACING_ON
    if _TRACING_ON:
        return
    try:
        from google.adk.telemetry.google_cloud import get_gcp_exporters
        from google.adk.telemetry.setup import maybe_set_otel_providers
        from opentelemetry.sdk.resources import Resource
        hooks = get_gcp_exporters(enable_cloud_tracing=True)
        maybe_set_otel_providers([hooks], otel_resource=Resource.create(
            {"service.name": "afterledger-api", "gcp.project_id": s.project or ""}))
        _TRACING_ON = True
    except Exception:  # spans are a view on the work, never a condition for it
        log.exception("could not turn on Cloud Trace export")
