"""Sources: every rule the case relies on, in the source's own words, with how well it is checked."""
from __future__ import annotations

from typing import Any

from ...modules.common import knowledge, snapshot
from ...ports import PageSnapshot
from ..schemas import AssertionView, LevelCount, PageSnapshotView, SourceRow, SourcesView
from .case import case_cites
from .common import LEVEL_LABEL, cite_views, institution_name

LEVELS = ["gold", "outcome_confirmed", "adjudicated", "dual_verified", "sourced", "draft"]


async def sources(deps: Any, case_id: str) -> SourcesView:
    snap = await snapshot(deps, case_id)
    used = case_cites(snap)
    today = deps.today_for(snap.case)
    cites = await cite_views(deps, set(used), today)
    k = knowledge(deps)
    inst_names: dict[str, str] = {}
    for c in snap.claims:
        if c.institution_id and c.institution_id not in inst_names:
            inst_names[c.institution_id] = institution_name(await k.institution(c.institution_id), c.institution_id)
    rows = []
    for aid, cv in cites.items():
        insts = sorted({inst_names[c.institution_id] for c in snap.claims if c.title in used[aid]
                        and c.institution_id in inst_names})
        rows.append(SourceRow(cite=cv, used_by=sorted(used[aid]), institutions=insts))
    rows.sort(key=lambda r: (LEVELS.index(r.cite.level) if r.cite.level in LEVELS else 9, r.cite.jurisdiction, r.cite.id))
    counts = []
    for lvl in LEVELS:
        n = sum(1 for r in rows if r.cite.level == lvl)
        if n:
            counts.append(LevelCount(level=lvl, label=LEVEL_LABEL[lvl], count=n))
    researching = []
    for cp in snap.plans:
        claim = snap.claim(cp.claim_id)
        if claim is None or claim.status == "closed":
            continue
        if cp.level == "draft":
            researching.append(f"{claim.title}: not verified yet, so the plan starts by asking the institution in "
                               "writing.")
    return SourcesView(rows=rows, counts=counts, researching=researching,
                       institutions=[{"id": i, "name": n} for i, n in sorted(inst_names.items(), key=lambda x: x[1])])


async def assertion(deps: Any, assertion_id: str, today: Any) -> AssertionView | None:
    views = await cite_views(deps, [assertion_id], today)
    cv = views.get(assertion_id)
    if cv is None:
        return None
    page = await knowledge(deps).page_for_quote(cv.source_url, cv.quote)
    excerpt, found = "", False
    if page is not None:
        from ...domain.checks import quote_in_page
        found = quote_in_page(cv.quote, page.text)
        excerpt = _excerpt(page, cv.quote)
    return AssertionView(cite=cv, page_excerpt=excerpt, quote_found=found)


def _excerpt(page: PageSnapshot, quote: str, around: int = 600) -> str:
    text = page.text
    head = quote[:40]
    i = text.find(head)
    if i < 0:
        return text[:around * 2]
    start = max(0, i - around)
    return ("…" if start else "") + text[start:i + len(quote) + around] + ("…" if i + len(quote) + around < len(text) else "")


async def page(deps: Any, url: str) -> PageSnapshotView | None:
    p = await deps.pages.fetch(url)
    if p is None:
        return None
    return PageSnapshotView(url=p.url, title=p.title, text=p.text, updated=p.updated, tier=p.tier,
                            publisher=p.publisher, fictional=p.fictional, lang=p.lang)
