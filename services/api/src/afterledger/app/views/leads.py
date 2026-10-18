"""Leads: what the papers point to that nobody listed, each with its evidence, the investigator's and
the critic's notes, and what families in this country usually hold."""
from __future__ import annotations

from typing import Any

from ...modules.common import knowledge, snapshot
from ..schemas import CoverageItem, LeadsView, LeadView
from .common import cite_views, institution_name, money, row_view

LABEL_ORDER = {"sure": 0, "likely": 1, "possible": 2, "unlikely": 3}


async def leads(deps: Any, case_id: str) -> LeadsView:
    snap = await snapshot(deps, case_id)
    k = knowledge(deps)
    lines = {ln.id: ln for ln in await deps.cases.list(case_id, "lines")}
    by_group = {g.id: g for g in snap.groups}
    cps = {c.subject_ref: c for c in snap.checkpoints if c.code == "C4"}
    out: dict[str, list[LeadView]] = {"open": [], "confirmed": [], "closed": []}
    cite_ids: set[str] = set()
    for lead in sorted(snap.leads, key=lambda x: (x.kind == "debt", LABEL_ORDER.get(x.label, 9), x.title)):
        txn_ids = [t for gid in lead.group_ids if gid in by_group for t in by_group[gid].transaction_ids]
        txns = sorted((t for t in snap.transactions if t.id in set(txn_ids)), key=lambda t: t.date)
        rows = [row_view(t, snap, lines) for t in txns[-6:]]
        inst = await k.institution(lead.institution_id) if lead.institution_id else None
        papers = [p.title for p in snap.papers if p.id in set(lead.paper_ids) | {t.paper_id for t in txns}]
        value = lead.value if lead.value is not None else None
        currency = lead.currency or (txns[0].currency if txns else None) or \
            next((h.currency for h in snap.holdings if h.id == lead.holding_id), None)
        if value is None and lead.holding_id:
            h = snap.holding(lead.holding_id)
            value = h.value if h else None
        view = LeadView(lead=lead, rows=rows, value=money(value, currency), institution=institution_name(inst) or None,
                        checkpoint=cps.get(f"lead:{lead.id}"), papers=papers)
        out[lead.status].append(view)
        cite_ids |= set(lead.cites)
    coverage = [CoverageItem(**{k_: v for k_, v in c.items() if k_ in CoverageItem.model_fields})
                for c in snap.case.coverage]
    return LeadsView(open=out["open"], confirmed=out["confirmed"], closed=out["closed"], coverage=coverage,
                     country=(snap.case.country_codes or [None])[0],
                     countries=list(dict.fromkeys(c.split("-")[0] for c in snap.case.country_codes)),
                     cites=await cite_views(deps, cite_ids, deps.today_for(snap.case)))
