"""Plans: one per claim, in plain words, with every step's source, the questions that decide the route,
and any clock with the compensation it is running up, worked out by code."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from ...domain.calendars import format_date
from ...modules.common import knowledge, snapshot
from ..schemas import Compensation, PlanRow, PlansView, PlanView
from .common import chip, cite_views, institution_name, money, tidy_text
from .localize import localize

STATUS = {"planning": "Being planned", "ready": "Papers ready to sign", "submitted": "Handed in",
          "acknowledged": "Every paper received", "settled": "Paid", "returned": "Returned by the institution",
          "escalated": "Escalated", "handed_off": "With a professional", "closed": "Nothing to claim"}


async def compensation_views(deps: Any, claim: Any, plan_key: str | None, today: date) -> list[Compensation]:
    k = knowledge(deps)
    plan = await k.plan(plan_key) if plan_key else None
    out = []
    for clk in claim.clocks:
        if not clk.compensation_formula or clk.per_day is None:
            continue
        rate = None
        note = ""
        if clk.base_rate_pct is not None and clk.spread_pct is not None:
            rate = str((Decimal(str(clk.base_rate_pct)) + Decimal(str(clk.spread_pct))).normalize())
        spec = plan.clock(clk.clock_id) if plan else None
        params = (spec.compensation_params if spec is not None else None) or {}
        rate_id = params.get("base_rate")
        if rate_id and clk.started_on:
            raw = await k.store.get("rates", rate_id)
            later = [e for e in (raw or {}).get("history", [])
                     if clk.started_on < date.fromisoformat(str(e["from"])) <= today]
            if later:
                e = later[-1]
                note = (f"The rate is taken on the day every paper was received, as the rule says, so the change to "
                        f"{Decimal(str(e['pct'])):.2f}% on {format_date(date.fromisoformat(str(e['from'])), 'en')} "
                        "does not apply.")
        out.append(Compensation(clock=clk, base_rate_pct=f"{Decimal(str(clk.base_rate_pct)):.2f}" if clk.base_rate_pct is not None else None,
                                spread_pct=str(Decimal(str(clk.spread_pct)).normalize()) if clk.spread_pct is not None else None,
                                rate_pct=f"{Decimal(rate):.2f}" if rate else None,
                                amount=money(clk.amount, claim.currency), per_day=money(clk.per_day, claim.currency),
                                owed=money(clk.compensation, claim.currency), rate_on=clk.started_on,
                                note=("Worked out by code, not by the model. " + note).strip()))
    return out


def _document_states(documents: list[Any], claim: Any, pack: Any) -> list[Any]:
    """A plan lists the papers its route needs; how far each has got comes from the pack and the claim."""
    handed = claim.status in ("submitted", "acknowledged", "settled")
    out = []
    for d in documents:
        d = d.model_copy()
        in_pack = pack is not None and any(
            re.search(rf"(^|_){re.escape(d.id)}$", (it.form_key or "").split("/")[-1]) for it in pack.items)
        if handed and d.status in ("missing", "have", "filled", "signed") and (in_pack or d.status == "have"):
            d.status = "submitted"
        elif in_pack and d.status == "missing":
            d.status = "signed" if pack.status in ("approved", "submitted") else "filled"
        out.append(d)
    return out


async def plans(deps: Any, case_id: str) -> PlansView:
    snap = await snapshot(deps, case_id, ["claims", "plans", "checkpoints"])
    k = knowledge(deps)
    rows = []
    for claim in sorted(snap.claims, key=lambda c: (c.status == "closed", c.kind == "procedure", c.title)):
        cp = snap.plan_for_claim(claim.id)
        inst = await k.institution(claim.institution_id) if claim.institution_id else None
        late = any(c.status == "late" and c.owner == "institution" for c in claim.clocks) and \
            claim.status not in ("settled", "closed")
        q = sum(1 for c in snap.checkpoints if c.status == "open" and c.code == "C5" and c.kind == "plan_question"
                and (c.payload or {}).get("claim_id") == claim.id)
        rows.append(PlanRow(claim=claim, plan=cp, institution=institution_name(inst, claim.institution_id),
                            amount=money(claim.amount_due, claim.currency), status_label=STATUS.get(claim.status, claim.status),
                            late=late, questions=q))
    return PlansView(rows=rows)


async def plan(deps: Any, case_id: str, plan_id: str, lang: str = "en") -> PlanView | None:
    snap = await snapshot(deps, case_id, ["claims", "plans", "checkpoints", "people", "packs"])
    cp = next((p for p in snap.plans if p.id == plan_id), None)
    if cp is None:
        return None
    claim = snap.claim(cp.claim_id)
    if claim is None:
        return None
    k = knowledge(deps)
    inst = await k.institution(claim.institution_id) if claim.institution_id else None
    questions = [c for c in snap.checkpoints if c.status == "open" and c.code == "C5" and c.kind == "plan_question"
                 and (c.subject_ref or "").startswith(f"plan:{cp.id}:")]
    approval = next((c for c in snap.checkpoints if c.code == "C5" and c.kind == "plan_approval"
                     and c.subject_ref == f"plan:{cp.id}:approve"), None)
    ids: set[str] = set()
    for s in cp.steps:
        ids |= set(s.cites)
    for d in cp.documents:
        ids |= set(d.cites)
    for c in cp.clocks:
        ids |= set(c.cites)
    for e in cp.explained:
        ids |= set(e.get("cites") or [])
    family_clocks = []
    sources = [cp] + [snap.plan_for_claim(c.id) for c in snap.claims
                      if c.kind == "procedure" and claim.id in c.needed_by and c.status != "closed"]
    for src in sources:
        for c in (src.clocks if src else []):
            if c.owner == "family" and c.due_on and c.status in ("running", "late", "not_started") \
                    and all(x.clock_id != c.clock_id for x in family_clocks):
                family_clocks.append(c)
                ids |= set(c.cites)
    today = deps.today_for(snap.case)
    procedures = [c.title for c in snap.claims if c.kind == "procedure" and claim.id in c.needed_by
                  and c.status != "closed"]
    needed_by = [c.title for c in snap.claims if c.id in claim.needed_by]
    # the papers for the plan shown (an interim plan's letter is a pack of its own)
    pack = next((p for p in snap.packs if p.claim_id == claim.id and p.plan_key in (None, cp.plan_key)), None) or \
        next((p for p in snap.packs if p.claim_id == claim.id), None)
    explained = [{**e, "text": await tidy_text(deps, e.get("text")),
                  "text_local": await tidy_text(deps, e.get("text_local"), ids_only=True)} for e in cp.explained]
    cp = cp.model_copy(update={"documents": _document_states(cp.documents, claim, pack),
                               "summary": await tidy_text(deps, cp.summary), "explained": explained}, deep=True)
    view = PlanView(plan=cp, claim=claim, institution=inst, amount=money(claim.amount_due, claim.currency),
                    questions=questions, approval=approval, approved_by=chip(snap.person(cp.approved_by)),
                    compensation=await compensation_views(deps, claim, cp.plan_key, today),
                    cites=await cite_views(deps, ids, today), pack_id=pack.id if pack else None,
                    needed_by=needed_by, procedures=procedures,
                    family_clocks=[c.model_copy() for c in family_clocks])
    # the words the server wrote, in the page's language (the plan's own *_local texts are already there)
    await localize(deps, lang, [(q, "question") for q in view.questions] + [(s, "text") for s in view.plan.steps]
                   + [(c, f) for c in [*view.plan.clocks, *view.family_clocks] for f in ("label", "notes")]
                   + [(d, f) for d in view.plan.documents for f in ("name", "note")]
                   + [(view.plan, "must_not_be_asked"), (view.plan, "contested"), (view, "procedures"),
                      (view, "needed_by"), (view.claim, "title")])
    return view
