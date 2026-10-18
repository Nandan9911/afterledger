"""Typed access to shared knowledge: assertions with their sources, compiled plans, the
institution directory, forms, rates, coverage lists and profiles.

Assertions in data/knowledge name their page by URL; the page snapshot supplies the source's
title, tier and dates, and every quote is checked against the page text before it is served.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from .domain.case import Institution
from .domain.checks import normalise, quote_in_page
from .domain.ids import sha256_text
from .domain.plan_language import LEVEL_ORDER, Assertion, ProcedurePlan, Source
from .ports import KnowledgeStore, PageSnapshot


class Knowledge:
    def __init__(self, store: KnowledgeStore, pages: Any):
        self.store = store
        self.pages = pages
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

    async def institution(self, inst_id: str) -> Institution | None:
        r = await self.store.get("institutions", inst_id)
        return Institution.model_validate({k: v for k, v in r.items() if k in Institution.model_fields}) if r else None

    async def institution_raw(self, inst_id: str) -> dict | None:
        return await self.store.get("institutions", inst_id)

    async def find_institution(self, text: str, country: str | None = None) -> tuple[Institution | None, float]:
        """Match a printed name or narration token against names and aliases in every script."""
        from rapidfuzz import fuzz
        t = normalise(text)
        best, best_score = None, 0.0
        for r in await self.store.list("institutions"):
            if country and r.get("country") not in (country, None) and not str(r.get("country", "")).startswith(country):
                continue
            names = [r.get("name", ""), *r.get("aliases", []), *r.get("narration_tokens", [])]
            for n in names:
                nn = normalise(n)
                if not nn:
                    continue
                if nn in t and len(nn) >= 3:
                    score = 0.97 if len(nn) >= 5 else 0.9
                else:
                    score = fuzz.partial_ratio(nn, t) / 100.0 if len(nn) >= 5 else 0.0
                if score > best_score:
                    best, best_score = r, score
        if best is None or best_score < 0.85:
            return None, best_score
        inst = Institution.model_validate({k: v for k, v in best.items() if k in Institution.model_fields})
        return inst, best_score

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

    async def profile(self, collection: str, key: str) -> dict | None:
        return await self.store.get(collection, key)

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
