"""The institution's desk view: one claim, shared read-only with the institution, with every requirement
checked against the rule it comes from, so a claims officer sees a complete pack and the clock."""
from __future__ import annotations

import re
from typing import Any

from ...modules.common import knowledge, snapshot
from ..schemas import DeskRequirement, DeskView
from .common import cite_views, money
from .plans import compensation_views
from .today import _after_death


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]{4,}", text.lower())} - {"from", "with", "that", "this", "their", "have"}


def _para(cites: list[str], views: dict, about: str = "") -> str:
    """The paragraph a requirement comes from: of its sources, the one whose words best match it."""
    want = _words(about)
    best, score = "", -1
    for c in cites:
        v = views.get(c)
        if not (v and v.locator):
            continue
        sc = len(want & (_words(v.text) | _words(v.quote))) if want else 0
        if sc > score:
            best, score = v.locator, sc
    return best.replace("paras ", "").replace("para ", "")


async def desk(deps: Any, case_id: str, claim_id: str, until: Any = None) -> DeskView | None:
    snap = await snapshot(deps, case_id)
    claim = snap.claim(claim_id)
    if claim is None:
        return None
    k = knowledge(deps)
    inst = await k.institution(claim.institution_id) if claim.institution_id else None
    raw = await k.institution_raw(claim.institution_id) if claim.institution_id else None
    cp = snap.plan_for_claim(claim.id)
    pack = next((p for p in snap.packs if p.claim_id == claim.id), None)
    today = deps.today_for(snap.case)
    ids: set[str] = set()
    if cp:
        for d in cp.documents:
            ids |= set(d.cites)
        for s in cp.steps:
            ids |= set(s.cites)
        for c in cp.clocks:
            ids |= set(c.cites)
    views = await cite_views(deps, ids, today)
    received = claim.status in ("acknowledged", "settled") or bool(claim.complete_set_on)
    reqs: list[DeskRequirement] = []
    k_plan = await k.plan(cp.plan_key) if cp else None
    for d in (cp.documents if cp else []):
        spec = k_plan.document(d.id) if k_plan else None
        items = [it for it in (pack.items if pack else []) if spec and spec.form_key and it.form_key == spec.form_key]
        item = items[0] if items else None
        if item is not None:
            names = list(dict.fromkeys(snap.person(s).display_name for it in items for s in it.signers if snap.person(s)))
            in_pack = ("Signed by " + (" and ".join(names) if len(names) < 3 else ", ".join(names[:-1]) + " and " +
                                       names[-1])) if names else item.title
        elif d.status == "have":
            in_pack = "Among the family's papers"
        else:
            in_pack = d.note or "To come"
        status = "Received" if received else ("In the pack" if (item or d.status == "have") else "Missing")
        reqs.append(DeskRequirement(requirement=d.name, para=_para(d.cites, views, d.name), in_pack=in_pack,
                                    status=status, cites=d.cites))
    for m in (cp.must_not_be_asked if cp else []):
        key = m.split()[0].lower()
        cite = next((c for c, v in views.items() if key in v.text.lower() or key in v.quote.lower()), None)
        reqs.append(DeskRequirement(requirement=m, para=_para([cite], views, m) if cite else "",
                                    in_pack="Not required for this claim", status="Do not ask",
                                    cites=[cite] if cite else []))
    for clk in claim.clocks:
        if clk.owner != "institution" or not clk.started_on:
            continue
        late = clk.status == "late"
        reqs.append(DeskRequirement(
            requirement=clk.label, para=_para(clk.cites, views, clk.label),
            in_pack=f"Every document confirmed on {clk.started_on.day} {clk.started_on.strftime('%B')}",
            status=(f"{clk.days_late} day{'s' if clk.days_late != 1 else ''} overdue" if late else
                    "Met" if clk.status == "met" else f"Due {clk.due_on.day} {clk.due_on.strftime('%B')}" if clk.due_on else ""),
            late=late, cites=clk.cites))
    comp = await compensation_views(deps, claim, cp.plan_key if cp else None, today)
    for c in comp:
        if c.owed and c.clock.status == "late":
            reqs.append(DeskRequirement(
                requirement="Compensation for the delay", para=_para(c.clock.cites, views, "compensation interest delay"),
                in_pack=f"Bank Rate on {c.rate_on.day} {c.rate_on.strftime('%B')}, {c.base_rate_pct}%, plus "
                        f"{c.spread_pct}%: {c.per_day.text} a day" if c.rate_on and c.per_day else "",
                status=f"{c.owed.text} so far", late=True, cites=c.clock.cites))
    notes = []
    inst_names = {claim.institution_id: (inst.short_name or inst.name) if inst else ""}
    mine = {p.id for p in snap.papers if p.institution_id == claim.institution_id}
    snap_mine = type(snap)(case=snap.case, transactions=[t for t in snap.transactions if t.paper_id in mine],
                           holdings=snap.holdings, papers=snap.papers)
    notes += [u.text for u in _after_death(snap_mine, inst_names)]
    for v in snap.values:
        if v.status == "family_confirmed" and v.paper_id in mine:
            paper = snap.paper(v.paper_id)
            notes.append(f"{v.label}: read from {'a photo of ' if paper.origin == 'photo' else ''}the "
                         f"{paper.title} and confirmed by the family.")
    claimant = snap.claimant(claim)
    main = next((v for v in views.values() if v.tier == 1), None)
    route = ""
    if cp:
        route_step = next((s for s in cp.steps if s.status not in ("blocked",) and s.id in
                           {e.get("step_id") for e in cp.explained}), None)
        route = cp.summary or (route_step.text if route_step else "")
    due = ""
    late_clk = next((c for c in claim.clocks if c.owner == "institution" and c.due_on and c.status in ("late", "running")),
                    None)
    if late_clk:
        due = f"{late_clk.due_on.day} {late_clk.due_on.strftime('%B')}" + \
              (f", {late_clk.days_late} day{'s' if late_clk.days_late != 1 else ''} overdue" if late_clk.status == "late" else "")
    return DeskView(institution=inst, branch=(raw or {}).get("branch"), family_label=snap.case.family_label, until=until,
                    claim=claim, title=claim.title, deceased=snap.case.title,
                    claimant=claimant.display_name if claimant else None,
                    claimant_relationship=claimant.relationship if claimant else None,
                    amount=money(claim.amount_due, claim.currency), route=route, due=due, requirements=reqs,
                    compensation=comp, notes=notes,
                    checked_against=main.source_title if main else "", cites=views)
