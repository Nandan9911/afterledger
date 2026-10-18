"""The live challenge: name any country, institution and situation, and watch the rules being researched.

A first answer comes from a quick check of one official page, so the family is not left waiting; the
full research then runs (two researchers, citation check, adjudication, compiled plan, code checks);
and a letter is written in the institution's own language, with placeholders the family fills in
and an English copy beside it. Every step is logged with its time, for the screen that shows it, and
can be watched while it runs. A finished challenge can be saved as a new case, which starts with the
question as the family's first words and the compiled plan as a procedure to follow.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..agents.runner import call_agent
from ..domain.countries import country_name
from ..domain.ids import new_id
from ..health import ServiceFailure
from .common import knowledge
from .research import ResearchRun, _tools, as_listener, research, run_view

MODULE = "research"
log = logging.getLogger(__name__)
COUNTRY_LANG = {"FR": "fr", "DE": "de", "JP": "ja", "EG": "ar", "AE": "ar", "SA": "ar", "ES": "es", "MX": "es",
                "IN": "en", "GB": "en", "US": "en", "SG": "en", "IT": "it", "BR": "pt", "PT": "pt", "KR": "ko",
                "CN": "zh", "ID": "id", "MY": "ms", "TH": "th", "VN": "vi", "PH": "en", "AU": "en", "NZ": "en"}
FINISHED = "finished"


def _running(deps: Any) -> dict[str, ResearchRun]:
    return deps.extras.setdefault("challenge_runs", {})


def running_run(deps: Any, run_id: str) -> ResearchRun | None:
    """A challenge still in memory (running, or finished in this process), for watching it live."""
    return _running(deps).get(run_id)


def new_run(deps: Any, *, country: str, question: str, lang: str | None = None, institution: str | None = None,
            situation: str | None = None, holding_kind: str | None = None, asked_by: str | None = None,
            title: str | None = None, translation: str | None = None, owner_id: str | None = None) -> ResearchRun:
    country = country.upper()
    need = {"country": country, "institution": institution, "institution_id": None, "holding_kind": holding_kind,
            "situation": situation or "", "question": question, "lang": lang or COUNTRY_LANG.get(country, "en"),
            "asked_by": asked_by, "title": title, "translation": translation}
    run = ResearchRun(id=new_id("ch"), need=need, model=deps.models.label, owner_id=owner_id)
    _running(deps)[run.id] = run
    return run


async def start_challenge(deps: Any, *, country: str, question: str, lang: str | None = None,
                          institution: str | None = None, situation: str | None = None,
                          holding_kind: str | None = None, asked_by: str | None = None, on_event: Any = None,
                          run: ResearchRun | None = None, title: str | None = None,
                          translation: str | None = None, owner_id: str | None = None) -> ResearchRun:
    run = run or new_run(deps, country=country, question=question, lang=lang, institution=institution,
                         situation=situation, holding_kind=holding_kind, asked_by=asked_by, title=title,
                         translation=translation, owner_id=owner_id)
    listener = as_listener(on_event)
    if listener:
        run.listeners.append(listener)
    stopped: ServiceFailure | None = None
    try:
        await _challenge(deps, run)
    except Exception as exc:  # the screen must always learn that the run ended
        log.exception("challenge %s failed", run.id)
        run.status = "error"
        run.log(FINISHED, "The research stopped with an error; nothing from it was stored.", status="error",
                error=str(exc)[:300])
        if isinstance(exc, ServiceFailure):
            stopped = exc                       # a strict live run stops here, after the screen has been told
    finally:
        run.listeners.clear()
    if stopped is not None:
        raise stopped
    await knowledge(deps).store.put("challenges", run.id, run_view(run))
    return run


async def _challenge(deps: Any, run: ResearchRun) -> None:
    need = run.need
    country, holding_kind = need["country"], need.get("holding_kind")
    run.log("asked", "Question received.", question=need["question"])
    # ---- 1. a first answer from one official page --------------------------------------------------
    web_search, fetch_page, _ = _tools(deps, run, "First check")
    quick = await call_agent(deps, "unknown_need_handler", {
        "key": f"first_answer/{country}/{holding_kind or 'any'}", "mode": "first_answer", "need": need,
        "question": need["question"]}, tools=[web_search, fetch_page])
    run.first_answer = {**quick.model_dump(), "seconds": run.events[-1]["at"] if run.events else 0}
    run.log("first_answer", "First answer, from a quick check of one official page." if quick.found_official else
            "No official page answers this yet; general guidance only, labelled as such.",
            found_official=quick.found_official)
    # ---- 2. the full research --------------------------------------------------------------------
    await research(deps, need, run=run)
    # ---- 3. a letter in the institution's language ---------------------------------------------
    letter = await call_agent(deps, "letter_writer", {
        "key": f"challenge_letter/{country}/{holding_kind or 'any'}", "challenge": need, "lang": need["lang"],
        "facts": {"institution": need.get("institution"), "plan": run.plan, "placeholders": True,
                  "first_answer": run.first_answer}})
    run.letter = letter.model_dump()
    run.log("letter", "A letter in the institution's language is ready, with an English copy beside it. The "
            "bracketed details are left for the family.")
    unverified = bool(run.plan and run.plan.get("unverified"))
    run.log(FINISHED, {"done": "Finished: nothing could be verified yet, so the plan starts by asking the institution "
                               "in writing. The research goes on when the family asks again." if unverified else
                               "Finished: the plan is stored, with its sources, for the next family who asks.",
                       "partial": "Finished: what is known so far is shown; the plan could not be compiled cleanly.",
                       "failed_gold": "Finished: the plan differs from the hand-checked gold plan in some cases, so "
                                      "it is shown for checking and was not stored."
                       }.get(run.status, "Finished."), status=run.status, unverified=unverified)


def launch(deps: Any, **kwargs: Any) -> ResearchRun:
    """Starts a challenge in the background and returns its run at once, for watching."""
    run = new_run(deps, **{k: v for k, v in kwargs.items() if k != "on_event"})
    task = asyncio.get_running_loop().create_task(start_challenge(deps, run=run, **kwargs))
    deps.extras.setdefault("challenge_tasks", set()).add(task)
    task.add_done_callback(deps.extras["challenge_tasks"].discard)
    return run


async def get_challenge(deps: Any, run_id: str) -> dict | None:
    live = running_run(deps, run_id)
    if live is not None:
        return run_view(live)
    return await knowledge(deps).store.get("challenges", run_id)


async def clear_challenges(deps: Any) -> int:
    """Forgets every question asked on this copy (what research learned about the rules stays, with its sources)."""
    store = knowledge(deps).store
    gone = 0
    for c in await store.list("challenges"):
        if c.get("id") and await store.delete("challenges", c["id"]):
            gone += 1
    return gone


async def list_challenges(deps: Any) -> list[dict]:
    items = await knowledge(deps).store.list("challenges")
    out = [{"id": c["id"], "country": c["need"]["country"], "question": c["need"]["question"],
            "lang": c["need"].get("lang"), "status": c["status"], "started_at": c.get("started_at"),
            "stored_key": c.get("stored_key")} for c in items]
    return sorted(out, key=lambda c: c.get("started_at") or "", reverse=True)


async def save_as_case(deps: Any, run_id: str, *, by_name: str | None = None, owner_id: str | None = None) -> str | None:
    """A new case that starts with the question as the family's first words, the country, and the
    compiled plan as a procedure to follow."""
    from . import intake, planning
    view = await get_challenge(deps, run_id)
    if view is None:
        return None
    need = view["need"]
    plan = view.get("plan") or {}
    case, _ = await intake.start_case(deps, lang=need.get("lang") or "en")
    case.challenge_id = run_id
    case.owner_id = owner_id
    case.synthetic = False
    case.country_codes = [need["country"]]
    # named after the question until the family says who died (intake then uses the person's name)
    case.title = need.get("title") or (plan.get("title") if plan and not plan.get("unverified") else None) or \
        f"A question about {country_name(need['country'])}"
    await deps.cases.put_case(case)
    await intake.family_turn(deps, case.id, need["question"], lang=need.get("lang"),
                             translation=need.get("translation"))
    if view.get("stored_key"):
        await planning.start_procedure(deps, case.id, view["stored_key"], by=None)
    await deps.activity(case.id, MODULE, "Saved from a live challenge: the research, its sources and the letter "
                        "came with it.", kind="step", challenge_id=run_id)
    return case.id
