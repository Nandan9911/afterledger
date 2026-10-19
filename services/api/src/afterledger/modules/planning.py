"""Module 6, for one family: claims and their plans.

Holdings are grouped into claims (one per institution and route). Each claim gets the procedure
plan for its situation signature - country, institution, kind of holding - from shared knowledge,
or from the research service when none is stored or it has gone stale. The plan runs on the
case's facts (derived where code can, asked where it cannot), the heirship analyst says who must
sign, and the explainer puts it in plain words. The family answers questions and approves the
plan (checkpoint C5).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from ..agents.runner import call_agent
from ..domain.case import CasePlan, Claim, DocumentView, StepView
from ..domain.ids import stable_id
from ..domain.money import format_amount
from ..domain.plan_language import ProcedurePlan
from ..domain.plan_runtime import documents_for, route, run
from . import plan_facts, rules_research
from .common import case_block, knowledge, open_checkpoint, pack_key, snapshot
from .packs import _have
from .rules_research import is_interim, signature

MODULE = "planning"
LEAD_KINDS = {"life_cover": "government_cover", "accident_cover": "accident_cover", "mutual_fund": "mutual_fund",
              "locker": "locker", "shares": "shares_physical", "pension": "family_pension", "loan": "loan",
              "premium_bonds": "premium_bonds", "social_security": "social_security", "employer": "employer_benefit",
              "cpf": "cpf", "chit_fund": "chit_fund", "gold": "locker", "card_loan": "card_loan",
              "credit_card": "credit_card", "state_pension": "social_security", "bank_account": "savings",
              "salary": "salary", "employer_benefit": "employer_benefit"}


def holding_kind_of(lead: Any) -> str | None:
    """The kind of holding a lead would be on the money map, in the money map's own words: a live model's holding_kind
    that is not one of them (on 15 October one stopped every later step of the Vermas' story) gives way to the
    lead's category."""
    from typing import get_args

    from ..domain.case import HoldingKind
    allowed = set(get_args(HoldingKind))
    for k in (lead.holding_kind, LEAD_KINDS.get(lead.category), lead.category):
        if k in allowed:
            return str(k)
    return None


async def find_plan(deps: Any, country: str, kind: str, institution_id: str | None) -> ProcedurePlan | None:
    """The plan for a situation signature: the institution's own plan first, then the country's."""
    plans = await knowledge(deps).plans()
    cands = [p for p in plans if kind in p.holding_kinds and p.jurisdiction.split("-")[0] == country.split("-")[0]
             and not p.authority           # a procedure (a court, a registry) is reached through the claims that need it
             and not is_interim(p.key)]
    if institution_id:
        own = [p for p in cands if p.key.endswith("@" + institution_id)]
        if own:
            return own[0]
    generic = [p for p in cands if "@" not in p.key]
    return generic[0] if generic else None


async def holdings_from_leads(deps: Any, case_id: str) -> None:
    """Confirmed leads (and open ones with a known institution) become holdings on the money map."""
    snap = await snapshot(deps, case_id, ["leads", "holdings"])
    existing = {h.id: h for h in snap.holdings}
    k = knowledge(deps)
    from ..domain.case import Holding
    out = []
    for lead in snap.leads:
        if lead.category.startswith("__") or (lead.status == "closed" and lead.holding_id is None):
            continue
        kind = holding_kind_of(lead)
        if kind is None or lead.kind in ("stop_payment", "refund"):
            continue
        if lead.holding_id is None:       # a paper may already show what the lead points to
            on_paper = next((x for x in snap.holdings if (x.paper_ids or x.source == "family")
                             and x.institution_id == lead.institution_id and x.kind == kind), None)
            if on_paper is not None:
                lead.holding_id = on_paper.id
        hid = lead.holding_id or stable_id("h", case_id, "lead", lead.id)
        inst = await k.institution(lead.institution_id) if lead.institution_id else None
        h = existing.get(hid) or Holding(id=hid, case_id=case_id, institution_id=lead.institution_id, kind=kind,  # type: ignore[arg-type]
                                         label=lead.title, lead_id=lead.id, status="lead", source="lead")
        h.lead_id = lead.id
        if h.paper_ids or h.source == "family":   # a paper shows it, or the family said so: that stands
            lead.holding_id = hid
            if lead.status == "open":
                lead.status = "confirmed"
            out.append(h)
            continue
        h.direction = "debt" if lead.kind == "debt" else "entitlement" if lead.kind == "entitlement" else "asset"
        h.institution_id = lead.institution_id or h.institution_id     # a later run of discovery may name it
        h.country = inst.country if inst else h.country
        h.currency = {"IN": "INR", "JP": "JPY", "GB": "GBP", "US": "USD", "SG": "SGD"}.get(h.country or "", lead.currency or "INR")
        if lead.value is not None:
            h.value = lead.value
        h.status = "closed" if lead.status == "closed" else "confirmed" if lead.status == "confirmed" else "lead"
        if h.direction == "entitlement" and h.kind in ("government_cover",) and lead.status == "confirmed":
            h.nominee = "yes" if h.nominee == "unknown" else h.nominee
        h.evidence = lead.evidence[:2]
        lead.holding_id = hid
        out.append(h)
    await deps.cases.put_many(case_id, "holdings", out)
    await deps.cases.put_many(case_id, "leads", [x for x in snap.leads if x.holding_id])


async def build_claims(deps: Any, case_id: str) -> list[Claim]:
    await holdings_from_leads(deps, case_id)
    snap = await snapshot(deps, case_id, ["holdings", "claims", "leads"])
    k = knowledge(deps)
    groups: dict[tuple, list] = {}
    for h in snap.holdings:
        if h.status == "closed" or not h.institution_id:
            continue
        lead = snap.lead(h.lead_id) if h.lead_id else None
        if h.status == "lead" and lead is not None and lead.label in ("possible", "unlikely"):
            continue                      # too uncertain to plan a claim for; the lead asks the family first
        inst = await k.institution(h.institution_id)
        country = h.country or (inst.country if inst else None) or (snap.case.country_codes or ["IN"])[0]
        plan = await find_plan(deps, country, h.kind, h.institution_id)
        # no stored plan: the claim is still made, starts on the interim plan and has its rules researched
        plan_key = plan.key if plan else f"research:{signature(country, h.kind)}"
        nominee_key = "yes" if h.nominee == "yes" else "no"
        # a holding stays in the claim it joined, even when a later paper changes its nominee
        if h.claim_id and any(c.id == h.claim_id for c in snap.claims):
            cid = h.claim_id
        else:
            cid = stable_id("claim", case_id, h.institution_id, plan_key, nominee_key)
        groups.setdefault((cid, h.institution_id, plan_key), []).append(h)
    existing = {c.id: c for c in snap.claims}
    claims = []
    for (cid, inst_id, plan_key), hs in groups.items():
        inst = await k.institution(inst_id)
        c = existing.get(cid) or Claim(id=cid, case_id=case_id, institution_id=inst_id,
                                       title=claim_title(inst, inst_id, [h.label for h in hs]))
        c.holding_ids = sorted({h.id for h in hs} | set(c.holding_ids))
        if all(h.direction == "debt" for h in hs):
            c.kind = "debt"
        named = {h.nominee_person_id for h in hs if h.nominee_person_id}
        if len(named) == 1 and all(h.nominee == "yes" for h in hs):
            c.claimant_person_id = named.pop()   # the person the policy or account names claims it
        held = [h for h in hs if h.direction != "debt"] or hs
        total = [h.value for h in held if h.value is not None]
        c.amount_due = sum(total, Decimal(0)) if total and len(total) == len(held) else c.amount_due
        c.currency = hs[0].currency
        c.country = inst.country if inst else None
        for h in hs:
            h.claim_id = cid
        claims.append(c)
        c.next_action = c.next_action or "Plan the claim"
        if cid not in existing:
            await deps.activity(case_id, MODULE, f"New claim: {c.title}.", kind="step")
        if not plan_key.startswith("research:"):
            deps.extras.setdefault("claim_plan_key", {})[cid] = plan_key
    await deps.cases.put_many(case_id, "claims", claims)
    await deps.cases.put_many(case_id, "holdings", [h for hs in groups.values() for h in hs])
    return claims


_KEEP_CAPITAL = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                 "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


def _lower_first(label: str) -> str:
    """'Savings account' -> 'savings account', but 'PMJJBY life cover', 'September salary' and names stay, as does a
    name in title case ('Pradhan Mantri Jeevan Jyoti Bima Yojana')."""
    first = label.split(" ", 1)[0].lower().strip(",.")
    if first in _KEEP_CAPITAL:
        return label
    words = [w for w in label.split()[:3] if w[:1].isalpha()]
    if len(words) >= 2 and all(w[0].isupper() for w in words):
        return label
    if len(label) > 1 and label[0].isupper() and (label[1].islower() or label[1] == " "):
        return label[0].lower() + label[1:]
    return label


def claim_title(inst: Any, inst_id: str, labels: list[str]) -> str:
    import re
    name = (inst.short_name or inst.name) if inst else inst_id
    if len(labels) == 1 and labels[0].startswith(name):
        return labels[0]
    # the longer name first, so that a full name goes whole rather than as its short name and a word left over
    names = sorted({n for n in {name, inst.name if inst else None} if n}, key=len, reverse=True)
    for n in names:
        # "... from Straits Marine Engineering": capitalised words that run on from the name are part of it (a live
        # lead's title once left "final salary and entitlements Engineering")
        labels = [re.sub(rf"\s+(?:at|with|from|via)\s+{re.escape(n)}\b(?:\s+[A-Z][\w.&'-]*)*", "", x) for x in labels]
        # "RIVERWALK LOGISTICS final pay": the name again at the start, in any case
        labels = [re.sub(rf"^{re.escape(n)}\s+(?=\S)", "", x, flags=re.IGNORECASE) for x in labels]
    labels = [re.sub(r"^(?:A|An|The)\s+", "", x) for x in labels]
    parts = [_lower_first(x) for x in labels]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"{name}: {joined}"


async def plan_key_for_claim(deps: Any, snap: Any, claim: Claim) -> str | None:
    """The stored plan this claim follows: a procedure's own, the one it was planned on, or the one its situation now
    finds. A claim on the interim plan moves to a stored plan as soon as research has stored one."""
    if claim.plan_key:
        return claim.plan_key
    if claim.plan_id:
        plan = next((p for p in snap.plans if p.id == claim.plan_id), None)
        if plan and not is_interim(plan.plan_key):
            return plan.plan_key
    h = next((x for x in snap.holdings if x.id in claim.holding_ids), None)
    if h is None:
        return None
    plan = await find_plan(deps, claim.country or h.country or "IN", h.kind, claim.institution_id)
    return plan.key if plan else None


def _ctx(snap: Any, raw_inst: dict | None, today: date) -> dict:
    birth = next((v.value for v in snap.values if v.field == "birth_date" and v.value and
                  any(p.id == v.paper_id and p.doc_type in ("insurance_policy", "death_certificate") for p in snap.papers)),
                 None)
    death_country = None
    if snap.case.situation and (snap.case.situation.death_country or snap.case.situation.country_codes):
        death_country = snap.case.situation.death_country or snap.case.situation.country_codes[0]
    dec = snap.deceased
    if dec and dec.death_place and snap.case.died_in:
        death_country = death_country
    return {"institution_raw": raw_inst or {}, "today": today, "death_country": death_country,
            "deceased_birth_date": date.fromisoformat(birth) if birth else None}


async def _checked_again(deps: Any, case: Any, claim: Claim, key: str) -> str | None:
    """The decision controller's research depth for a stored plan (AL_DECISIONS=controller). A claim moves money, so
    it is tier A: a plan resting on two verified sources and fresh rules is used as stored (recall); one resting on a
    single source, or with a rule past its check date, is researched again before use (standard, or deep when it cites
    few rules). Offline there is nothing new to find (the page snapshots are what the plan was built from), so the
    decision is recorded and the stored plan is used. Returns the refreshed plan's key, or None to use the stored one."""
    if getattr(deps.settings, "decisions", "controller") != "controller":
        return None
    from ..domain.decision import research_depth
    from ..domain.ids import new_id
    k = knowledge(deps)
    plan = await k.plan(key)
    if plan is None:
        return None
    today = deps.today_for(case)
    cites = sorted(plan.all_cites())
    rules = [a for a in [await k.assertion(c) for c in cites] if a is not None]
    fresh = bool(rules) and all(k.is_fresh(a, today) for a in rules)
    depth = research_depth("A", plan.level, fresh, thin_coverage=len(rules) < 3)
    await deps.trace.record({"type": "decision", "id": new_id("dc"), "case_id": case.id, "module": MODULE,
                             "subject": f"plan:{key}", "move": "recall" if depth == "recall" else "research",
                             "depth": depth, "level": plan.level, "fresh": fresh, "rules": len(rules)})
    if depth == "recall" or deps.models.mode == "scripted":
        return None
    # until 19 October 2026 this research ran there and then, holding the case for up to half an hour; now the stored
    # plan is used at once and its rules are researched in the background, and the claim moves to the result
    why = "rules past their check date" if not fresh else "few official sources" if plan.unverified or not rules \
        else "a single official source"
    h = await deps.cases.get(case.id, "holdings", claim.holding_ids[0]) if claim.holding_ids else None
    state = await rules_research.ensure(deps, case.id, claim, country=claim.country or plan.jurisdiction,
                                        kind=h.kind if h else None, reason="recheck",
                                        plan_key=key if plan.authority else None, authority=plan.authority)
    if state.get("status") == "running" and (claim.research or {}).get("run_id") != state.get("run_id"):
        claim.research = state
        await deps.cases.put(case.id, "claims", claim)
        await deps.activity(case.id, MODULE, f"The stored plan for {claim.title} rests on {why}, so its rules are being "
                            "researched again in the background; the plan is used meanwhile.", kind="research",
                            run_id=state.get("run_id"))
    return None


async def plan_claim(deps: Any, case_id: str, claim_id: str, *, answers: dict | None = None) -> CasePlan | None:
    snap = await snapshot(deps, case_id)
    case = snap.case
    claim = snap.claim(claim_id)
    if claim is None:
        return None
    k = knowledge(deps)
    key = await plan_key_for_claim(deps, snap, claim)
    if key is None:
        # no stored plan fits: the claim starts on the interim plan at once, and its rules are researched now, in
        # the background (until 19 October 2026 research ran here and the case waited, or the claim was never made)
        h = next((x for x in snap.holdings if x.id in claim.holding_ids), None)
        state = await rules_research.ensure(deps, case_id, claim, country=claim.country or (h.country if h else None)
                                            or (case.country_codes or ["XX"])[0], kind=h.kind if h else None)
        if (claim.research or {}).get("run_id") != state.get("run_id") and state.get("status") == "running":
            await deps.activity(case_id, MODULE, f"No stored plan covers {claim.title} yet, so its rules are being "
                                "researched now; until then the plan is to tell the institution in writing and ask how "
                                "to claim.", kind="research", run_id=state.get("run_id"))
        claim.research = state
        key = rules_research.INTERIM_DEBT if claim.kind == "debt" else rules_research.INTERIM_CLAIM
    elif snap.plan_for_claim(claim_id) is None:      # the first time this claim is planned
        key = await _checked_again(deps, case, claim, key) or key
    plan = await k.plan(key)
    if plan is None:
        return None
    raw_inst = await k.institution_raw(claim.institution_id)
    ctx = _ctx(snap, raw_inst, deps.today_for(case))
    prev = snap.plan_for_claim(claim_id)
    facts: dict[str, Any] = {}
    for v in plan.variables:
        if v.derive:
            facts[v.name] = plan_facts.derive(v.derive, snap, claim, ctx)
    if prev:
        for name, val in prev.facts.items():
            if name.startswith("__") or val in (None, "unknown"):
                continue
            # an earlier answer stands until the papers settle the question themselves
            if facts.get(name) in (None, "unknown"):
                facts[name] = val
                if prev.facts.get(f"__asked__{name}"):
                    facts[f"__asked__{name}"] = True
            elif prev.facts.get(f"__asked__{name}") and facts.get(name) != val:
                await deps.ledger(case_id, f"plan:{claim_id}:{name}", "evidence_over_answer", "pass",
                                  detail=f"the family said {val}; the papers now show {facts[name]}")
    for name, val in (answers or {}).items():
        facts[name] = val
        facts[f"__asked__{name}"] = True
    result = run(plan, facts)
    linked = {v.name for v in plan.variables if (v.derive or "").startswith("plan_answer:")}
    if linked:                            # answered in the plan that owns the decision, not asked twice
        keep = [i for i, v in enumerate(result.question_vars) if v not in linked]
        result.questions = [result.questions[i] for i in keep]
        result.question_vars = [result.question_vars[i] for i in keep]
    route_steps = route(plan, facts)
    pid = stable_id("plan", case_id, claim_id)
    steps: list[StepView] = []
    prev_status = {s.id: s.status for s in prev.steps} if prev else {}
    for s in plan.steps:
        if s.id in result.skipped:
            continue
        status = "blocked" if s.id in result.blocked else prev_status.get(s.id, "todo")
        if status in ("skipped", "blocked") and s.id not in result.blocked:
            status = "todo"               # an answer has unblocked it
        if s.kind == "decide" and s.decides and facts.get(s.decides) not in (None, "unknown"):
            status = "done"               # the decision is made: the family answered it
        steps.append(StepView(id=s.id, text=s.text, status=status, cites=s.cites,  # type: ignore[arg-type]
                              must_not_be_asked=s.must_not_be_asked,
                              note=("Waiting for: " + ", ".join(sorted(result.blocked[s.id]))) if s.id in result.blocked else ""))
    docs: list[DocumentView] = []
    seen = set()
    for sid in route_steps:
        step = plan.step(sid)
        for doc, verdict, alts in documents_for(plan, step, facts):
            if doc.id in seen:
                continue
            seen.add(doc.id)
            have = _have(doc.id, {p.doc_type for p in snap.papers})
            docs.append(DocumentView(id=doc.id, name=doc.name + (" (or: " + ", ".join(
                plan.document(a).name for a in alts) + ")" if alts else ""), certification=list(doc.certification),
                cites=doc.cites, status="have" if have else "unknown" if verdict == "unknown" else "missing",
                note=doc.note))
    level = plan.level
    gold = None
    if (claim.country or "").startswith("IN") and key.startswith("IN/"):
        gold = level == "gold"
    verified_label = {"gold": "Hand-checked gold plan", "dual_verified": "Verified: two researchers agree",
                      "adjudicated": "Verified after a conflict was settled", "sourced": "One official source",
                      "draft": "Not verified yet"}.get(level, level)
    if plan.unverified:
        verified_label = "Not verified: the plan starts by asking the institution"
    interim = is_interim(key)
    research = claim.research or {}
    if interim:
        verified_label = ("For now: the rules are being researched" if research.get("status") == "running" else
                          "For now: research found no official rules yet" if research.get("status") in (
                              "inconclusive", "failed", "done") else "For now: ask the institution in writing")
    must_not = sorted({m for sid in route_steps for m in plan.step(sid).must_not_be_asked})
    cp = CasePlan(id=pid, case_id=case_id, claim_id=claim_id, plan_key=key, title=plan.title,
                  institution_id=claim.institution_id, jurisdiction=plan.jurisdiction, level=level,
                  verified_label=verified_label, gold_match=gold, facts=facts,
                  questions=result.questions, steps=steps, documents=docs, must_not_be_asked=must_not,
                  # an approval holds for the plan approved: a plan that replaced it (researched rules for an interim
                  # plan) is approved again
                  contested=plan.contested, approved_by=prev.approved_by if prev and prev.plan_key == key else None,
                  approved_on=prev.approved_on if prev and prev.plan_key == key else None,
                  research_label=("researching now" if research.get("status") == "running" else "") if interim else
                  f"{'researched' if plan.unverified or level == 'draft' else 'verified'} on "
                  f"{plan.verified_on.day} {plan.verified_on.strftime('%B %Y')}"
                  if plan.verified_on else "researched just now", verified_on=None if interim else plan.verified_on,
                  unverified=bool(plan.unverified))
    viewer = snap.person(case.viewer_person_id)
    lang = (viewer.languages[0] if viewer and viewer.languages else "en")
    claimant = snap.claimant(claim)
    if lang == "en" and claimant and claimant.languages and claimant.languages[0] != "en":
        lang = claimant.languages[0]      # the person who signs reads the plan in their own language
    expl = await call_agent(deps, "explainer", {
        "key": pack_key("explainer", case, claim_id, str(len(route_steps)), ",".join(route_steps)),
        "case": case_block(case), "claim": {"id": claim.id, "title": claim.title,
                                             "amount": str(claim.amount_due) if claim.amount_due else None,
                                             "currency": claim.currency},
        "plan": {"key": key, "title": plan.title, "route": route_steps, "questions": result.questions,
                 "steps": [{"id": s.id, "text": s.text} for s in steps]}, "lang": lang,
        "facts": [{"name": v.name, "asks": v.question or v.name.replace("_", " "), "value": facts.get(v.name)}
                  for v in plan.variables if facts.get(v.name) not in (None, "unknown")],
        "claimant": claimant.display_name if claimant else None,
        "people": [{"name": p.display_name, "relationship": p.relationship, "roles": p.roles} for p in snap.family()]},
        case_id=case_id)
    cp.summary = expl.summary
    cp.summary_local = expl.summary_local if lang != "en" else ""
    cp.lang_local = lang if lang != "en" and (expl.summary_local or any(e.text_local for e in expl.steps)) else ""
    plan_ids = {s.id for s in cp.steps}
    known = {a.id for a in await k.assertions()}
    cp.explained = [{"text": e.text, "text_local": e.text_local if lang != "en" else "", "who": e.who,
                     "step_id": e.step_id if e.step_id in plan_ids else None,
                     "cites": plan.step(e.step_id).cites if e.step_id in plan_ids else
                     [c for c in e.cites if c in known]} for e in expl.steps]
    if not route_steps and result.questions:
        cp.summary = cp.summary or "The route depends on a few answers; the plan covers every answer until then."
    claim.plan_id = pid
    if not interim and claim.status == "submitted" and not claim.submitted_on and "notified" in claim.events:
        # the family wrote under the interim plan; the researched plan's own claim is still to be made
        claim.status, claim.next_action = "planning", "Approve the researched plan"
    claim.next_action = ("Answer " + str(len(result.questions)) + " question" + ("s" if len(result.questions) != 1 else "")
                         if result.questions else claim.next_action)
    await deps.cases.put(case_id, "plans", cp)
    await deps.cases.put(case_id, "claims", claim)
    await _clocks(deps, case_id, claim, plan, cp)
    for c in await deps.cases.list(case_id, "checkpoints"):
        # a question settled since it was asked (by another answer, a paper, or the situation) closes
        if c.kind == "plan_question" and c.status == "open" and c.subject_ref and \
                c.subject_ref.startswith(f"plan:{pid}:") and c.subject_ref.split(":")[-1] not in result.question_vars:
            var = c.subject_ref.split(":")[-1]
            c.status, c.answer = "answered", str(facts.get(var, ""))
            c.answer_note, c.answered_at = "Settled by another answer or by the papers.", deps.now()
            await deps.cases.put(case_id, "checkpoints", c)
    if claim.status == "closed":
        pass                              # nothing to ask or approve on a claim that has nothing to claim
    elif result.questions:
        for var in result.question_vars:
            v = plan.variable(var)
            await open_checkpoint(deps, case, code="C5", kind="plan_question", title=v.question or var,
                                  question=v.question or var, options=_options(v), subject_ref=f"plan:{pid}:{var}",
                                  why=f"The plan for {claim.title} depends on it.",
                                  payload={"claim_id": claim_id, "variable": var, "plan_key": key})
    elif not cp.approved_by:
        if prev is not None and prev.plan_key != key:     # a new plan replaced the one approved: asked again
            for c in await deps.cases.list(case_id, "checkpoints"):
                if c.subject_ref == f"plan:{pid}:approve" and c.status == "answered":
                    c.status, c.answer, c.answered_by, c.answered_at = "open", None, None, None
                    await deps.cases.put(case_id, "checkpoints", c)
        await open_checkpoint(deps, case, code="C5", kind="plan_approval", title=f"Approve the plan: {claim.title}",
                              question="Does this plan look right to you?",
                              options=[("approve", "Approve"), ("question", "I have a question")],
                              subject_ref=f"plan:{pid}:approve", why=cp.summary, payload={"claim_id": claim_id})
    if not prev:
        await deps.activity(case_id, MODULE, f"Plan ready for {claim.title}: {verified_label.lower()}.",
                            agent="procedure_compiler" if level != "gold" else None, kind="step", plan_key=key)
    deps.changed(case_id, "plans", "claims", "checkpoints")
    return cp


def _options(v: Any) -> list[tuple[str, str]]:
    if v.type == "tri":
        return [("yes", "Yes"), ("no", "No"), ("unknown", "We don't know")]
    if v.type == "choice":
        return [(o, o.replace("_", " ").capitalize()) for o in v.options]
    return [("answer", "Answer")]


async def _clocks(deps: Any, case_id: str, claim: Claim, plan: ProcedurePlan, cp: CasePlan) -> None:
    from .followup import compute_clocks
    cp.clocks = await compute_clocks(deps, case_id, claim, plan, cp.facts)
    claim.clocks = cp.clocks
    await deps.cases.put(case_id, "plans", cp)
    await deps.cases.put(case_id, "claims", claim)


SITUATION_FACTS = {"situation_will": "will", "situation_dispute": "dispute"}
"""Answers about the family's situation rather than one claim: given once, they count for every plan."""


async def answer_question(deps: Any, case_id: str, plan_id: str, variable: str, value: Any, *, by: str | None) -> CasePlan | None:
    cp: CasePlan = await deps.cases.get(case_id, "plans", plan_id)
    for c in await deps.cases.list(case_id, "checkpoints"):
        if c.subject_ref == f"plan:{plan_id}:{variable}" and c.status == "open":
            c.status, c.answer, c.answered_by, c.answered_at = "answered", str(value), by, deps.now()
            await deps.cases.put(case_id, "checkpoints", c)
    plan = await knowledge(deps).plan(cp.plan_key)
    var = plan.variable(variable) if plan else None
    field = SITUATION_FACTS.get(var.derive or "") if var else None
    if var is not None and (var.derive or "").startswith("situation_cause:") and value == "yes":
        field, value_for_case = "death_cause", var.derive.split(":", 1)[1]
    else:
        value_for_case = value
    person = await deps.cases.get(case_id, "people", by) if by else None
    q = (var.question if var and var.question else variable.replace("_", " ")).rstrip("?")
    label = dict(_options(var)).get(str(value), str(value)) if var is not None else str(value)
    await deps.activity(case_id, MODULE, f"{person.display_name.split()[0] if person else 'The family'} answered "
                        f"“{q}?”: {label.replace('_', ' ')}.", kind="decision")
    out = await plan_claim(deps, case_id, cp.claim_id, answers={variable: value})
    await sync_procedures(deps, case_id)
    if (field and value in ("yes", "no")) or field == "death_cause":
        from ..domain.case import SituationModel
        case = await deps.cases.get_case(case_id)
        sit = case.situation or SituationModel()
        if getattr(sit, field, None) != value_for_case:
            setattr(sit, field, value_for_case)
            case.situation = sit
            await deps.cases.put_case(case)
            for other in await deps.cases.list(case_id, "claims"):
                if other.id != cp.claim_id and other.plan_id and other.status != "closed":
                    await plan_claim(deps, case_id, other.id)
    return out


async def approve_plan(deps: Any, case_id: str, plan_id: str, *, by: str | None) -> CasePlan:
    cp: CasePlan = await deps.cases.get(case_id, "plans", plan_id)
    cp.approved_by, cp.approved_on = by, deps.today_for(await deps.cases.get_case(case_id))
    await deps.cases.put(case_id, "plans", cp)
    for c in await deps.cases.list(case_id, "checkpoints"):
        if c.subject_ref == f"plan:{plan_id}:approve" and c.status == "open":
            c.status, c.answer, c.answered_by, c.answered_at = "answered", "approve", by, deps.now()
            await deps.cases.put(case_id, "checkpoints", c)
    person = await deps.cases.get(case_id, "people", by) if by else None
    claim = await deps.cases.get(case_id, "claims", cp.claim_id) if cp.claim_id else None
    await deps.activity(case_id, MODULE, f"{person.display_name if person else 'The family'} approved the plan: "
                        f"{claim.title if claim else cp.title}.", kind="decision")
    deps.changed(case_id, "plans", "checkpoints")
    return cp


async def ensure_procedures(deps: Any, case_id: str) -> list[Claim]:
    """Procedures other claims depend on become claims of their own, with their own plan: a paper a
    route needs that has a plan for getting it (a court-approved affidavit, an heir information
    certificate, a certified foreign death certificate), and a decision another plan waits for
    (Japan's three-month choice to accept or renounce)."""
    snap = await snapshot(deps, case_id, ["claims", "plans", "papers"])
    k = knowledge(deps)
    wanted: dict[str, set[str]] = {}
    for cp in snap.plans:
        claim = snap.claim(cp.claim_id)
        if claim is None or claim.status == "closed":
            continue
        plan = await k.plan(cp.plan_key)
        if plan is None:
            continue
        facts = {kk: v for kk, v in cp.facts.items() if not kk.startswith("__")}
        active = run(plan, facts).active
        for sid in route(plan, facts) + [s for s in active if plan.step(s).kind == "acquire"]:
            step = plan.step(sid)
            for doc, verdict, alts in documents_for(plan, step, facts):
                if alts or verdict == "unknown":
                    continue              # the family has not chosen which paper yet
                had = next((d for d in cp.documents if d.id == doc.id and d.status == "have"), None)
                if doc.acquire_plan and not had:
                    wanted.setdefault(doc.acquire_plan, set()).add(claim.id)
        for v in plan.variables:
            if (v.derive or "").startswith("plan_answer:"):
                wanted.setdefault(v.derive.split(":")[1], set()).add(claim.id)
    made: list[Claim] = []
    for c in snap.claims:                 # a procedure nothing needs any more is closed, with the reason
        if c.kind == "procedure" and c.plan_key not in wanted and c.status not in ("closed", "settled") \
                and not c.events and not c.submitted_on and not c.started_by:
            c.status, c.closed_reason, c.needed_by = "closed", "No claim needs it any more.", []
            await deps.cases.put(case_id, "claims", c)
    for key, needed_by in wanted.items():
        plan = await k.plan(key)
        if plan is None:
            continue
        cid = stable_id("claim", case_id, "procedure", key)
        c = snap.claim(cid) or Claim(id=cid, case_id=case_id, institution_id=plan.authority or "", kind="procedure",
                                     plan_key=key, title=plan.title, next_action="Plan the procedure")
        c.needed_by = sorted(needed_by)
        if c.status == "closed" and c.closed_reason == "No claim needs it any more.":
            c.status, c.closed_reason = "planning", ""
        c.country = plan.jurisdiction
        if snap.claim(cid) is None:
            await deps.activity(case_id, MODULE, f"Needed first: {plan.title.lower()}, for "
                                f"{len(needed_by)} claim{'s' if len(needed_by) != 1 else ''}.", kind="step")
        await deps.cases.put(case_id, "claims", c)
        made.append(c)
    return made


async def start_procedure(deps: Any, case_id: str, plan_key: str, *, by: str | None = None) -> Claim | None:
    """The family starts a procedure from a plan before any claim strictly needs it (applying for
    Japan's heir information certificate early, say), so it is tracked like any other."""
    k = knowledge(deps)
    plan = await k.plan(plan_key)
    if plan is None:
        return None
    cid = stable_id("claim", case_id, "procedure", plan_key)
    c = await deps.cases.get(case_id, "claims", cid) or Claim(
        id=cid, case_id=case_id, institution_id=plan.authority or "", kind="procedure", plan_key=plan_key,
        title=plan.title, next_action="Plan the procedure")
    c.country = plan.jurisdiction
    users = []
    for other in await deps.cases.list(case_id, "claims"):
        cp = await deps.cases.get(case_id, "plans", other.plan_id) if other.plan_id else None
        other_plan = await k.plan(cp.plan_key) if cp else None
        if other_plan and any(d.acquire_plan == plan_key for d in other_plan.documents):
            users.append(other.id)
    c.needed_by = sorted(set(c.needed_by) | set(users))
    c.started_by = c.started_by or by or "family"
    if c.status == "closed":
        c.status, c.closed_reason = "planning", ""
    await deps.cases.put(case_id, "claims", c)
    await plan_claim(deps, case_id, cid)
    person = await deps.cases.get(case_id, "people", by) if by else None
    await deps.activity(case_id, MODULE, f"{person.display_name.split()[0] if person else 'The family'} started: "
                        f"{plan.title.lower()}.", kind="decision")
    return await deps.cases.get(case_id, "claims", cid)


async def sync_procedures(deps: Any, case_id: str) -> None:
    """After an answer changes a route, the procedures it needs are created and planned at once."""
    before = {c.id for c in await deps.cases.list(case_id, "claims") if c.kind == "procedure" and c.status != "closed"}
    for proc in await ensure_procedures(deps, case_id):
        if proc.id not in before:
            await plan_claim(deps, case_id, proc.id)


def amount_text(amount: Decimal | None, currency: str) -> str:
    return format_amount(amount, currency) if amount is not None else "an amount not known yet"
