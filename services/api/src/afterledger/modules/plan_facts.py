"""Derivations: facts code can work out for a plan's variables, so the family is asked only what
no paper or earlier answer settles. Each plan variable may name one with 'derive: name[:argument]'.
A derivation returns 'unknown' (or None) when the case does not settle it."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from .common import Snapshot

Derivation = Callable[[Snapshot, Any, str, dict], Any]
DERIVATIONS: dict[str, Derivation] = {}


def derivation(name: str) -> Callable[[Derivation], Derivation]:
    def wrap(fn: Derivation) -> Derivation:
        DERIVATIONS[name] = fn
        return fn
    return wrap


def _claim_holdings(snap: Snapshot, claim: Any) -> list:
    return [h for h in snap.holdings if h.id in (claim.holding_ids if claim else [])]


@derivation("holding_nominee")
def holding_nominee(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    vals = {h.nominee for h in _claim_holdings(snap, claim)}
    if vals == {"yes"}:
        return "yes"
    if vals and vals <= {"no"}:
        return "no"
    return "unknown"


@derivation("claim_amount")
def claim_amount(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    """What the institution holds for the estate; a card balance at the same bank is owed, not held."""
    hs = [h for h in _claim_holdings(snap, claim) if h.direction != "debt"]
    if not hs or any(h.value is None for h in hs):
        return "unknown"
    return float(sum((h.value for h in hs), Decimal(0)))


@derivation("institution")
def institution_attr(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    raw = ctx.get("institution_raw") or {}
    return raw.get(arg, "unknown")


@derivation("situation_will")
def situation_will(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    if any(p.doc_type == "will" for p in snap.papers):
        return "yes"
    return snap.case.situation.will if snap.case.situation else "unknown"


@derivation("situation_dispute")
def situation_dispute(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    return snap.case.situation.dispute if snap.case.situation else "unknown"


@derivation("heirs_not_claiming")
def heirs_not_claiming(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    heirs = [p for p in snap.family() if "heir" in p.roles]
    claimants = [p for p in heirs if "claimant" in p.roles]
    if not heirs:
        return "unknown"
    return len(heirs) - max(1, len(claimants))


@derivation("paper_choice")
def paper_choice(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    kinds = {p.doc_type for p in snap.papers}
    return arg if arg in kinds else "unknown"


@derivation("death_outside")
def death_outside(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    country = ctx.get("death_country")
    if not country:
        return "unknown"
    return "yes" if country.split("-")[0] != arg else "no"


@derivation("death_country")
def death_country(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    return ctx.get("death_country") or "unknown"


@derivation("claim_has_kind")
def claim_has_kind(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    return "yes" if any(h.kind == arg for h in _claim_holdings(snap, claim)) else "no"


@derivation("holding_joint_survivor")
def holding_joint_survivor(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    hs = _claim_holdings(snap, claim)
    if any(len(h.holders) > 1 for h in hs):
        return "unknown"
    vals = {v.value.upper() for v in snap.values if v.field == "holding_mode" and v.value}
    if vals and all("SINGLE" in v for v in vals):
        return "no"
    return "unknown"


@derivation("claimant_relationship_in")
def claimant_relationship_in(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    claimant = next((p for p in snap.family() if "claimant" in p.roles), None)
    if claimant is None or not claimant.relationship:
        return "unknown"
    return "yes" if claimant.relationship in arg.split(",") else "no"


@derivation("paper_present")
def paper_present(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    kinds = {p.doc_type for p in snap.papers}
    return "yes" if kinds & set(arg.split(",")) else "no"


@derivation("holding_institution_known")
def holding_institution_known(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    """Whether we know who actually holds the money. A registrar collecting for funds it does not name
    (a SIP debit to CAMS, say) is not the fund: that stays unknown until a statement names it."""
    hs = _claim_holdings(snap, claim)
    if any(h.status == "lead" for h in hs):
        return "no"
    inst = ctx.get("institution_raw") or {}
    if inst.get("kind") == "registrar":
        named = any(p.institution_id == inst.get("id") and p.doc_type in ("fund_statement", "cas_statement")
                    for p in snap.papers)
        return "yes" if named else "no"
    return "yes"


@derivation("claim_flag")
def claim_flag(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    """A flag the institution raises on a claim, such as an investigation. Once the claim is in and the
    institution has not raised it, the answer is no; it turns yes when the event is recorded."""
    if claim is None:
        return "unknown"
    if arg in claim.events or f"{arg}_started" in claim.events:
        return "yes"
    return "no" if claim.submitted_on else "unknown"


@derivation("lead_evidence")
def lead_evidence(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    cats = {"pmjjby": "life_cover", "pmsby": "accident_cover"}
    want = arg.upper()
    for g in snap.groups:
        if want in g.label.upper() and g.direction == "debit":
            last = g.last
            died = snap.case.died_on
            if died and (died - last).days <= 400:
                return "yes"
    return "unknown" if cats.get(arg) else "no"


@derivation("pmjjby_age_ok")
def pmjjby_age_ok(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    born = ctx.get("deceased_birth_date")
    died = snap.case.died_on
    if not (born and died):
        return "unknown"
    age = died.year - born.year - ((died.month, died.day) < (born.month, born.day))
    return "yes" if age < 55 else "no"


@derivation("situation_cause")
def situation_cause(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    cause = snap.case.situation.death_cause if snap.case.situation else "unknown"
    if not cause or cause == "unknown":
        return "unknown"
    return "yes" if cause == arg else "no"


@derivation("paper_language_is")
def paper_language_is(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    doc_type, _, lang = arg.partition(":")
    papers = [p for p in snap.papers if p.doc_type == doc_type]
    if not papers:
        return "unknown"
    return "yes" if any(lang in p.languages for p in papers) else "no"


@derivation("deceased_state")
def deceased_state(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    return ctx.get("deceased_state") or "unknown"


@derivation("credits_after_settlement")
def credits_after_settlement(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    for c in snap.correspondence:
        if c.direction == "in" and arg in (c.title + " " + c.summary).lower():
            return "yes"
    return "unknown"


@derivation("credits_after_death")
def credits_after_death(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    words = {"pension": ["PENSION"], "social_security": ["SOC SEC", "SSA"]}.get(arg, [arg.upper()])
    died = snap.case.died_on
    if not died:
        return "unknown"
    for t in snap.transactions:
        if t.direction == "credit" and t.date > died and any(w in t.narration.upper() for w in words):
            return "yes"
    for c in snap.correspondence:
        if any(w.lower() in (c.title + " " + c.summary).lower() for w in words) and "after" in c.summary.lower():
            return "yes"
    return "unknown"


@derivation("debts_have_balance_certificates")
def debts_have_balance_certificates(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    debts = [h for h in snap.holdings if h.direction == "debt"]
    if not debts:
        return "unknown"
    return "yes" if all(h.value is not None for h in debts) else "no"


@derivation("heirs_renouncing")
def heirs_renouncing(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    return len([p for p in snap.family() if "heir" in p.roles]) or "unknown"


@derivation("plan_answer")
def plan_answer(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    plan_key, _, var = arg.rpartition(":")
    for p in snap.plans:
        if p.plan_key == plan_key and var in p.facts:
            return p.facts[var]
    return "unknown"


@derivation("heirs_living_outside")
def heirs_living_outside(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    heirs = [p for p in snap.family() if "heir" in p.roles]
    if not heirs or any(not p.country for p in heirs):
        return "unknown"
    return "yes" if any(p.country != arg for p in heirs) else "no"


@derivation("jurisdiction_not_contested")
def jurisdiction_not_contested(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    j = snap.case.jurisdiction
    if j is None:
        return "unknown"
    return "no" if j.contested else "yes"


@derivation("viewer_is_heir")
def viewer_is_heir(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    viewer = snap.person(snap.case.viewer_person_id)
    if viewer is None:
        return "unknown"
    return "yes" if "heir" in viewer.roles else "no"


@derivation("correspondence_requests")
def correspondence_requests(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    want = arg.replace("_", " ")
    for c in snap.correspondence:
        if claim and c.claim_id == claim.id and any(want in r.lower() for r in c.requests):
            return "yes"
    return "no" if claim else "unknown"


@derivation("plan_steps_active")
def plan_steps_active(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    found = False
    for p in snap.plans:
        for s in p.steps:
            if s.id == arg and s.status in ("active", "todo", "done", "late"):
                found = True
    return "yes" if found else "no"


@derivation("spouse_same_household")
def spouse_same_household(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    spouse = next((p for p in snap.family() if p.relationship in ("wife", "husband", "spouse")), None)
    if spouse is None:
        return "unknown"
    return "yes" if spouse.city and snap.case.died_in and spouse.city == snap.case.died_in else "unknown"


@derivation("claimant_language")
def claimant_language(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    claimant = next((p for p in snap.family() if "claimant" in p.roles), None)
    if claimant is None:
        return "unknown"
    return "yes" if claimant.languages and claimant.languages[0].startswith(arg) else "no"


@derivation("estate_value_excluding")
def estate_value_excluding(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    hs = [h for h in snap.holdings if h.direction == "asset" and h.kind != arg and h.status != "closed"]
    if not hs or any(h.value is None for h in hs if h.kind in ("savings", "current", "fixed_deposit")):
        return "unknown"
    return float(sum((h.value for h in hs if h.value is not None and h.kind not in ("life_policy", "employer_benefit")),
                     Decimal(0)))


@derivation("debts_at_institution")
def debts_at_institution(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> str:
    inst = claim.institution_id if claim else None
    return "yes" if any(h.direction == "debt" and h.institution_id == inst for h in snap.holdings) else "no"


@derivation("years_since_death")
def years_since_death(snap: Snapshot, claim: Any, arg: str, ctx: dict) -> Any:
    died = snap.case.died_on
    today: date = ctx.get("today") or date.today()
    if not died:
        return "unknown"
    return round((today - died).days / 365.25, 2)


def derive(name: str, snap: Snapshot, claim: Any, ctx: dict) -> Any:
    fn_name, _, arg = name.partition(":")
    fn = DERIVATIONS.get(fn_name)
    if fn is None:
        return "unknown"
    try:
        return fn(snap, claim, arg, ctx)
    except Exception:  # a derivation must never break planning; it just stays unknown
        return "unknown"
