"""The live challenge: name any country and question, and watch the rules being researched."""
from __future__ import annotations

import asyncio
import copy
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from ...accounts import User
from ...domain.countries import country_name
from ...modules import challenge
from ...runtime import Deps
from ..schemas import ChallengeIn, ChallengePreset, ChallengeRow, ChallengeView
from ..views.common import cite_views
from ..views.localize import localize, page_language
from .auth import may_see_question, optional_user
from .common import get_deps, sse

router = APIRouter(prefix="/v1/challenges", tags=["challenge"])


def presets(deps: Deps) -> list[ChallengePreset]:
    f = deps.settings.knowledge_dir / "challenge_presets.yaml"
    raw = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("presets", []) if f.exists() else []
    return [ChallengePreset(country_name=country_name(p["country"]), **{k: v for k, v in p.items()
                                                                         if k in ChallengePreset.model_fields})
            for p in raw]


async def _view(deps: Deps, raw: dict[str, Any], lang: str = "en") -> ChallengeView:
    ids = {k["id"] for k in raw.get("kept", [])}
    for s in (raw.get("plan") or {}).get("steps", []):
        ids |= set(s.get("cites", []))
    if lang != "en":
        raw = copy.deepcopy(raw)          # the run may still be live: translate a copy, never the run itself
    view = ChallengeView(**{k: v for k, v in raw.items() if k in ChallengeView.model_fields})
    view.finished = any(e.get("stage") == challenge.FINISHED for e in view.events)
    if view.finished:
        view.cites = await cite_views(deps, ids, deps.clock.today())
    plan = view.plan or {}
    await localize(deps, lang, [(e, "text") for e in view.events]
                   + [(plan, "title"), (plan, "notes")] + [(x, "text") for x in plan.get("steps", [])]
                   + [(x, "name") for x in plan.get("documents", [])]
                   + [(view.researcher_a, "gaps"), (view.researcher_b, "gaps"), (view.adjudication, "summary"),
                      (view.agreement, "line")])
    return view


async def _localized(deps: Deps, ev: dict[str, Any], lang: str) -> dict[str, Any]:
    if lang == "en" or not ev.get("text"):
        return ev
    out = dict(ev)
    await localize(deps, lang, [(out, "text")])
    return out


@router.get("/presets", response_model=list[ChallengePreset])
async def list_presets(deps: Deps = Depends(get_deps)) -> list[ChallengePreset]:
    return presets(deps)


@router.get("", response_model=list[ChallengeRow])
async def list_challenges(user: User | None = Depends(optional_user),
                          deps: Deps = Depends(get_deps)) -> list[ChallengeRow]:
    if deps.settings.product:              # on the public site nobody sees the questions others asked
        return []
    rows = await challenge.list_challenges(deps)
    if deps.settings.auth_required:
        mine = {c["id"] for c in await challenge.knowledge(deps).store.list("challenges")
                if may_see_question(deps, c, user)}
        rows = [r for r in rows if r["id"] in mine]
    return [ChallengeRow(**c) for c in rows]


@router.post("", response_model=ChallengeView)
async def start(body: ChallengeIn, user: User | None = Depends(optional_user),
                deps: Deps = Depends(get_deps)) -> ChallengeView:
    if deps.settings.auth_required and user is None:
        raise HTTPException(401, "Please sign in first.")
    run = challenge.launch(deps, country=body.country, question=body.question, lang=body.lang,
                           institution=body.institution, situation=body.situation, holding_kind=body.holding_kind,
                           asked_by=body.asked_by, title=body.title, translation=body.translation,
                           owner_id=user.id if user else None)
    return await _view(deps, challenge.run_view(run))


@router.get("/{run_id}", response_model=ChallengeView)
async def get(run_id: str, request: Request, user: User | None = Depends(optional_user),
              deps: Deps = Depends(get_deps)) -> ChallengeView:
    raw = await challenge.get_challenge(deps, run_id)
    if raw is None or not may_see_question(deps, raw, user):
        raise HTTPException(404, f"No challenge {run_id}")
    return await _view(deps, raw, page_language(request))


@router.get("/{run_id}/events")
async def events(run_id: str, request: Request, pace: float = Query(0.0, ge=0, le=5),
                 lang: str = Query("en", max_length=8), user: User | None = Depends(optional_user),
                 deps: Deps = Depends(get_deps)) -> EventSourceResponse:
    """Every step of the run as it happens. Steps already done are replayed first, `pace` seconds apart, and
    each step's words come in the page's language (`lang`, since an event stream cannot set headers)."""
    lang = lang.split("-")[0].lower() or "en"
    run = challenge.running_run(deps, run_id)
    stored = None if run is not None else await challenge.get_challenge(deps, run_id)
    if run is None and stored is None:
        raise HTTPException(404, f"No challenge {run_id}")
    if not may_see_question(deps, {"owner_id": run.owner_id} if run is not None else stored, user):
        raise HTTPException(404, f"No challenge {run_id}")
    queue: asyncio.Queue = asyncio.Queue()
    if run is not None:
        run.listeners.append(queue.put_nowait)
        done = list(run.events)
    else:
        done = list(stored.get("events", []))

    async def stream():
        sent = 0
        try:
            for ev in done:
                if await request.is_disconnected():
                    return
                sent = max(sent, ev.get("n", 0))
                yield sse(await _localized(deps, ev, lang), "step")
                if ev.get("stage") == challenge.FINISHED:
                    return
                if pace:
                    await asyncio.sleep(pace)
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    if run is None or any(e.get("stage") == challenge.FINISHED for e in run.events[sent:]):
                        return
                    yield {"event": "ping", "data": "{}"}
                    continue
                if ev.get("n", 0) <= sent:
                    continue
                sent = ev.get("n", 0)
                yield sse(await _localized(deps, ev, lang), "step")
                if ev.get("stage") == challenge.FINISHED:
                    return
                if pace:
                    await asyncio.sleep(pace)
        finally:
            if run is not None and queue.put_nowait in run.listeners:
                run.listeners.remove(queue.put_nowait)

    return EventSourceResponse(stream())


@router.post("/{run_id}/case")
async def save_as_case(run_id: str, user: User | None = Depends(optional_user), deps: Deps = Depends(get_deps)) -> dict:
    if deps.settings.auth_required and user is None:
        raise HTTPException(401, "Please sign in first.")
    if not may_see_question(deps, await challenge.get_challenge(deps, run_id), user):
        raise HTTPException(404, f"No challenge {run_id}")
    case_id = await challenge.save_as_case(deps, run_id, owner_id=user.id if user else None)
    if case_id is None:
        raise HTTPException(404, f"No challenge {run_id}")
    return {"case_id": case_id}
