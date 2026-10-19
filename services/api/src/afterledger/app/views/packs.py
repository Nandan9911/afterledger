"""Packs: the papers for one claim, filled, read back and checked, waiting for the family's approval."""
from __future__ import annotations

from typing import Any

from ...modules.common import knowledge, snapshot
from ..schemas import PackItemView, PackRow, PacksView, PackView
from .common import approver, chips, cite_views, institution_name, money
from .localize import localize


def item_url(case_id: str, pack_id: str, item_id: str, mirror: bool = False) -> str:
    return f"/v1/cases/{case_id}/packs/{pack_id}/items/{item_id}/file" + ("?mirror=1" if mirror else "")


async def packs(deps: Any, case_id: str, lang: str = "en") -> PacksView:
    snap = await snapshot(deps, case_id, ["packs", "claims"])
    k = knowledge(deps)
    rows = []
    for p in sorted(snap.packs, key=lambda p: ({"ready": 0, "draft": 1, "approved": 2, "submitted": 3}.get(p.status, 4),
                                               p.title)):
        claim = snap.claim(p.claim_id)
        inst = await k.institution(claim.institution_id) if claim and claim.institution_id else None
        rows.append(PackRow(pack=p, claim_title=claim.title if claim else "", institution=institution_name(inst)))
    view = PacksView(rows=rows)
    await localize(deps, lang, [(r, "claim_title") for r in view.rows]
                   + [(r.pack, f) for r in view.rows for f in ("title", "summary")])
    return view


async def pack(deps: Any, case_id: str, pack_id: str, lang: str = "en") -> PackView | None:
    snap = await snapshot(deps, case_id, ["packs", "claims", "people", "checkpoints", "plans"])
    p = next((x for x in snap.packs if x.id == pack_id), None)
    if p is None:
        return None
    claim = snap.claim(p.claim_id)
    k = knowledge(deps)
    inst = await k.institution(claim.institution_id) if claim and claim.institution_id else None
    from ..actions import item_pages
    items = []
    for it in p.items:
        has_file = bool(it.file or it.text)
        items.append(PackItemView(
            item=it, file_url=item_url(case_id, p.id, it.id) if has_file else None,
            mirror_url=item_url(case_id, p.id, it.id, True) if (it.mirror_file or it.mirror_text) else None,
            signers=chips(snap, it.signers), pages=await item_pages(deps, case_id, p.id, it.id) if has_file else 0,
            page_url=f"/v1/cases/{case_id}/packs/{p.id}/items/{it.id}/pages" if has_file else None))
    cp = next((c for c in snap.checkpoints if c.code == "C6" and c.subject_ref == f"pack:{p.id}"), None)
    plan = snap.plan_for_claim(claim.id) if claim else None
    ids: set[str] = set()
    if plan:
        for d in plan.documents:
            ids |= set(d.cites)
        for s in plan.steps:
            ids |= set(s.cites)
    view = PackView(pack=p, claim=claim, institution=inst, amount=money(claim.amount_due, claim.currency) if claim else None,
                    items=items, checkpoint=cp, approved_by=approver(snap, p.approved_by),
                    download_url=f"/v1/cases/{case_id}/packs/{p.id}/download",
                    cites=await cite_views(deps, ids, deps.today_for(snap.case)))
    # the words the server wrote, in the page's language; the forms and the letter stay as they will be sent
    await localize(deps, lang, [(iv.item, f) for iv in view.items for f in ("title", "note")]
                   + [(fld, "note") for iv in view.items for fld in iv.item.fields]
                   + [(e, f) for e in view.pack.enclosures for f in ("name", "note")]
                   + [(view.pack, "summary"), (view.pack, "submit_how"), (view.pack, "title")]
                   + ([(view.claim, "title")] if view.claim else []))
    return view
