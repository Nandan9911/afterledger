"""Researching the rules a claim needs, when no stored plan covers its situation.

A real family's papers and words lead to claims no plan was written for in advance: a provident fund, a loan or a
card after the borrower dies, an insurer abroad. Such a claim starts at once on an interim plan (tell the
institution in writing and ask what it holds and how to claim), and the research agents work out the actual rules
there and then, in the background: two researchers on official sources, the citation check, the adjudicator, the
compiler and the code checks, the same research the live challenge shows, and the family can watch it. When it
finishes, every claim waiting on that situation is planned again on the researched plan, which is stored for every
family after this one.

Research is bounded so that one case cannot crowd out the rest of the service:
- RESEARCH_AT_ONCE runs at a time on the server, shared with the live challenge; the next waits its turn, saying so;
- one run per situation at a time: a second claim (in this case or another) waits for the run already going;
- a cost cap per run (AL_RESEARCH_BUDGET_USD) and a depth set for claims (AL_CLAIM_RESEARCH, standard by default);
- a situation research could not settle is not researched again for RETRY_DAYS days, and one that settled it is
  stored, so the next family's claim is planned at once.

The question research is given says nothing about the family: only the country, the kind of holding and, for a
procedure, its name.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .common import knowledge

log = logging.getLogger(__name__)
MODULE = "research"

RETRY_DAYS = 7
INTERIM_CLAIM = "XX/interim_claim"
INTERIM_DEBT = "XX/interim_debt"
INTERIM_KEYS = (INTERIM_CLAIM, INTERIM_DEBT)

KIND_WORDS = {
    "savings": "money in a savings account", "current": "money in a current account",
    "fixed_deposit": "a fixed deposit", "recurring_deposit": "a recurring deposit", "nre": "an NRE account",
    "nro": "an NRO account", "demat": "shares in a demat account", "mutual_fund": "mutual fund units",
    "shares_physical": "shares held as paper certificates", "life_policy": "a life insurance policy",
    "government_cover": "a government life cover scheme", "accident_cover": "an accident cover",
    "pension": "a pension", "family_pension": "a family pension", "provident_fund": "a provident fund (such as EPF)",
    "cpf": "CPF savings", "locker": "a bank locker", "premium_bonds": "premium bonds",
    "social_security": "social security benefits", "employer_benefit": "dues from an employer (final pay, gratuity, "
    "group insurance)", "salary": "salary owed by an employer", "chit_fund": "a chit fund membership",
    "gold": "gold held with an institution", "property": "property", "loan": "a loan",
    "card_loan": "a card loan", "credit_card": "a credit card", "other": "money held by an institution"}
DEBT_KINDS = {"loan", "card_loan", "credit_card"}


def is_interim(key: str | None) -> bool:
    return key in INTERIM_KEYS


def signature(country: str | None, kind: str | None) -> str:
    return f"{(country or 'XX').split('-')[0].upper()}/{kind or 'other'}"


def _question(country: str, kind: str) -> str:
    from ..domain.countries import country_name
    where = country_name(country)
    what = KIND_WORDS.get(kind, kind.replace("_", " "))
    if kind in DEBT_KINDS:
        return (f"In {where}, when someone with {what} dies: what the family must do about it, what the lender may "
                "ask of the heirs and what they are not liable for, whether insurance can settle it, which papers "
                "the lender needs, and any time limits.")
    return (f"In {where}, how does a family claim {what} after the holder dies: who may claim (nominee, heirs), "
            "which forms and papers the institution needs and how they are certified, how long the institution has "
            "to settle, and any time limits for the family.")


def _inflight(deps: Any) -> dict[str, dict]:
    return deps.extras.setdefault("rules_research", {})


def _starting(deps: Any) -> asyncio.Lock:
    """Held while a claim's research is looked up or started, so claims planned side by side never start one
    situation's research twice."""
    lock = deps.extras.get("rules_research_starting")
    if lock is None:
        lock = deps.extras["rules_research_starting"] = asyncio.Lock()
    return lock


async def ensure(deps: Any, case_id: str, claim: Any, *, country: str, kind: str | None, reason: str = "no_plan",
                 plan_key: str | None = None, authority: str | None = None) -> dict:
    """Starts research for the claim's situation, or joins the run already going; returns the claim's research state.
    A situation research settled, or failed to settle within RETRY_DAYS, is not researched again."""
    sig = plan_key if plan_key and authority else signature(country, kind)
    mode = getattr(deps.settings, "claim_research", "standard")
    if mode == "off":
        return {"signature": sig, "status": "off"}
    async with _starting(deps):
        return await _ensure(deps, case_id, claim, sig=sig, mode=mode, kind=kind, reason=reason, plan_key=plan_key,
                             authority=authority)


async def _ensure(deps: Any, case_id: str, claim: Any, *, sig: str, mode: str, kind: str | None, reason: str,
                  plan_key: str | None, authority: str | None) -> dict:
    case = await deps.cases.get_case(case_id)
    if case is not None and case.demo_key:
        # a sample is a scripted story: its claims stay on the interim plan rather than start research (research
        # run while the samples are built is scripted too, and its plans would exist only where they were built)
        return {"signature": sig, "status": "off", "reason": "sample"}
    entry = _inflight(deps).get(sig)
    if entry is not None:
        entry["claims"].setdefault(case_id, set()).add(claim.id)
        run = entry["run"]
        return {"signature": sig, "run_id": run.id, "status": "running", "started": run.started_iso, "reason": reason,
                "authority": authority}
    last = await knowledge(deps).store.get("research_attempts", sig)
    if last and last.get("at"):
        try:
            when = datetime.fromisoformat(last["at"])
        except ValueError:
            when = None
        if when is not None and datetime.now(UTC) - when < timedelta(days=RETRY_DAYS):
            return {**last, "signature": sig}     # researched lately, settled or not: not again yet
    from ..domain.countries import country_name
    from . import challenge
    country_code = sig.split("/")[0]
    lang = challenge.COUNTRY_LANG.get(country_code, "en")
    if authority:
        proc = await knowledge(deps).plan(plan_key or "")
        question = (f"In {country_name(country_code)}: {(proc.title if proc else plan_key or '').rstrip('.')}. How "
                    "does a family apply, which papers are needed, and how long does it take?")
    else:
        question = _question(country_code, kind or "other")
    run = challenge.new_run(deps, country=country_code, question=question, lang=lang, holding_kind=kind,
                            owner_id=case.owner_id if case else None)
    run.need.update(key=sig, authority=authority, for_claims=True, situation="")
    run.shared = True                    # rules, not the family's affairs: anyone with the link may watch
    entry = {"run": run, "claims": {case_id: {claim.id}}, "reason": reason}
    _inflight(deps)[sig] = entry
    depth = {"quick": "quick", "standard": "standard", "deep": "deep"}.get(mode, "standard")
    task = asyncio.get_running_loop().create_task(_run(deps, sig, run, depth))
    deps.extras.setdefault("rules_research_tasks", set()).add(task)
    task.add_done_callback(deps.extras["rules_research_tasks"].discard)
    return {"signature": sig, "run_id": run.id, "status": "running", "started": run.started_iso, "reason": reason,
            "authority": authority}


async def _run(deps: Any, sig: str, run: Any, depth: str) -> None:
    from ..concurrency import WRITER
    from . import challenge
    from .research import research, run_view
    WRITER.set(None)                      # a run of its own, not part of the job or action that started it
    gate = challenge._research_gate(deps)
    status = "failed"
    try:
        if gate.locked():
            run.log("waiting", f"{challenge.RESEARCH_AT_ONCE} other questions are being researched; this one starts as "
                    "soon as one of them finishes.")
        async with gate:
            run.log("asked", "Researching the rules a family's claim needs: no stored plan covers this yet.",
                    question=run.need["question"])
            await research(deps, run.need, run=run, depth=depth)
        status = "done" if run.stored_key and run.status == "done" else \
            "done" if run.stored_key else "inconclusive"
        unverified = bool(run.plan and run.plan.get("unverified"))
        run.log(challenge.FINISHED, "Finished: the plan is stored, with its sources, and the claims waiting for it are "
                "planned again." if status == "done" and not unverified else
                "Finished: no official source settled the rules, so the plan keeps asking the institution in writing."
                if status == "done" else
                "Finished: the rules could not be compiled cleanly; the plan keeps asking the institution in writing.",
                status=run.status, unverified=unverified)
    except Exception as exc:  # research that fails leaves the interim plan in place, and says so
        log.exception("research for %s failed", sig)
        run.status = "error"
        run.log(challenge.FINISHED, "The research stopped with an error; the plan keeps asking the institution in "
                "writing.", status="error", error=str(exc)[:300])
    finally:
        run.listeners.clear()
        k = knowledge(deps)
        try:
            await k.store.put("challenges", run.id, {**run_view(run), "shared": True})
            await k.store.put("research_attempts", sig, {"id": sig, "status": status, "run_id": run.id,
                                                         "plan_key": run.stored_key,
                                                         "at": datetime.now(UTC).isoformat(timespec="seconds")})
        except Exception:
            log.exception("could not store research %s", run.id)
        entry = _inflight(deps).pop(sig, {"claims": {}})
        held = challenge._running(deps)
        asyncio.get_running_loop().call_later(challenge.KEEP_FINISHED_S,
                                              lambda: held.pop(run.id, None) if held.get(run.id) is run else None)
        for case_id, claim_ids in entry["claims"].items():
            try:
                await _replan(deps, case_id, claim_ids, sig, run, status)
            except Exception:
                log.exception("could not plan %s again after research %s", case_id, run.id)


async def _replan(deps: Any, case_id: str, claim_ids: set[str], sig: str, run: Any, status: str) -> None:
    """The claims that waited for this research are planned again, on the researched plan if one was stored."""
    from ..concurrency import job
    from .planning import plan_claim, sync_procedures
    async with job(deps, case_id):
        if await deps.cases.get_case(case_id) is None:
            return
        titles = []
        for cid in sorted(claim_ids):
            claim = await deps.cases.get(case_id, "claims", cid)
            if claim is None:
                continue
            claim.research = {**claim.research, "signature": sig, "run_id": run.id, "status": status,
                              "finished": datetime.now(UTC).isoformat(timespec="seconds"), "plan_key": run.stored_key}
            await deps.cases.put(case_id, "claims", claim)
            await plan_claim(deps, case_id, cid)
            titles.append(claim.title)
        await sync_procedures(deps, case_id)
        if titles:
            plan = await knowledge(deps).plan(run.stored_key) if run.stored_key else None
            if plan is not None and not plan.unverified:
                label = "two researchers agree" if plan.level == "dual_verified" else "one official source" \
                    if plan.level == "sourced" else plan.level.replace("_", " ")
                text = (f"The rules for {', '.join(titles)} are researched ({label}); the plan now follows them, and "
                        "what was already done stands.")
            else:
                text = (f"Research could not settle the rules for {', '.join(titles)} from official sources, so the "
                        "plan keeps asking the institution in writing.")
            await deps.activity(case_id, MODULE, text, kind="research", run_id=run.id)
        deps.changed(case_id, "claims", "plans", "checkpoints")
    try:
        pending = deps.extras.setdefault("today_pending", set())
        if case_id not in pending:
            pending.add(case_id)
            await deps.tasks.enqueue("plan_today", {"case_id": case_id})
    except KeyError:                      # no task runner here (a command-line run): Today is planned by its caller
        deps.extras.setdefault("today_pending", set()).discard(case_id)
    except Exception:
        deps.extras.setdefault("today_pending", set()).discard(case_id)
        log.exception("could not plan today for %s", case_id)


async def resume_interrupted(deps: Any, case_id: str) -> int:
    """Research a restart cut short (the claim says running, but nothing runs here) starts again. Returns how many."""
    n = 0
    for claim in await deps.cases.list(case_id, "claims"):
        r = claim.research or {}
        if r.get("status") != "running" or r.get("signature") in _inflight(deps):
            continue
        holding = await deps.cases.get(case_id, "holdings", claim.holding_ids[0]) if claim.holding_ids else None
        country, _, kind = str(r.get("signature", "")).partition("/")
        claim.research = await ensure(deps, case_id, claim, country=country or claim.country or "XX",
                                      kind=(holding.kind if holding else kind) or None,
                                      reason="resumed", plan_key=claim.plan_key,
                                      authority=r.get("authority"))
        await deps.cases.put(case_id, "claims", claim)
        n += 1
    return n
