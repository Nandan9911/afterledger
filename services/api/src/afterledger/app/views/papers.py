"""Papers: what was added, how far reading has got, and every value with the place it was read from."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ...domain.calendars import format_date
from ...domain.money import format_amount
from ...modules.common import snapshot
from ...modules.reading import paper_currency
from ..schemas import PageView, PaperRow, PapersView, PaperView, ValueView
from .common import chip, row_view


def _note(paper: Any) -> str:
    if paper.status in ("queued", "reading"):
        return paper.stage or "Reading"
    if paper.values_to_check:
        return f"{'Photo, ' if paper.origin == 'photo' else ''}{paper.values_to_check} value" \
               f"{'s' if paper.values_to_check != 1 else ''} to check"
    if paper.status == "failed":
        return "Could not be read; try another photo"
    return paper.summary or "Read twice, matched"


async def papers(deps: Any, case_id: str) -> PapersView:
    snap = await snapshot(deps, case_id, ["papers", "people"])
    rows = [PaperRow(paper=p, uploaded_by=chip(snap.person(p.uploaded_by)), note=_note(p))
            for p in sorted(snap.papers, key=lambda p: (p.uploaded_at or deps.now(), p.id))]
    return PapersView(papers=rows, reading=sum(1 for p in snap.papers if p.status in ("queued", "reading")),
                      to_check=sum(p.values_to_check for p in snap.papers))


def display_value(v: Any, currency: str) -> str:
    if v.value is None:
        return ""
    if v.kind == "amount":
        try:
            return format_amount(Decimal(v.value), currency)
        except (InvalidOperation, ValueError):
            return v.value
    if v.kind == "date":
        try:
            from datetime import date
            return format_date(date.fromisoformat(v.value), "en")
        except ValueError:
            return v.value
    return v.value


def page_url(case_id: str, paper_id: str, page_id: str) -> str:
    return f"/v1/cases/{case_id}/papers/{paper_id}/pages/{page_id}/image"


async def paper(deps: Any, case_id: str, paper_id: str) -> PaperView | None:
    snap = await snapshot(deps, case_id, ["papers", "values", "transactions", "checkpoints", "holdings"])
    p = snap.paper(paper_id)
    if p is None:
        return None
    cur = paper_currency(p)
    cps = {c.subject_ref: c for c in snap.checkpoints if c.code == "C2"}
    values = []
    for v in snap.values_of(paper_id):
        values.append(ValueView(value=v, display=display_value(v, cur), checkpoint=cps.get(f"read_value:{v.id}")))
    order = {"check": 0, "unknown": 1, "sure": 2}
    values.sort(key=lambda x: (order.get(x.value.certainty, 3), x.value.page_id or "", x.value.label))
    lines = {ln.id: ln for ln in await deps.cases.list(case_id, "lines") if ln.paper_id == paper_id}
    rows = [row_view(t, snap, lines) for t in sorted((t for t in snap.transactions if t.paper_id == paper_id),
                                                       key=lambda t: (t.date, t.id))]
    pages = [PageView(id=pg.id, number=pg.number, width=pg.width, height=pg.height,
                      image_url=page_url(case_id, paper_id, pg.id)) for pg in p.pages]
    open_cps = [c for c in snap.checkpoints if c.code == "C2" and c.status == "open"
                and (c.payload or {}).get("paper_id") == paper_id]
    return PaperView(paper=p, pages=pages, values=values, rows=rows,
                     file_url=f"/v1/cases/{case_id}/papers/{paper_id}/file" if p.file else None, checkpoints=open_cps)
