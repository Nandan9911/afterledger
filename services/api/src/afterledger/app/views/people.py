"""People: who is involved, every way the papers write the deceased's name and why each is them,
questions about people seen only in payments, and who signs what."""
from __future__ import annotations

import re
from typing import Any

from ...modules.common import snapshot
from ..schemas import IdentityQuestion, NameSeen, PeopleView, SignCell, SignRow
from .common import chip, row_view
from .intake import pronoun

SHORT = [(r"annex_v$|annex_vi$|claim_?form|seikyu|irai|bereavement|claimant_statement", "the claim form"),
         (r"annex_vii$|annexure_4$|indemnity", "the indemnity"),
         (r"annex_viii$", "a no-objection letter"),
         (r"annex_ix$", "the declaration about the heirs"),
         (r"annexure_3$", "the transmission request"),
         (r"annexure_5$", "the affidavit and no-objection"),
         (r"heir_chart", "the heir chart"),
         (r"todoke", "the notification form")]


def short_item(item: Any) -> str:
    key = (item.form_key or "").lower()
    for rx, name in SHORT:
        if key and re.search(rx, key):
            return name
    title = re.sub(r"\s*\(.*?\)\s*", " ", item.title).split(" - ")[0].strip()
    return "the " + title[:1].lower() + title[1:] if title else "a paper"


def _signs_text(items: list[str]) -> str:
    uniq = list(dict.fromkeys(items))
    if not uniq:
        return "Nothing to sign"
    return "Signs " + (uniq[0] if len(uniq) == 1 else ", ".join(uniq[:-1]) + " and " + uniq[-1])


async def people(deps: Any, case_id: str) -> PeopleView:
    snap = await snapshot(deps, case_id)
    deceased = snap.deceased
    names: list[NameSeen] = []
    if deceased is not None:
        for nf in deceased.names:
            paper = snap.paper(nf.seen_on) if nf.seen_on and nf.seen_on != "family" else None
            seen = paper.title if paper else ("The family" if nf.seen_on in (None, "family") else nf.seen_on)
            why = nf.why or ""
            if not why and paper and paper.doc_type == "death_certificate":
                why = "The reference record"
            elif not why and nf.seen_on in (None, "family"):
                why = "As the family say it"
            names.append(NameSeen(form=nf, seen_on_title=seen, why=why))
    family = snap.family()
    lines = {ln.id: ln for ln in await deps.cases.list(case_id, "lines")}
    by_group = {g.id: g for g in snap.groups}
    questions = []
    unknown_people = set()
    for cp in snap.checkpoints:
        if cp.code != "C3" or cp.status != "open":
            continue
        pid = (cp.payload or {}).get("person_id")
        if pid:
            unknown_people.add(pid)
        gid = (cp.payload or {}).get("group_id")
        txns = [t for t in snap.transactions if gid and gid in by_group and t.id in set(by_group[gid].transaction_ids)]
        rows = [row_view(t, snap, lines) for t in sorted(txns, key=lambda t: t.date)[-3:]]
        questions.append(IdentityQuestion(checkpoint=cp, person=chip(snap.person(pid)),
                                          asked_of=chip(snap.person(cp.for_person_id)), rows=rows))
    signing: list[SignRow] = []
    pending: list[str] = []
    for claim in snap.claims:
        if claim.status == "closed" or claim.kind == "procedure":
            continue
        pack = next((p for p in snap.packs if p.claim_id == claim.id), None)
        if pack is None:
            pending.append(claim.title)
            continue
        holdings = [snap.holding(h) for h in claim.holding_ids]
        # with a nominee, only the nominee signs, so an unknown relative changes nothing here
        nominee_route = bool(holdings) and all(h is not None and h.nominee == "yes" for h in holdings)
        cells = []
        for p in family:
            mine = [short_item(it) for it in pack.items if p.id in it.signers and it.kind != "letter"]
            if not mine and any(p.id in it.signers for it in pack.items if it.kind == "letter"):
                mine = ["the covering letter"]
            if not mine and p.id in unknown_people and not nominee_route:
                cells.append(SignCell(person_id=p.id, text="Depends on the answer above", signs=False))
            else:
                cells.append(SignCell(person_id=p.id, text=_signs_text(mine), signs=bool(mine)))
        signing.append(SignRow(claim_id=claim.id, title=claim.title, country=claim.country, cells=cells))
    return PeopleView(deceased=deceased, names=names, family=family, questions=questions, signing=signing,
                      pending=pending, pronoun=pronoun(snap, await deps.cases.list(case_id, "utterances")))
