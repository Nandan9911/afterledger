"""Typed access to shared knowledge: assertions with their sources, compiled plans, the
institution directory, forms, rates, coverage lists and profiles.

Assertions in data/knowledge name their page by URL; the page snapshot supplies the source's
title, tier and dates, and every quote is checked against the page text before it is served.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from .domain.case import Institution
from .domain.checks import normalise, quote_in_page
from .domain.ids import sha256_text
from .domain.plan_language import LEVEL_ORDER, Assertion, ProcedurePlan, Source
from .ports import KnowledgeStore, PageSnapshot

LEARNED = "~"
"""A learned institution's id is "<case id>~<slug>": one the directory does not have, named on a family's own papers
(the bank on a real statement) or by the family. It is kept with that case only, never in the shared directory, so one
family's papers say nothing to another's."""


def learned_case_id(inst_id: str | None) -> str | None:
    """The case a learned institution belongs to, or None for an institution of the directory."""
    if inst_id and LEARNED in inst_id:
        return inst_id.split(LEARNED, 1)[0] or None
    return None


def _words(text: str) -> str:
    """Normalised Latin text as plain words: 'UPI/DR/BAJAJ-FINANCE' -> ' upi dr bajaj finance '."""
    return " " + " ".join(re.sub(r"[^0-9a-z&]+", " ", normalise(text)).split()) + " "


def name_score(name: str, text: str) -> float:
    """How surely a printed text names an institution called `name`, 0 to 1. A Latin name must stand as whole words
    ('SBI' in 'SBI LIFE', never in the branch code 'SBIN0001234', which names any account held at SBI); a name run
    together on a statement ('BAJAJFINANCE') counts too; names in other scripts match as written. Close spellings of
    long names (an OCR slip) score just below an exact match."""
    from rapidfuzz import fuzz
    nn = normalise(name)
    if len(nn) < 3:
        return 0.0
    if nn.isascii():
        w = _words(name).strip()
        if len(w) < 3:
            return 0.0
        hay = _words(text)
        if f" {w} " in hay:
            return 0.97 if len(w) >= 5 else 0.9
        squashed = w.replace(" ", "")
        if " " in w and len(squashed) >= 8 and squashed in hay.replace(" ", ""):
            return 0.93
        if len(w) >= 6:
            return min(0.95, fuzz.partial_ratio(w, hay.strip()) / 100.0 * 0.98)
        return 0.0
    t = normalise(text)
    if nn in t:
        return 0.97
    return fuzz.partial_ratio(nn, t) / 100.0 if len(nn) >= 5 else 0.0


class Knowledge:
    def __init__(self, store: KnowledgeStore, pages: Any, cases: Callable[[], Any] | None = None):
        self.store = store
        self.pages = pages
        self._cases = cases                              # the case store, for institutions learned in a case
        self._page_meta: dict[str, dict] = {}
        self._page_hash: dict[str, str | None] = {}      # url -> hash of the snapshot's normalised text

    # ---- pages ------------------------------------------------------------------------------
    async def page(self, url: str) -> PageSnapshot | None:
        return await self.pages.fetch(url)

    async def page_for_quote(self, url: str, quote: str) -> PageSnapshot | None:
        """The page a quote is checked on: the saved snapshot when the quote is in it (the evidence a hand-checked rule
        rests on), otherwise, in a live run, the page as it is now, which is what research checked its rules on. A saved
        excerpt holds only the sentences a person copied: on 15 October five quotes research had found on justice.fr
        looked missing because the check read the excerpt of that page, not the page."""
        page = await self.page(url)
        if page is not None and quote_in_page(quote, page.text):
            return page
        fetch_live = getattr(self.pages, "fetch_live", None)
        if fetch_live is None:
            return page
        now = await fetch_live(url)
        return now if now is not None and quote_in_page(quote, now.text) else page or now

    def _meta(self, url: str) -> dict:
        if not self._page_meta:
            for rec in _fixture_page_records(self.pages):
                self._page_meta[rec["url"].rstrip("/").lower()] = rec
        return self._page_meta.get(url.rstrip("/").lower(), {})

    # ---- assertions -------------------------------------------------------------------------
    def _to_assertion(self, raw: dict) -> Assertion:
        if "source" in raw and isinstance(raw["source"], dict):
            return Assertion.model_validate(raw)
        meta = self._meta(raw["source_url"])
        url = raw["source_url"]
        if url not in self._page_hash:        # normalising a whole page is slow: once per page, not per assertion
            self._page_hash[url] = sha256_text(normalise(meta.get("text", ""))) if meta else None
        snap_hash = self._page_hash[url]
        source = Source(url=raw["source_url"], title=meta.get("title", ""), publisher=meta.get("publisher", ""),
                        tier=int(meta.get("tier", raw.get("tier", 4))), language=meta.get("lang", "en"),
                        retrieved=date.fromisoformat(str(meta.get("retrieved", "2026-10-14"))),
                        updated=meta.get("updated"), page_hash=snap_hash,
                        fictional=bool(meta.get("fictional", False)))
        return Assertion(
            id=raw["id"], text=raw["text"], quote=raw["quote"], quote_translation=raw.get("quote_translation"),
            source=source, jurisdiction=raw["jurisdiction"], topic=raw.get("topic", ""),
            institution_id=raw.get("institution_id"), level=raw.get("level", "sourced"),
            ttl_days=int(raw.get("ttl_days", 180)),
            checked_on=date.fromisoformat(str(raw.get("checked_on", meta.get("retrieved", "2026-10-14")))),
            researchers=raw.get("researchers", []), contested_with=raw.get("contested_with", []),
            notes=raw.get("notes", "") + (f" Locator: {raw['locator']}." if raw.get("locator") else ""))

    async def assertions(self) -> list[Assertion]:
        return [self._to_assertion(r) for r in await self.store.list("assertions")]

    async def assertion(self, assertion_id: str) -> Assertion | None:
        raw = await self.store.get("assertions", assertion_id)
        return self._to_assertion(raw) if raw else None

    async def assertions_for(self, *, jurisdiction: str | None = None, topic_prefix: str | None = None,
                             institution_id: str | None = None) -> list[Assertion]:
        out = []
        for a in await self.assertions():
            if jurisdiction and not (a.jurisdiction == jurisdiction or a.jurisdiction.startswith(jurisdiction + "-")
                                     or jurisdiction.startswith(a.jurisdiction + "-")):
                continue
            if topic_prefix and not a.topic.startswith(topic_prefix):
                continue
            if institution_id and a.institution_id not in (None, institution_id):
                continue
            out.append(a)
        return out

    async def put_assertion(self, a: Assertion) -> None:
        await self.store.put("assertions", a.id, a.model_dump(mode="json"))

    async def verify_quote(self, a: Assertion) -> tuple[bool, str]:
        page = await self.page_for_quote(a.source.url, a.quote)
        if page is None:
            return False, "page not reachable"
        return quote_in_page(a.quote, page.text), page.content_hash

    def is_fresh(self, a: Assertion, today: date) -> bool:
        checked = a.checked_on or a.source.retrieved
        return (today - checked).days <= a.ttl_days

    @staticmethod
    def level_at_least(a: Assertion, level: str) -> bool:
        return LEVEL_ORDER[a.level] >= LEVEL_ORDER[level]

    # ---- plans ------------------------------------------------------------------------------
    async def plan(self, key: str) -> ProcedurePlan | None:
        raw = await self.store.get("plans", key)
        return ProcedurePlan.model_validate(raw) if raw else None

    async def plans(self) -> list[ProcedurePlan]:
        return [ProcedurePlan.model_validate(r) for r in await self.store.list("plans")]

    async def put_plan(self, plan: ProcedurePlan) -> None:
        await self.store.put("plans", plan.key, plan.model_dump(mode="json"))

    # ---- institutions -----------------------------------------------------------------------
    async def institutions(self) -> list[Institution]:
        out = []
        for r in await self.store.list("institutions"):
            out.append(Institution.model_validate({k: v for k, v in r.items() if k in Institution.model_fields}))
        return out

    async def _learned(self, inst_id: str) -> Institution | None:
        case_id = learned_case_id(inst_id)
        store = self._cases() if (case_id and self._cases is not None) else None
        if store is None:
            return None
        rec = await store.get(case_id, "institutions", inst_id)
        if rec is None or isinstance(rec, Institution):
            return rec
        return Institution.model_validate(rec)

    async def institution(self, inst_id: str | None) -> Institution | None:
        if not inst_id:
            return None
        if learned_case_id(inst_id):
            return await self._learned(inst_id)
        r = await self.store.get("institutions", inst_id)
        return Institution.model_validate({k: v for k, v in r.items() if k in Institution.model_fields}) if r else None

    async def institution_raw(self, inst_id: str | None) -> dict | None:
        if not inst_id:
            return None
        if learned_case_id(inst_id):
            inst = await self._learned(inst_id)
            return inst.model_dump(mode="json") if inst is not None else None
        return await self.store.get("institutions", inst_id)

    async def put_institution(self, record: dict) -> None:
        """An institution researched on the web joins the shared directory, for every family after this one."""
        await self.store.put("institutions", record["id"], record)

    async def find_institution(self, text: str, country: str | None = None, *,
                               case_id: str | None = None) -> tuple[Institution | None, float]:
        """Match a printed name or narration token against names and aliases in every script: the directory's, those
        researched since, and (with case_id) the institutions learned in this case. A longer name that matches beats
        a shorter one (ICICI Prudential Life over ICICI Bank in 'ICICI PRU LIFE PREMIUM')."""
        if not (text or "").strip():
            return None, 0.0
        records = list(await self.store.list("institutions"))
        if case_id and self._cases is not None:
            for inst in await self._cases().list(case_id, "institutions"):
                if inst.learned and learned_case_id(inst.id) == case_id:
                    records.append(inst.model_dump(mode="json"))
        best, best_key = None, (0.0, 0)
        for r in records:
            rc = str(r.get("country") or "")
            if country and rc and rc != country and not rc.startswith(country) and not country.startswith(rc):
                continue
            names = [r.get("name", ""), r.get("short_name") or "", *r.get("aliases", []), *r.get("narration_tokens", [])]
            for n in names:
                if not n:
                    continue
                score = name_score(n, text)
                key = (round(score, 3), len(normalise(n)))
                if key > best_key:
                    best, best_key = r, key
        if best is None or best_key[0] < 0.88:
            return None, best_key[0]
        inst = Institution.model_validate({k: v for k, v in best.items() if k in Institution.model_fields})
        return inst, best_key[0]

    # ---- rates, coverage, profiles, forms ------------------------------------------------------
    async def rate_on(self, rate_id: str, on: date) -> tuple[Decimal, str] | None:
        raw = await self.store.get("rates", rate_id)
        if not raw:
            return None
        best = None
        for entry in raw.get("history", []):
            if date.fromisoformat(str(entry["from"])) <= on and (best is None or entry["from"] > best["from"]):
                best = entry
        return (Decimal(str(best["pct"])), best.get("cites", "")) if best else None

    async def coverage(self, country: str) -> list[dict]:
        raw = await self.store.get("coverage", country)
        return raw.get("items", []) if raw else []


    async def form(self, key: str) -> dict | None:
        return await self.store.get("forms", key)

    async def forms(self) -> list[dict]:
        return await self.store.list("forms")


def _fixture_page_records(pages: Any) -> list[dict]:
    """Page metadata (tier, publisher, retrieved) as written in the fixture files."""
    import yaml

    from .config import get_settings
    out: list[dict] = []
    folder = get_settings().knowledge_dir / "pages"
    if folder.is_dir():
        for f in sorted(folder.rglob("*.y*ml")):
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            out.extend(data.get("pages", []) if isinstance(data, dict) else data)
    return out
