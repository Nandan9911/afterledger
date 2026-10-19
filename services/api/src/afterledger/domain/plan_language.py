"""Shared knowledge records and the procedure-plan language.

An assertion is one checkable sentence about the world with the verbatim words that support it.
A procedure plan is a small typed program that agents compile from assertions and code runs.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Tri = Literal["yes", "no", "unknown"]
Scalar = str | int | float | bool
Level = Literal["draft", "sourced", "dual_verified", "adjudicated", "outcome_confirmed", "gold"]
LEVEL_ORDER: dict[str, int] = {
    "draft": 0, "sourced": 1, "dual_verified": 2, "adjudicated": 3, "outcome_confirmed": 4, "gold": 5,
}
TIER_NAMES = {
    1: "Law, regulator or government",
    2: "The institution's own page or form",
    3: "Court, ombudsman or public body",
    4: "Professional body or established firm",
    5: "News or forum (never evidence)",
}


class Source(BaseModel):
    url: str
    title: str = ""
    publisher: str = ""
    tier: int = Field(ge=1, le=5)
    language: str = "en"
    retrieved: date
    updated: str | None = None          # the page's own "last updated" text, if shown
    page_hash: str | None = None        # sha256 of the normalised page text when the quote was checked
    fictional: bool = False             # pages of fictional institutions in the demo


class Assertion(BaseModel):
    id: str
    text: str                            # one sentence in plain English
    quote: str                           # verbatim, in the source's language
    quote_translation: str | None = None
    source: Source
    jurisdiction: str                    # ISO country code, or code + subdivision ("GB-ENG", "US-TX")
    topic: str = ""                      # e.g. "deposit/no_nominee/documents"
    institution_id: str | None = None
    level: Level
    ttl_days: int
    checked_on: date | None = None
    researchers: list[str] = []          # which researcher paths reached it
    contested_with: list[str] = []       # ids of assertions that disagree
    notes: str = ""


class AssertionSet(BaseModel):
    question_ids: list[str] = []
    assertions: list[Assertion] = []


class Cond(BaseModel):
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "all", "any", "not"]
    var: str | None = None
    value: Scalar | list[str] | None = None
    args: list[Cond] = []

    def text(self) -> str:
        """The condition in the same small language plans are written in."""
        return cond_to_text(self)


# ---- the condition language ----------------------------------------------------------------
# Plans written by hand (and shown to people) use a small expression language:
#     nominee_or_survivor = no and (bank_type = commercial and amount <= 1500000) or dispute = yes
# Words are compared as text (so "yes" stays the string "yes"), numbers as numbers.

_TOKEN = re.compile(r"""\s*(?:(?P<num>-?\d+(?:\.\d+)?)(?![A-Za-z_])|(?P<str>'[^']*'|"[^"]*")|"""
                    r"""(?P<op><=|>=|==|!=|=|<|>)|(?P<punct>[()\[\],])|(?P<word>[A-Za-z_][A-Za-z0-9_.\-]*))""")
_OPS = {"=": "eq", "==": "eq", "!=": "ne", "<": "lt", "<=": "le", ">": "gt", ">=": "ge"}
_SYMBOL = {v: k for k, v in _OPS.items() if k != "=="}


class ConditionSyntaxError(ValueError):
    pass


def _tokenise(text: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ConditionSyntaxError(f"cannot read the condition at: {text[pos:pos + 20]!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1
    return out


def parse_cond(text: str) -> Cond:
    tokens = _tokenise(text)
    pos = 0

    def peek(*accept: str) -> tuple[str, str] | None:
        if pos < len(tokens):
            kind, val = tokens[pos]
            if not accept or val.lower() in accept or kind in accept:
                return kind, val
        return None

    def take() -> tuple[str, str]:
        nonlocal pos
        if pos >= len(tokens):
            raise ConditionSyntaxError(f"condition ends too early: {text!r}")
        pos += 1
        return tokens[pos - 1]

    def value() -> Scalar:
        kind, val = take()
        if kind == "num":
            return float(val) if "." in val else int(val)
        if kind == "str":
            return val[1:-1]
        if kind == "word":
            return val
        raise ConditionSyntaxError(f"expected a value, found {val!r} in {text!r}")

    def comparison() -> Cond:
        kind, var = take()
        if kind != "word":
            raise ConditionSyntaxError(f"expected a variable name, found {var!r} in {text!r}")
        if peek("in"):
            take()
            if take()[1] != "[":
                raise ConditionSyntaxError(f"'in' needs a list in brackets in {text!r}")
            items: list[str] = []
            while not peek("]"):
                items.append(str(value()))
                if peek(","):
                    take()
            take()
            return Cond(op="in", var=var, value=items)
        kind, op = take()
        if kind != "op":
            raise ConditionSyntaxError(f"expected a comparison after {var!r} in {text!r}")
        return Cond(op=_OPS[op], var=var, value=value())

    def atom() -> Cond:
        if peek("not"):
            take()
            return Cond(op="not", args=[atom()])
        if peek("("):
            take()
            inner = disjunction()
            if take()[1] != ")":
                raise ConditionSyntaxError(f"missing ')' in {text!r}")
            return inner
        return comparison()

    def conjunction() -> Cond:
        parts = [atom()]
        while peek("and"):
            take()
            parts.append(atom())
        return parts[0] if len(parts) == 1 else Cond(op="all", args=_flatten("all", parts))

    def disjunction() -> Cond:
        parts = [conjunction()]
        while peek("or"):
            take()
            parts.append(conjunction())
        return parts[0] if len(parts) == 1 else Cond(op="any", args=_flatten("any", parts))

    result = disjunction()
    if pos != len(tokens):
        raise ConditionSyntaxError(f"unexpected {tokens[pos][1]!r} in {text!r}")
    return result


def _flatten(op: str, parts: list[Cond]) -> list[Cond]:
    out: list[Cond] = []
    for p in parts:
        out.extend(p.args if p.op == op else [p])
    return out


def _fmt_value(v: Any) -> str:
    if isinstance(v, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]*", v) and v.lower() not in (
            "and", "or", "not", "in"):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return repr(str(v))


def cond_to_text(c: Cond, parent: str | None = None) -> str:
    if c.op in ("all", "any"):
        joiner = " and " if c.op == "all" else " or "
        inner = joiner.join(cond_to_text(a, c.op) for a in c.args)
        return f"({inner})" if parent in ("all", "any", "not") else inner
    if c.op == "not":
        return "not " + cond_to_text(c.args[0], "not")
    if c.op == "in":
        return f"{c.var} in [{', '.join(_fmt_value(v) for v in (c.value or []))}]"
    return f"{c.var} {_SYMBOL[c.op]} {_fmt_value(c.value)}"


def _coerce_cond(v: Any) -> Any:
    return parse_cond(v) if isinstance(v, str) else v


class Variable(BaseModel):
    name: str
    type: Literal["tri", "number", "money", "date", "choice", "text"]
    question: str | None = None          # how to ask the family, in plain words
    derive: str | None = None            # name of a derivation the case can compute
    options: list[str] = []              # for choice variables
    unit: str | None = None              # currency or unit for money and number variables
    cites: list[str] = []


class DocumentReq(BaseModel):
    id: str
    name: str
    certification: list[Literal[
        "original", "certified_copy", "self_attested", "notarised", "stamp_paper", "apostille", "consular",
        "translation"]] = []
    acquire_plan: str | None = None      # key of a plan that obtains this paper
    form_key: str | None = None          # form library key when the document is a form to fill
    signed_by: list[str] = []            # roles: claimant, other_heirs, independent_person, executor
    when: Cond | None = None             # needed only when this holds (e.g. there are other heirs)
    note: str = ""
    cites: list[str] = []

    _when = field_validator("when", mode="before")(classmethod(lambda cls, v: _coerce_cond(v)))


class Clock(BaseModel):
    id: str
    label: str = ""
    short: str = ""                      # in a list of deadlines, after the institution's name: "settlement"
    short_local: str = ""                # the same in the plan's own language ("相続の承認・放棄の期限")
    kind: Literal["deadline", "window", "wait"] = "deadline"
    """deadline: someone must act by the due day (late after it); window: something stays possible until
    the due day (ended after it); wait: something becomes possible on the due day (met after it)."""
    owner: Literal["institution", "family"]
    starts_on: str                       # event name, e.g. "complete_set_received"
    ends_on: str = ""                    # event that stops the clock; "" = settled (institution) or submitted (family)
    length: int
    unit: Literal["calendar_days", "business_days", "months"]
    holidays: str | None = None          # "IN", "GB-ENG", "JP"
    compensation: str | None = None      # formula name
    compensation_params: dict[str, str] = {}   # e.g. {"spread_pct": "4"}; base rates come from knowledge
    notes: str = ""
    when: Cond | None = None             # a clock that applies only in some situations (e.g. an investigation)
    cites: list[str]
    rule: bool = True                    # False: AfterLedger's own date to follow up (an interim plan's), not a rule

    _when = field_validator("when", mode="before")(classmethod(lambda cls, v: _coerce_cond(v)))


class Step(BaseModel):
    id: str
    kind: Literal["ask", "acquire", "fill", "sign", "certify", "submit", "notify", "wait", "follow_up",
                  "handoff", "decide", "info"]
    actor: Literal["family", "institution", "authority", "professional", "agent"]
    text: str
    when: Cond | None = None
    documents: list[str] = []
    one_of: list[list[str]] = []         # sets of alternative documents: any one of each set will do
    forms: list[str] = []
    must_not_be_asked: list[str] = []
    clocks: list[str] = []
    after: list[str] = []                # steps that must be finished first
    decides: str | None = None           # for decide steps: the variable the decision settles
    done_on: str = ""                    # the claim event that finishes this step, when not the usual one
    cites: list[str] = []

    _when = field_validator("when", mode="before")(classmethod(lambda cls, v: _coerce_cond(v)))


ROUTE_KINDS = ("submit", "handoff")
"""Step kinds that decide where a claim goes; gold tests compare these."""


class ProcedurePlan(BaseModel):
    key: str                             # situation signature, e.g. "IN/bank_deposit"
    title: str = ""
    jurisdiction: str
    institution_kind: str = ""           # bank, insurer, depository, pension, government...
    authority: str | None = None         # for a procedure (a court, a registry): the institution id that runs it
    holding_kinds: list[str] = []
    language: str = "en"
    variables: list[Variable]
    documents: list[DocumentReq]
    clocks: list[Clock]
    steps: list[Step]
    level: Level = "draft"
    compiled_from: list[str] = []
    contested: list[str] = []            # assertion ids still contested
    conditions: dict[str, str] = {}      # named conditions, used in 'when' texts as @name
    verified_on: date | None = None
    unverified: bool = False             # no tier 1 or 2 source: the plan starts by asking the institution
    notes: str = ""
    # Variables whose threshold the sources leave open on the day itself ("less than 10 years" and "more than 10
    # years", nothing for exactly 10): a gold check does not test a plan at the threshold itself for these.
    unclear_at_threshold: list[str] = []

    @model_validator(mode="before")
    @classmethod
    def _expand_named_conditions(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not data.get("conditions"):
            return data
        named: dict[str, str] = data["conditions"]

        def expand(text: str, depth: int = 0) -> str:
            if depth > 5:
                raise ConditionSyntaxError("named conditions nest too deeply")

            def sub(m: re.Match) -> str:
                name = m.group(1)
                if name not in named:
                    raise ConditionSyntaxError(f"unknown named condition @{name}")
                return f"({expand(named[name], depth + 1)})"
            return re.sub(r"@([A-Za-z_][A-Za-z0-9_]*)", sub, text)

        data = dict(data)
        for section in ("steps", "documents"):
            items = []
            for item in data.get(section) or []:
                if isinstance(item, dict) and isinstance(item.get("when"), str):
                    item = dict(item, when=expand(item["when"]))
                items.append(item)
            data[section] = items
        return data

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def document(self, doc_id: str) -> DocumentReq | None:
        return next((d for d in self.documents if d.id == doc_id), None)

    def clock(self, clock_id: str) -> Clock | None:
        return next((c for c in self.clocks if c.id == clock_id), None)

    def variable(self, name: str) -> Variable | None:
        return next((v for v in self.variables if v.name == name), None)

    def all_cites(self) -> set[str]:
        out: set[str] = set(self.compiled_from)
        for item in [*self.steps, *self.documents, *self.clocks, *self.variables]:
            out.update(item.cites)
        return out


Cond.model_rebuild()
