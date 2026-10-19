"""The tracker: every claim's clock from the day an institution had every paper to the day it must pay,
the letters drafted for the family, and the replies that came back."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ...modules.common import knowledge, snapshot
from ..schemas import ClockRow, DeadlineView, Escalation, LetterView, TrackerView
from .common import chip, cite_views, institution_name, money
from .localize import localize


def _day(d: date) -> str:
    return f"{d.day} {d.strftime('%b')}"      # "9 Oct" on every platform


def _status_text(claim: Any, clocks: list, day: date) -> tuple[str, bool]:
    late = next((c for c in clocks if c.status == "late" and c.owner == "institution"), None)
    if claim.status == "settled":
        amt = money(claim.settled_amount or claim.amount_due, claim.currency)
        on = _day(claim.settled_on) if claim.settled_on else ""
        return (f"Paid {amt.short if amt else ''} on {on}".replace("  ", " ").strip(), False)
    if late is not None:
        owed = money(late.compensation, claim.currency)
        return (f"{late.days_late} day{'s' if late.days_late != 1 else ''} late" + (f", {owed.short} owed" if owed and
                                                                                     late.compensation else ""), True)
    running = next((c for c in clocks if c.status == "running" and c.due_on), None)
    if running is not None:
        return (f"{running.short[:1].upper() + running.short[1:] if running.short else 'Due'} by "
                f"{_day(running.due_on)}", False)
    return ({"planning": "Being planned", "ready": "Papers ready to sign", "submitted": "Handed in",
             "acknowledged": "Every paper received", "returned": "Returned", "closed": "Nothing to claim"}
            .get(claim.status, claim.status), False)


# Institutions the Reserve Bank's ombudsman scheme covers (RB-IOS 2026, clause 1(3)): banks and non-bank lenders
_RBI_REGULATED = {"bank", "nbfc", "co_operative_bank"}
_RBI_OMBUDSMAN = ["in_rbi_ios_first_complain", "in_rbi_ios_30_days", "in_rbi_ios_90_days", "in_rbi_ios_online"]


async def _regulator_route(k: Any, claim: Any, institution: str) -> Escalation:
    """Where a regulator's ombudsman takes the complaint and its rules are stored with their source, the route with
    its time limits; elsewhere, a note that it is not suggested until it has been researched."""
    inst = await k.institution(claim.institution_id) if claim is not None and claim.institution_id else None
    if inst is not None and inst.country == "IN" and inst.kind in _RBI_REGULATED and \
            all([await k.assertion(a) for a in _RBI_OMBUDSMAN]):
        return Escalation(text=f"If {institution} has not replied within 30 days of a written complaint, or its reply "
                               "does not settle it, the complaint can go to the RBI Ombudsman within 90 days, online at "
                               "cms.rbi.org.in.", cites=list(_RBI_OMBUDSMAN), verified=True)
    return Escalation(text="Where a regulator or ombudsman takes complaints about this, its route and time limits are "
                           "researched and checked against their source before AfterLedger suggests it.", verified=False)


async def tracker(deps: Any, case_id: str, lang: str = "en") -> TrackerView:
    snap = await snapshot(deps, case_id)
    k = knowledge(deps)
    day = deps.today_for(snap.case)
    rows: list[ClockRow] = []
    starts, ends = [day - timedelta(days=14)], [day + timedelta(days=14)]
    for claim in snap.claims:
        if claim.status == "closed":
            continue
        inst = await k.institution(claim.institution_id) if claim.institution_id else None
        clocks = [c for c in claim.clocks if c.started_on or c.due_on]
        for c in clocks:
            if c.started_on:
                starts.append(c.started_on)
            if c.due_on:
                ends.append(c.due_on)
        if claim.settled_on:
            starts.append(claim.settled_on)
        text, late = _status_text(claim, clocks, day)
        if not clocks and claim.status not in ("settled",) and not claim.submitted_on:
            continue                      # nothing handed in yet: the plan shows it, not the tracker
        rows.append(ClockRow(claim_id=claim.id, title=claim.title, institution=institution_name(inst, claim.institution_id),
                             kind=claim.kind, status=claim.status, status_text=text, clocks=clocks, events=claim.events,
                             settled_on=claim.settled_on, settled=money(claim.settled_amount, claim.currency),
                             late=late))
    rows.sort(key=lambda r: (not r.late, r.status == "settled", r.title))
    letters, received = [], []
    ledger = {e.subject: e.result for e in await deps.cases.list(case_id, "ledger") if e.check == "fidelity"}
    cite_ids: set[str] = set()
    for corr in sorted(snap.correspondence, key=lambda c: (c.on, c.id), reverse=True):
        claim = snap.claim(corr.claim_id) if corr.claim_id else None
        if corr.direction == "out" and corr.status == "draft" and claim is not None and \
                claim.status in ("settled", "closed"):
            continue                      # drafted before the claim was settled: nothing to send any more
        inst = await k.institution(claim.institution_id) if claim and claim.institution_id else None
        raw = await k.institution_raw(claim.institution_id) if claim and claim.institution_id else None
        to_line = ""
        if inst is not None:
            to_line = "To " + ("the Branch Manager, " if inst.kind == "bank" else "") + inst.name + \
                      (f", {raw.get('branch')}" if raw and raw.get("branch") else "")
        kind = corr.kind or ("objection" if "objection" in corr.summary.lower() else "reminder")
        signer = snap.claimant(claim)
        view = LetterView(letter=corr, claim_title=claim.title if claim else "", institution=institution_name(inst),
                          to_line=to_line, signer=chip(signer),
                          fidelity=ledger.get(f"claim:{corr.claim_id}:letter:{kind}") if corr.direction == "out" else None)
        cite_ids |= set(corr.cites)
        (letters if corr.direction == "out" and corr.letter_text else received).append(view)
    expected = [DeadlineView(date=c.expected_on, title=c.expected_title or c.title, note=c.expected_note or "",
                             kind="expected", claim_id=c.claim_id)
                for c in snap.correspondence if c.expected_on and c.expected_on >= day]
    escalation: dict[str, list[Escalation]] = {}
    for r in rows:
        if r.late:
            escalation[r.claim_id] = [
                Escalation(text=f"If {r.institution} does not answer, write to its complaints or grievance officer. "
                                "AfterLedger drafts it with the same facts.", verified=True),
                await _regulator_route(k, snap.claim(r.claim_id), r.institution)]
            cite_ids |= {c for e in escalation[r.claim_id] for c in e.cites}
    for r in rows:
        for c in r.clocks:
            cite_ids |= set(c.cites)
    view = TrackerView(today=day, start=min(starts), end=max(ends), rows=rows, letters=letters, received=received,
                       expected=sorted(expected, key=lambda d: d.date), escalation=escalation,
                       cites=await cite_views(deps, cite_ids, day))
    # the words the server wrote, in the page's language; a letter's own text stays as it was written (its
    # family copy is the mirror beside it)
    await localize(deps, lang, [(r, f) for r in view.rows for f in ("status_text", "title")]
                   + [(lv, "claim_title") for lv in [*view.letters, *view.received]]
                   + [(lv.letter, "summary") for lv in view.letters]
                   + [(lv.letter, f) for lv in view.received for f in ("title", "summary")]
                   + [(d, f) for d in view.expected for f in ("title", "note")]
                   + [(e, "text") for steps in view.escalation.values() for e in steps])
    return view
