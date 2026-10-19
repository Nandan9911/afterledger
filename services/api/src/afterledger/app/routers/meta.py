"""Health, what mode the service is in, and the demo families."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from ... import __version__
from ...health import health_of
from ...runtime import Deps
from ...seed.families import family_keys
from .. import demo
from ..schemas import CalibrationInfo, DemoFamily, HealthInfo, Meta, ServiceProblem, SlowRoute
from ..views.localize import localize, page_language
from .common import get_deps, queue_events

router = APIRouter(prefix="/v1", tags=["meta"])

UI_LANGUAGES = ["en", "hi", "ja", "es", "ml", "ar"]


@router.get("/health")
async def health() -> dict:
    return {"ok": True, "version": __version__}


@router.get("/meta", response_model=Meta)
async def meta(deps: Deps = Depends(get_deps)) -> Meta:
    s = deps.settings
    from ...modules.common import knowledge
    countries = {p.jurisdiction.split("-")[0] for p in await knowledge(deps).plans() if not p.unverified}
    return Meta(version=__version__, mode=s.mode, llm=deps.models.label, store=s.store, story_day=s.story_day,
                today=deps.clock.today(), languages=UI_LANGUAGES,
                live_gateway="/v1/cases/{case_id}/voice" if s.live_voice else None,
                countries_researched=len(countries - {"EU"}), services=dict(deps.extras.get("services", {})),
                calibration=_calibration(deps), health=_health(deps))


def _health(deps: Deps) -> HealthInfo:
    snap = health_of(deps).snapshot()
    return HealthInfo(strict=snap["strict"], ok=snap["ok"], failures={
        name: ServiceProblem(count=f["count"], at=f["last"]["at"], error=f["last"]["error"])
        for name, f in snap["failures"].items()}, slow={
        name: SlowRoute(count=f["count"], at=f["last"]["at"], error=f["last"]["error"], passed_over=f["passed_over"])
        for name, f in snap.get("slow", {}).items()})


def _calibration(deps: Deps) -> CalibrationInfo:
    tables = deps.calibrator.tables
    fitted = tables.get("_fitted") or {}
    return CalibrationInfo(status=str(tables.get("_status") or "default"), fitted_on=fitted.get("on"),
                           live_cases=list(fitted.get("live_cases") or []), items=dict(fitted.get("items") or {}))


@router.get("/demo/families", response_model=list[DemoFamily])
async def demo_families(request: Request, deps: Deps = Depends(get_deps)) -> list[DemoFamily]:
    out = await demo.families(deps)
    await localize(deps, page_language(request), [(f, x) for f in out for x in ("family_label", "where", "spoken", "shows")])
    return out


@router.get("/demo/status")
async def demo_status(deps: Deps = Depends(get_deps)) -> dict:
    return demo.status(deps)


@router.post("/demo/{key}/reset")
async def demo_reset(key: str, request: Request, deps: Deps = Depends(get_deps)) -> dict:
    """Puts a sample family back as the image built it. Where sign-in is required only the admin may: the samples
    are shared by every visitor, and may hold a live run's work."""
    if key not in family_keys():
        raise HTTPException(404, f"No demo family {key}")
    if deps.settings.auth_required:
        from .auth import signed_in
        user = await signed_in(deps, request)
        if user is None or user.role != "admin":
            raise HTTPException(403, "Only the admin can reset a sample case.")
    return await demo.reset(deps, key)


@router.get("/demo/{key}/watch")
async def demo_watch(key: str, request: Request, delay: float = Query(1.5, ge=0, le=10),
                     deps: Deps = Depends(get_deps)) -> EventSourceResponse:
    """Replays the family's story from the first paper, with a pause after each event, as server-sent events."""
    if key not in family_keys() or deps.settings.product:
        raise HTTPException(404, f"No demo family {key}")
    queue: asyncio.Queue = asyncio.Queue()
    demo.follow(deps, key, queue, delay=delay)

    async def stream() -> AsyncIterator[dict]:
        try:
            async for item in queue_events(request, queue, until=("done", "error"), seconds=None):
                yield item
        finally:
            demo.listeners(deps, key).discard(queue)

    return EventSourceResponse(stream())
