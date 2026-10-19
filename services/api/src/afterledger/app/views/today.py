"""Today: the greeting, what the family has, the few things that need them, the deadlines and the doubts."""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from ...modules import followup
from ...modules.common import Snapshot, knowledge, snapshot
from ..schemas import CurrencyTotals, DeadlineView, LateClock, TaskView, TodayView, Uncertainty
from .common import chip, chips, cite_views, institution_name, money, tidy_text
from .localize import localize


def totals_view(raw: dict) -> list[CurrencyTotals]:
    out = []
    for cur, t in raw.get("currencies", {}).items():
        out.append(CurrencyTotals(currency=cur, known=money(t["known"], cur), paid=money(t["paid"], cur),
                                  in_progress=money(t["in_progress"], cur), owed=money(t["owed"], cur),
                                  holdings_known=int(t.get("holdings_known", 0))))
    return out


_CASH = re.compile(r"\b(ATM|CASH|WDL|WITHDRAWAL|GAA|BARGELD|現金|引出)\b", re.I)
_FEE = re.compile(r"\b(AMC|FEE|CHARGE|CHG|GEBÜHR|手数料)\b", re.I)


def _after_death(snap: Snapshot, inst_names: dict[str, str]) -> list[Uncertainty]:
    """Money that left an account after the death: the bank asks about cash, and a fee can be reversed."""
    out = []
    by_holding: dict[str, list] = {}
    for t in snap.transactions:
        if t.after_death and t.direction == "debit":
            by_holding.setdefault(t.holding_id or f"paper:{t.paper_id}", []).append(t)
    for hid, txns in by_holding.items():
        h = snap.holding(hid)
        paper = snap.paper(txns[0].paper_id)
        inst_id = (h.institution_id if h else None) or (paper.institution_id if paper else None)
        where = inst_names.get(inst_id or "", "")
        parts, cash, fee = [], False, False
        for t in sorted(txns, key=lambda x: x.date):
            amt = money(t.amount, t.currency)
            if _CASH.search(t.narration):
                parts.append(f"{amt.short} taken in cash")
                cash = True
            elif _FEE.search(t.narration):
                parts.append(f"a {amt.short} fee")
                fee = True
            else:
                parts.append(f"{amt.short} paid to {t.narration.title()}")
        n = len(parts)
        what = ("One payment" if n == 1 else f"{_word(n)} payments") + f" left the {where + ' ' if where else ''}account " \
            "after the date of death: " + (parts[0] if n == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]) + "."
        notes = []
        if cash:
            notes.append("The bank will ask about the cash")
        if fee:
            notes.append("the fee can be reversed" if notes else "The fee can be reversed")
        if notes:
            what += " " + ", and ".join(notes) + "."
        out.append(Uncertainty(certainty="check", text=what, ref=f"holding:{hid}" if h else None))
    return out


def _noun(label: str) -> str:
    """'A family pension for Sunita' -> 'family pension for Sunita', for use after 'the'."""
    words = label.split(" ", 1)
    if len(words) == 2 and words[0].lower() in ("a", "an", "the"):
        label = words[1]
    return label[:1].lower() + label[1:] if label[:2] != label[:2].upper() else label


def _word(n: int) -> str:
    return {2: "Two", 3: "Three", 4: "Four", 5: "Five"}.get(n, str(n))


_SCRIPT_LANG = [(r"[\u0900-\u097f]", "hi"), (r"[\u0d00-\u0d7f]", "ml"), (r"[\u3040-\u30ff\u4e00-\u9fff]", "ja"),
                (r"[\u0600-\u06ff]", "ar"), (r"[\u0b80-\u0bff]", "ta"), (r"[\u0980-\u09ff]", "bn")]


def text_language(text: str, case_languages: list[str]) -> str | None:
    """The language a family-language text is written in: by its script, or else the case's first language
    that is not English (for Spanish or German, which share the Latin script with English)."""
    for rx, lang in _SCRIPT_LANG:
        if re.search(rx, text or ""):
            return lang
    return next((lang.split("-")[0] for lang in case_languages if not lang.startswith("en")), None)


async def today(deps: Any, case_id: str, viewer_id: str | None = None, lang: str = "en") -> TodayView:
    snap = await snapshot(deps, case_id)
    case = snap.case
    k = knowledge(deps)
    day = deps.today_for(case)
    viewer = snap.person(viewer_id) or snap.person(case.viewer_person_id)
    inst_names: dict[str, str] = {}
    for iid in {h.institution_id for h in snap.holdings if h.institution_id} | {c.institution_id for c in snap.claims}:
        inst = await k.institution(iid) if iid else None
        inst_names[iid] = institution_name(inst, iid)
    tasks = [t.model_copy(update={"title": await tidy_text(deps, t.title), "body": await tidy_text(deps, t.body)})
             for t in sorted(snap.tasks, key=lambda t: -t.priority)]
    task_views = [TaskView(task=t, owners=chips(snap, t.owner_person_ids),
                           for_viewer=bool(viewer and viewer.id in t.owner_person_ids)) for t in tasks]
    deadlines = [DeadlineView(date=date.fromisoformat(d["date"]), title=d["title"], note=d.get("note", ""),
                              late=d.get("late", False), kind=d.get("kind", "clock"), claim_id=d.get("claim_id"),
                              owner=d.get("owner"), cites=d.get("cites", []))
                 for d in await followup.deadlines(deps, case_id, refresh=False)]
    late = []
    for c in snap.claims:
        if c.status in ("settled", "closed"):
            continue
        for clk in c.clocks:
            if clk.status == "late" and clk.owner == "institution":
                owners = next((tv.owners for tv in task_views if tv.task.claim_id == c.id), [])
                late.append(LateClock(claim_id=c.id, institution=inst_names.get(c.institution_id, c.institution_id),
                                      days_late=clk.days_late, due_on=clk.due_on,
                                      compensation=money(clk.compensation, c.currency),
                                      per_day=money(clk.per_day, c.currency), owners=owners))
    uncertain = _after_death(snap, inst_names)
    for v in snap.values:
        if v.certainty == "check" and v.tier in ("A", "B"):
            paper = snap.paper(v.paper_id)
            uncertain.append(Uncertainty(certainty="check", text=f"{v.label} on the {paper.title if paper else 'paper'}: "
                                         f"the two readings did not agree.", ref=f"value:{v.id}"))
    for cp in snap.checkpoints:
        if cp.status == "open" and cp.code == "C5" and cp.kind == "plan_question":
            claim = snap.claim((cp.payload or {}).get("claim_id"))
            text = cp.question.rstrip("?") + "?" + (" The plan covers every answer until we know." if claim else "")
            uncertain.append(Uncertainty(certainty="unknown", text=text, ref=f"checkpoint:{cp.id}"))
    for h in snap.holdings:
        if h.status != "confirmed" or h.direction != "asset" or h.value is not None:
            continue                      # leads, debts and pensions are not 'values to find'
        name = inst_names.get(h.institution_id or "", "")
        noun = _noun(h.label)
        text = f"The value of the {noun}" + (f" at {name}" if name and name.lower() not in noun.lower() else "") + "."
        expected = next((c for c in snap.correspondence if c.expected_on and c.expected_on >= day and
                         c.claim_id and c.claim_id == h.claim_id), None)
        if expected and expected.expected_note:
            text += f" {expected.expected_note.rstrip('.')}."
        uncertain.append(Uncertainty(certainty="unknown", text=text, ref=f"holding:{h.id}"))
    for lead in snap.leads:
        if lead.status == "open" and lead.kind in ("asset", "entitlement") and lead.label == "possible":
            uncertain.append(Uncertainty(certainty="unknown", text=lead.title + ".", ref=f"lead:{lead.id}"))
    reading = [p.title for p in snap.papers if p.status in ("queued", "reading")]
    raw = await followup.totals(deps, case_id)
    cite_ids = {c for t in tasks for c in t.cites} | {c for d in deadlines for c in d.cites}
    # each family member has their own greeting; one written for someone else is shown only until it is redone
    note = case.today_notes.get(viewer.id) if viewer else None
    greeting, greeting_local = (note.text, note.local) if note else (case.today_note, case.today_note_local)
    greeting_for = viewer.id if note and viewer else case.today_note_for
    view = TodayView(date=day, greeting=greeting, greeting_local=greeting_local, viewer=chip(viewer),
                     totals=totals_view(raw), unknown_values=raw.get("unknown_leads", 0), tasks=task_views,
                     deadlines=deadlines, late=late, uncertain=uncertain[:4], reading=reading,
                     greeting_lang=text_language(greeting_local, case.languages) if greeting_local else None,
                     greeting_for=greeting_for, cites=await cite_views(deps, cite_ids, day))
    await localize(deps, lang, [(d, f) for d in view.deadlines for f in ("title", "note")]
                   + [(u, "text") for u in view.uncertain])
    return view

