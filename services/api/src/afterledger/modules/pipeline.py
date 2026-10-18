"""How the modules follow each other. A new paper flows through reading, people, discovery, claims
and plans; answers and approvals flow back into planning. Every step is idempotent, so a module
can be re-run after any change without duplicating anything."""
from __future__ import annotations

import logging
from typing import Any

from . import discovery, jurisdiction, people, planning, reading
from .common import snapshot

log = logging.getLogger(__name__)


async def after_papers(deps: Any, case_id: str, paper_ids: list[str]) -> None:
    for pid in paper_ids:
        await _read(deps, case_id, pid)
    await people.resolve_institutions(deps, case_id)
    await people.resolve_people(deps, case_id)
    await discovery.discover(deps, case_id)
    await people.payees(deps, case_id)
    await refresh_claims(deps, case_id)
    await jurisdiction.map_jurisdiction(deps, case_id)
    case = await deps.cases.get_case(case_id)
    if case.stage in ("intake", "papers"):
        case.stage = "working"
        await deps.cases.put_case(case)


async def _read(deps: Any, case_id: str, paper_id: str) -> None:
    """Reads one paper, once more after a passing failure (a dropped connection, a model that timed out). A paper
    that still cannot be read says so on its card instead of reading for ever, and the other papers carry on."""
    for attempt in (1, 2):
        try:
            await reading.read_paper(deps, case_id, paper_id)
            return
        except Exception as e:
            passing = type(e).__name__ in ("ModelTimeout", "ReadError", "TransportError", "ConnectError",
                                           "RemoteProtocolError", "ServerError", "TimeoutError")
            if passing and attempt == 1:
                log.warning("reading %s/%s failed (%s); trying once more", case_id, paper_id, e)
                continue
            log.exception("reading %s/%s failed", case_id, paper_id)
            paper = await deps.cases.get(case_id, "papers", paper_id)
            if paper is not None:
                paper.status, paper.stage = "failed", "Could not be read. Try a clearer copy, or the pages that matter."
                await deps.cases.put(case_id, "papers", paper)
            await deps.activity(case_id, "reading", f"“{paper.title if paper else paper_id}” could not be read "
                                f"({type(e).__name__}). The other papers carry on.", kind="warning", paper_id=paper_id)
            deps.changed(case_id, "papers")
            return


async def refresh_claims(deps: Any, case_id: str) -> None:
    claims = await planning.build_claims(deps, case_id)
    for c in claims:
        await planning.plan_claim(deps, case_id, c.id)
    seen: set[str] = set()
    for _ in range(3):                    # a procedure's own plan can need another procedure
        procs = [p for p in await planning.ensure_procedures(deps, case_id) if p.id not in seen]
        if not procs:
            break
        for p in procs:
            seen.add(p.id)
            await planning.plan_claim(deps, case_id, p.id)
    await close_settled_leads(deps, case_id)


async def close_settled_leads(deps: Any, case_id: str) -> None:
    """A plan that ends in 'not covered' closes the lead behind it, with the rule as the reason."""
    from .discovery import close_lead
    snap = await snapshot(deps, case_id, ["plans", "claims", "holdings", "leads"])
    for cp in snap.plans:
        not_covered = next((s for s in cp.steps if s.id.startswith("not_covered") and s.status != "blocked"), None)
        if not not_covered:
            continue
        claim = snap.claim(cp.claim_id)
        if claim is None:
            continue
        for hid in claim.holding_ids:
            h = snap.holding(hid)
            lead = snap.lead(h.lead_id) if h else None
            if lead and lead.status != "closed":
                await close_lead(deps, case_id, lead.id, not_covered.text, not_covered.cites)
            if h and h.status != "closed":
                h.status = "closed"
                await deps.cases.put(case_id, "holdings", h)
        if claim.status != "closed":
            claim.status, claim.closed_reason = "closed", not_covered.text
            claim.next_action = "Nothing to claim"
            await deps.cases.put(case_id, "claims", claim)
        for c in await deps.cases.list(case_id, "checkpoints"):
            if c.status == "open" and (c.subject_ref or "").startswith(f"plan:{cp.id}:"):
                c.status, c.answer_note = "expired", not_covered.text
                await deps.cases.put(case_id, "checkpoints", c)
