"""The case header every case screen shares: who died, who is viewing, and the counts in the navigation."""
from __future__ import annotations

from typing import Any

from ...domain.case import CaseFile
from ...modules.common import Snapshot, snapshot
from ..schemas import CaseHeader, CaseSummary, NavCounts
from .common import chip


def case_cites(snap: Snapshot) -> dict[str, set[str]]:
    """Every assertion the case relies on, with the claims that rely on it."""
    used: dict[str, set[str]] = {}

    def add(ids: list[str] | None, who: str) -> None:
        for i in ids or []:
            used.setdefault(i, set()).add(who)

    for cp in snap.plans:
        claim = snap.claim(cp.claim_id)
        if claim is not None and claim.status == "closed" and claim.kind == "procedure":
            continue
        who = claim.title if claim else cp.title
        for s in cp.steps:
            add(s.cites, who)
        for d in cp.documents:
            add(d.cites, who)
        for c in cp.clocks:
            add(c.cites, who)
        for e in cp.explained:
            add(e.get("cites"), who)
    for lead in snap.leads:
        add(lead.cites, lead.title)
    for corr in snap.correspondence:
        add(corr.cites, corr.title)
    if snap.case.jurisdiction:
        add(snap.case.jurisdiction.cites, "Whose rules apply")
        for leg in snap.case.jurisdiction.legs:
            add(leg.cites, "Whose rules apply")
    for t in snap.tasks:
        add(t.cites, t.title)
    return used


def nav_counts(snap: Snapshot, activity: int = 0) -> NavCounts:
    open_cp = [c for c in snap.checkpoints if c.status == "open"]
    late = sum(1 for c in snap.claims if c.status not in ("settled", "closed")
               and any(k.status == "late" and k.owner == "institution" for k in c.clocks))
    return NavCounts(
        papers=len(snap.papers), papers_to_check=sum(p.values_to_check for p in snap.papers),
        money=sum(1 for h in snap.holdings if h.status != "closed"),
        leads_open=sum(1 for x in snap.leads if x.status == "open"),
        people=len(snap.family()),
        people_questions=sum(1 for c in open_cp if c.code == "C3"),
        plans=sum(1 for c in snap.claims if c.plan_id and c.status != "closed"),
        plan_questions=sum(1 for c in open_cp if c.code == "C5" and c.kind == "plan_question"),
        packs=len(snap.packs), packs_ready=sum(1 for p in snap.packs if p.status == "ready"),
        tracker_late=late, sources=len(case_cites(snap)), activity=activity)


async def header(deps: Any, case_id: str, viewer_id: str | None = None) -> CaseHeader:
    snap = await snapshot(deps, case_id)
    activity = len(await deps.cases.list(case_id, "activity"))
    viewer = snap.person(viewer_id) or snap.person(snap.case.viewer_person_id)
    places = []
    for p in snap.family():
        if p.city and p.city not in places:
            places.append(p.city)
    work = (getattr(deps, "extras", None) or {}).get("case_work", {}).get(snap.case.id) or {}
    researching = [{"claim_id": c.id, "title": c.title, "run_id": c.research.get("run_id"),
                    "started": c.research.get("started")}
                   for c in snap.claims if (c.research or {}).get("status") == "running" and c.status != "closed"]
    return CaseHeader(case=snap.case, deceased=snap.deceased, viewer=chip(viewer),
                      family=[c for c in (chip(p) for p in snap.family()) if c], family_places=places,
                      today=deps.today_for(snap.case), nav=nav_counts(snap, activity),
                      mode=snap.case.model_mode or deps.models.label,   # what prepared this case's content
                      working=work.get("step"), working_since=work.get("since"), researching=researching)


def summary(case: CaseFile) -> CaseSummary:
    return CaseSummary(id=case.id, title=case.title, title_en=case.title_en, family_label=case.family_label,
                       demo_key=case.demo_key, died_on=case.died_on, died_in=case.died_in,
                       country_codes=case.country_codes, languages=case.languages, stage=case.stage,
                       story_day=case.story_day, synthetic=case.synthetic,
                       updated_at=case.updated_at.isoformat() if case.updated_at else None)
