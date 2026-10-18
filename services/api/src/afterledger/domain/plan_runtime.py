"""Runs a compiled procedure plan on a case. No model calls: this is the exact part."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import holidays as holiday_calendars

from .plan_language import ROUTE_KINDS, Clock, Cond, DocumentReq, ProcedurePlan, Step, Tri

_NUMERIC = {"lt", "le", "gt", "ge"}


def evaluate(cond: Cond | None, facts: dict) -> Tri:
    """Three-valued logic: an unknown fact makes the answer unknown unless it cannot matter."""
    if cond is None:
        return "yes"
    if cond.op in ("all", "any"):
        results = [evaluate(c, facts) for c in cond.args]
        decisive, other = ("no", "yes") if cond.op == "all" else ("yes", "no")
        if decisive in results:
            return decisive
        return "unknown" if "unknown" in results else other
    if cond.op == "not":
        inner = evaluate(cond.args[0], facts)
        return {"yes": "no", "no": "yes", "unknown": "unknown"}[inner]
    value = facts.get(cond.var)
    if value is None or value == "unknown":
        return "unknown"
    if cond.op in _NUMERIC:
        left, right = Decimal(str(value)), Decimal(str(cond.value))
        ok = {"lt": left < right, "le": left <= right,
              "gt": left > right, "ge": left >= right}[cond.op]
    elif cond.op == "in":
        ok = value in cond.value
    else:
        ok = (value == cond.value) if cond.op == "eq" else (value != cond.value)
    return "yes" if ok else "no"


def unknown_vars(cond: Cond | None, facts: dict) -> set[str]:
    """The variables whose answers would settle this condition."""
    if cond is None:
        return set()
    if cond.op in ("all", "any", "not"):
        return set().union(*(unknown_vars(c, facts) for c in cond.args))
    value = facts.get(cond.var)
    return {cond.var} if value is None or value == "unknown" else set()


@dataclass
class PlanRun:
    active: list[str] = field(default_factory=list)       # steps that apply
    blocked: dict[str, set[str]] = field(default_factory=dict)  # step -> unknown variables
    skipped: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)    # in plan order, no repeats
    question_vars: list[str] = field(default_factory=list)  # the variable behind each question


def run(plan: ProcedurePlan, facts: dict) -> PlanRun:
    out, asked = PlanRun(), set()
    by_name = {v.name: v for v in plan.variables}
    for step in plan.steps:
        verdict = evaluate(step.when, facts)
        if verdict == "yes":
            out.active.append(step.id)
        elif verdict == "no":
            out.skipped.append(step.id)
        else:
            missing = unknown_vars(step.when, facts)
            out.blocked[step.id] = missing
            for name in sorted(missing):
                question = by_name[name].question
                if question and name not in asked:
                    asked.add(name)
                    out.questions.append(question)
                    out.question_vars.append(name)
    # a step on the route that takes one of several papers asks which one, if a choice decides it
    for step in plan.steps:
        if step.id not in out.active:
            continue
        for group in step.one_of:
            var = next((v for v in plan.variables if v.type == "choice" and set(group) & set(v.options)), None)
            if var is None or facts.get(var.name) not in (None, "unknown"):
                continue
            if var.question and var.name not in asked:
                asked.add(var.name)
                out.questions.append(var.question)
                out.question_vars.append(var.name)
    return out


def route(plan: ProcedurePlan, facts: dict) -> list[str]:
    """The steps that decide where the claim goes (submissions and hand-offs) for these facts.
    Gold tests compare exactly this list, so information and waiting steps can change freely."""
    result = run(plan, facts)
    kinds = {s.id: s.kind for s in plan.steps}
    return [sid for sid in result.active if kinds[sid] in ROUTE_KINDS]


def documents_for(plan: ProcedurePlan, step: Step, facts: dict) -> list[tuple[DocumentReq, Tri, list[str]]]:
    """The papers a step needs for these facts, as (document, verdict, alternatives).

    Required documents come first. For each set of alternatives the chosen one is returned when a
    choice variable whose options name the set's documents already has an answer; otherwise the
    first is returned with the others listed as alternatives and an 'unknown' verdict.
    """
    out: list[tuple[DocumentReq, Tri, list[str]]] = []
    for doc_id in step.documents:
        doc = plan.document(doc_id)
        if doc is None:
            continue
        verdict = evaluate(doc.when, facts)
        if verdict != "no":
            out.append((doc, verdict, []))
    for group in step.one_of:
        members = [d for d in (plan.document(i) for i in group) if d is not None]
        members = [d for d in members if evaluate(d.when, facts) != "no"]
        if not members:
            continue
        chosen = None
        for var in plan.variables:
            value = facts.get(var.name)
            if var.type == "choice" and value in group and set(group) & set(var.options):
                chosen = next((d for d in members if d.id == value), None)
                break
        if chosen is not None:
            out.append((chosen, evaluate(chosen.when, facts), []))
        else:
            out.append((members[0], "unknown", [d.id for d in members[1:]]))
    return out


def static_check(plan: ProcedurePlan, known_assertions: set[str]) -> list[str]:
    """Errors that stop a compiled plan from being stored. Empty list means it passed."""
    errors: list[str] = []
    variables = {v.name: v for v in plan.variables}
    documents = {d.id for d in plan.documents}
    clocks = {c.id for c in plan.clocks}

    def walk(cond: Cond | None):
        if cond is None:
            return
        if cond.op in ("all", "any", "not"):
            if not cond.args or (cond.op == "not" and len(cond.args) != 1):
                errors.append(f"malformed '{cond.op}' condition")
            for c in cond.args:
                walk(c)
        elif cond.var not in variables:
            errors.append(f"condition uses undeclared variable '{cond.var}'")
        elif not (variables[cond.var].question or variables[cond.var].derive):
            errors.append(f"variable '{cond.var}' can be neither asked nor derived")

    ids = [s.id for s in plan.steps]
    if len(ids) != len(set(ids)):
        errors.append("duplicate step ids")
    if len(documents) != len(plan.documents):
        errors.append("duplicate document ids")
    for step in plan.steps:
        walk(step.when)
        errors += [f"step {step.id}: unknown document '{d}'" for d in step.documents if d not in documents]
        for group in step.one_of:
            if len(group) < 2:
                errors.append(f"step {step.id}: a set of alternatives needs at least two documents")
            errors += [f"step {step.id}: unknown alternative document '{d}'" for d in group if d not in documents]
        errors += [f"step {step.id}: unknown clock '{c}'" for c in step.clocks if c not in clocks]
        errors += [f"step {step.id}: waits for unknown step '{a}'" for a in step.after if a not in ids]
        if step.decides and step.decides not in variables:
            errors.append(f"step {step.id}: decides undeclared variable '{step.decides}'")
        if not step.cites and not plan.unverified:
            errors.append(f"step {step.id}: no source cited")
    for doc in plan.documents:
        walk(doc.when)
        if not doc.cites and not plan.unverified:
            errors.append(f"document {doc.id}: no source cited")
        if doc.acquire_plan is not None and not doc.acquire_plan:
            errors.append(f"document {doc.id}: empty acquire plan key")
    for var in plan.variables:
        if var.type == "choice" and var.question and not var.options:
            errors.append(f"variable {var.name}: a choice asked of the family needs options")
    # 'after' must not form a cycle
    graph = {s.id: list(s.after) for s in plan.steps}
    state: dict[str, int] = {}

    def visit(node: str) -> bool:
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        cyclic = any(visit(n) for n in graph.get(node, []) if n in graph)
        state[node] = 2
        return cyclic
    if any(visit(n) for n in graph):
        errors.append("steps wait for each other in a cycle")
    finishing = ROUTE_KINDS if plan.holding_kinds else (*ROUTE_KINDS, "certify", "acquire")
    if not any(s.kind in finishing for s in plan.steps):
        errors.append("no step submits a claim or hands it off")
    for clock in plan.clocks:
        walk(clock.when)
        if not clock.cites:
            errors.append(f"clock {clock.id}: no source cited")
    for item in [*plan.steps, *plan.documents, *plan.clocks, *plan.variables]:
        errors += [f"{item.__class__.__name__} {getattr(item, 'id', getattr(item, 'name', '?'))}: "
                   f"cites missing assertion '{a}'" for a in item.cites if a not in known_assertions]
    return errors


def _add_months(start: date, months: int) -> date:
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def active_clocks(plan: ProcedurePlan, facts: dict) -> list[Clock]:
    """The clocks that apply on these facts: a clock whose condition is false is left out, and one whose
    condition cannot be decided yet is kept (the tracker shows it until the facts settle it)."""
    return [c for c in plan.clocks if evaluate(c.when, facts) != "no"]


def due_date(clock: Clock, started: date) -> date:
    if clock.unit == "calendar_days":
        return started + timedelta(days=clock.length)
    if clock.unit == "months":
        return _add_months(started, clock.length)
    country, _, subdiv = (clock.holidays or "").partition("-")
    closed = holiday_calendars.country_holidays(country, subdiv=subdiv or None) if country else {}
    day, counted = started, 0
    while counted < clock.length:
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in closed:
            counted += 1
    return day


COMPENSATION_FORMULAS = ("rate_per_year_on_amount", "rate_per_year_from_start", "fixed_per_day")


def compensation(formula: str, amount: Decimal, due: date, paid_or_today: date,
                 params: dict[str, Decimal], start: date | None = None) -> Decimal:
    """Only formulas a cited rule states are implemented; anything else is refused.

    rate_per_year_on_amount   interest for the days after the due date (RBI deposits: Bank Rate + 4%)
    rate_per_year_from_start  once late, interest from the clock's start (IRDAI: bank rate + 2% from
                              intimation till payment)
    fixed_per_day             a fixed sum for each day of delay (RBI lockers: Rs 5,000 a day)
    """
    days_late = max(0, (paid_or_today - due).days)
    if formula == "rate_per_year_on_amount":
        rate = params["base_rate_pct"] + params["spread_pct"]
        return (amount * rate / 100 * days_late / 365).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if formula == "rate_per_year_from_start":
        if days_late == 0:
            return Decimal("0.00")
        if start is None:
            raise ValueError("rate_per_year_from_start needs the clock's start date")
        rate = params["base_rate_pct"] + params["spread_pct"]
        days = max(0, (paid_or_today - start).days)
        return (amount * rate / 100 * days / 365).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if formula == "fixed_per_day":
        return (params["per_day"] * days_late).quantize(Decimal("0.01"), ROUND_HALF_UP)
    raise ValueError(f"no implementation for compensation formula '{formula}'")


def per_day(formula: str, amount: Decimal, params: dict[str, Decimal]) -> Decimal | None:
    """What one more day of delay adds, for showing on the tracker."""
    if formula in ("rate_per_year_on_amount", "rate_per_year_from_start"):
        rate = params["base_rate_pct"] + params["spread_pct"]
        return (amount * rate / 100 / 365).quantize(Decimal("0.01"), ROUND_HALF_UP)
    if formula == "fixed_per_day":
        return params["per_day"]
    return None
