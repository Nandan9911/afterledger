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
    """An amount for a screen: text exact, written without paise or cents when it has none ("₹8,00,000", not
    "₹8,00,000.00"; "₹41,870.50" keeps them), and short rounded to whole units, for totals and estimates."""
    if amount is None or amount == "" or not currency:
        return None
    value = Decimal(str(amount))
    return Money(amount=str(value), currency=currency, text=format_amount(value, currency, decimals=value % 1 != 0),
                 short=format_amount(value, currency, decimals=False))


_RULE_ID = re.compile(r"\b[a-z]{2}_[a-z0-9]+(?:_[a-z0-9]+)+\b")
_ISO_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_SIGNED_AMOUNT = re.compile(r"(₹|Rs\.?\s?|INR\s?)(\d{4,}(?:\.\d{1,2})?)(?![\d,])")
_CODED_AMOUNT = re.compile(r"(?<![\d,.])(\d{4,}(?:\.\d{2})?)\s?(SGD|INR|USD|GBP|JPY|EUR|AUD)\b")
# a grouped whole amount written with its paise or cents ("₹1,84,212.00"): shown as the screens show it ("₹1,84,212")
_WHOLE_GROUPED = re.compile(r"((?:₹|Rs\.?\s?|S\$|US\$|\$|£|€|¥)\s?\d{1,3}(?:,\d{2,3})+)\.00(?!\d)")
_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"]


async def tidy_text(deps: Any, text: str | None, *, ids_only: bool = False) -> str | None:
    """A sentence an agent wrote, as a family should read it: no rule ids in it (the rules are behind "Why?"), dates
    in words, and amounts with their grouping and symbol (until 19 October 2026 a lead could read "(in_rbi_305_inventory)",
    "2026-04-08" or "₹800000.00")."""
    if not text:
        return text
    k = knowledge(deps)
    out = text
    for m in set(_RULE_ID.findall(out)):
        if await k.assertion(m) is not None:
            out = out.replace(m, "")
    out = re.sub(r"\(\s*(?:,\s*)*\)", "", out)              # brackets left empty
    out = re.sub(r"\s+,", ",", re.sub(r",(\s*,)+", ",", out))
    out = re.sub(r"\s{2,}", " ", out).replace(" .", ".").strip()
    if ids_only:                          # a sentence in another language keeps its own dates and numbers
        return out

    def day(m: re.Match) -> str:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{d} {_MONTHS[mo - 1]} {y}" if 1 <= mo <= 12 and 1 <= d <= 31 else m.group(0)
    out = _ISO_DATE.sub(day, out)
    out = _SIGNED_AMOUNT.sub(lambda m: format_amount(Decimal(m.group(2)), "INR", decimals=False), out)
    out = _CODED_AMOUNT.sub(lambda m: format_amount(Decimal(m.group(1)), m.group(2),
                                                    decimals=not m.group(1).endswith(".00")), out)
    out = _WHOLE_GROUPED.sub(lambda m: m.group(1), out)
    return out


async def tidy_utterance(deps: Any, u: Any) -> Any:
    """An answer as a family reads it (tidy_text): in full when it is in English, its rule ids only otherwise."""
    if getattr(u, "role", "") != "assistant" or not getattr(u, "text", ""):
        return u
    english = (u.lang or "en").split("-")[0] == "en"
    update = {"text": await tidy_text(deps, u.text, ids_only=not english)}
    if u.translation:
        update["translation"] = await tidy_text(deps, u.translation, ids_only=english)
    return u.model_copy(update=update)


def chip(p: Person | None) -> PersonChip | None:
    if p is None:
        return None
    return PersonChip(id=p.id, name=p.display_name, name_en=p.name_en, initials=p.initials or p.display_name[:1],
                      color=p.color, relationship=p.relationship, languages=p.languages, city=p.city, country=p.country,
                      roles=list(p.roles))


def approver(snap: Snapshot, who: str | None) -> PersonChip | None:
    """Who approved: the person, or "family" when the screen did not say which (the web says "the family")."""
    if not who:
        return None
    return chip(snap.person(who)) or PersonChip(id="family", name="", initials="")


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


# Japan's e-Gov law service: the rules are checked against its XML (api/1/articles;lawId=...;article=...), and a
# person reads the same law on its page
_EGOV_API = re.compile(r"^https?://laws\.e-gov\.go\.jp/api/[^/]+/(?:articles;lawId=|lawdata/)([0-9A-Za-z]+)")


def read_url(url: str | None) -> str:
    """The page a person opens for a source: a machine-readable copy (an official data service's XML) is swapped for
    the page that shows the same text; every other address is kept as it is."""
    if not url:
        return ""
    m = _EGOV_API.match(url)
    if m:
        return f"https://laws.e-gov.go.jp/law/{m.group(1)}"
    return url


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
                            source_title=a.source.title, source_url=a.source.url, read_url=read_url(a.source.url),
                            publisher=a.source.publisher,
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


