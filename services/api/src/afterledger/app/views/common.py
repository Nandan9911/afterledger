"""Helpers shared by the screen read models: amounts, people, sources and statement rows."""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ...domain.case import Person, Transaction
from ...domain.money import format_amount
from ...modules.common import Snapshot, knowledge
from ..schemas import CiteView, Money, PersonChip, RowView

LEVEL_LABEL = {"gold": "Gold", "outcome_confirmed": "Confirmed by an outcome", "adjudicated": "Settled after a conflict",
               "dual_verified": "Two researchers agree", "sourced": "One official source", "draft": "Not verified yet"}


def money(amount: Any, currency: str | None) -> Money | None:
    if amount is None or amount == "" or not currency:
        return None
    value = Decimal(str(amount))
    return Money(amount=str(value), currency=currency, text=format_amount(value, currency),
                 short=format_amount(value, currency, decimals=False))


def chip(p: Person | None) -> PersonChip | None:
    if p is None:
        return None
    return PersonChip(id=p.id, name=p.display_name, name_en=p.name_en, initials=p.initials or p.display_name[:1],
                      color=p.color, relationship=p.relationship, languages=p.languages, city=p.city, country=p.country,
                      roles=list(p.roles))


def chips(snap: Snapshot, ids: list[str]) -> list[PersonChip]:
    out = []
    for pid in ids:
        c = chip(snap.person(pid))
        if c is not None and all(x.id != c.id for x in out):
            out.append(c)
    return out


def tier_label(tier: int | None, publisher: str = "", url: str = "") -> str:
    pub = f"{publisher} {url}".lower()
    if tier == 1:
        if re.search(r"reserve bank|securities and exchange|insurance regulatory|rbidocs|sebi\.gov|irdai|caalley", pub):
            return "Regulator"
        if re.search(r"legislat|e-gov|laws\.e-gov|statute|legis\.texas|bare act|eur/", pub):
            return "Law"
        if re.search(r"court|裁判所", pub):
            return "Court"
        return "Government source"
    if tier == 2:
        return "Institution's own page"
    if tier == 3:
        return "Public body"
    if tier == 4:
        return "Secondary source"
    if tier == 5:
        return "Forum, never evidence"
    return "Source"


async def cite_views(deps: Any, ids: list[str] | set[str], today: date) -> dict[str, CiteView]:
    k = knowledge(deps)
    out: dict[str, CiteView] = {}
    for aid in sorted({i for i in ids if i}):
        a = await k.assertion(aid)
        if a is None:
            continue
        checked = a.checked_on or a.source.retrieved
        until = checked + timedelta(days=a.ttl_days) if checked else None
        locator = a.notes.split("Locator:", 1)[1].strip().rstrip(".") if "Locator:" in a.notes else ""
        out[aid] = CiteView(id=a.id, text=a.text, quote=a.quote, quote_translation=a.quote_translation,
                            source_title=a.source.title, source_url=a.source.url, publisher=a.source.publisher,
                            tier=a.source.tier, tier_label=tier_label(a.source.tier, a.source.publisher, a.source.url),
                            level=a.level, level_label=LEVEL_LABEL.get(a.level, a.level), checked_on=checked,
                            fresh_until=until, fresh=bool(until is None or until >= today), locator=locator,
                            language=a.source.language, fictional=a.source.fictional, jurisdiction=a.jurisdiction,
                            institution_id=a.institution_id, contested_with=a.contested_with)
    return out


def row_view(t: Transaction, snap: Snapshot, lines: dict[str, Any] | None = None) -> RowView:
    paper = snap.paper(t.paper_id)
    page_no = None
    if paper and t.page_id:
        page_no = next((p.number for p in paper.pages if p.id == t.page_id), None)
    box = None
    if lines is not None and t.line_id and t.line_id in lines:
        box = lines[t.line_id].box
    return RowView(id=t.id, date=t.date, narration=t.narration, amount=money(t.amount, t.currency),
                   direction=t.direction, balance=money(t.balance, t.currency) if t.balance is not None else None,
                   after_death=t.after_death, paper_id=t.paper_id, paper_title=paper.title if paper else "",
                   page_id=t.page_id, page_number=page_no, line_id=t.line_id, box=box)


def institution_name(inst: Any, fallback: str = "") -> str:
    if inst is None:
        return fallback
    return inst.short_name or inst.name


def plural(n: int, noun: str, many: str | None = None) -> str:
    return f"{n} {noun if n == 1 else (many or noun + 's')}"
