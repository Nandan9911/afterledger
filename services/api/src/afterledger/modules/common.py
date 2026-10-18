"""Shared helpers for the modules: loading a case, building context packs, small formatting."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from ..domain.case import (
    CaseFile,
    Checkpoint,
    Claim,
    Correspondence,
    Fact,
    Holding,
    Lead,
    OcrLine,
    Pack,
    Paper,
    PaymentGroup,
    Person,
    ReadValue,
    Task,
    Transaction,
)
from ..domain.ids import new_id
from ..knowledge import Knowledge


@dataclass
class Snapshot:
    case: CaseFile
    people: list[Person] = field(default_factory=list)
    papers: list[Paper] = field(default_factory=list)
    values: list[ReadValue] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    holdings: list[Holding] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    groups: list[PaymentGroup] = field(default_factory=list)
    leads: list[Lead] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    plans: list[Any] = field(default_factory=list)
    packs: list[Pack] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    correspondence: list[Correspondence] = field(default_factory=list)

    def person(self, pid: str | None) -> Person | None:
        return next((p for p in self.people if p.id == pid), None)

    @property
    def deceased(self) -> Person | None:
        return self.person(self.case.deceased_id)

    def paper(self, pid: str) -> Paper | None:
        return next((p for p in self.papers if p.id == pid), None)

    def holding(self, hid: str | None) -> Holding | None:
        return next((h for h in self.holdings if h.id == hid), None)

    def claim(self, cid: str | None) -> Claim | None:
        return next((c for c in self.claims if c.id == cid), None)

    def lead(self, lid: str | None) -> Lead | None:
        return next((x for x in self.leads if x.id == lid), None)

    def plan_for_claim(self, cid: str) -> Any | None:
        return next((p for p in self.plans if p.claim_id == cid), None)

    def values_of(self, paper_id: str) -> list[ReadValue]:
        return [v for v in self.values if v.paper_id == paper_id]

    def family(self) -> list[Person]:
        return [p for p in self.people if p.id != self.case.deceased_id]


COLLECTION_FIELDS = ["people", "papers", "values", "facts", "holdings", "transactions", "groups", "leads", "claims",
                     "plans", "packs", "tasks", "checkpoints", "correspondence"]


async def snapshot(deps: Any, case_id: str, only: list[str] | None = None) -> Snapshot:
    case = await deps.cases.get_case(case_id)
    if case is None:
        raise KeyError(f"no case {case_id}")
    snap = Snapshot(case=case)
    for name in only or COLLECTION_FIELDS:
        setattr(snap, name, await deps.cases.list(case_id, name))
    return snap


def knowledge(deps: Any) -> Knowledge:
    k = deps.extras.get("knowledge")
    if k is None:
        k = Knowledge(deps.knowledge, deps.pages)
        deps.extras["knowledge"] = k
    return k


def case_block(case: CaseFile) -> dict:
    """The part of every context pack that says which case this is."""
    return {"id": case.id, "demo": case.demo_key, "story_day": case.story_day.isoformat() if case.story_day else None,
            "died_on": case.died_on.isoformat() if case.died_on else None, "died_in": case.died_in,
            "countries": case.country_codes, "languages": case.languages}


def pack_key(agent: str, case: CaseFile, *parts: str) -> str:
    return "/".join([agent, case.demo_key or case.id, *[p for p in parts if p]])


def person_brief(p: Person) -> dict:
    return {"id": p.id, "name": p.display_name, "relationship": p.relationship, "roles": p.roles,
            "languages": p.languages, "country": p.country, "city": p.city}


def money(v: Decimal | str | float | int | None) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v)).quantize(Decimal("0.01"))


def lines_by_page(lines: list[OcrLine]) -> dict[str, list[OcrLine]]:
    out: dict[str, list[OcrLine]] = {}
    for ln in lines:
        out.setdefault(ln.page_id, []).append(ln)
    return out


def iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


async def open_checkpoint(deps: Any, case: CaseFile, *, code: str, kind: str, title: str, question: str,
                          options: list[tuple[str, str]], for_person_id: str | None = None,
                          subject_ref: str | None = None, why: str = "", payload: dict | None = None,
                          question_local: str | None = None, language: str = "en") -> Checkpoint:
    """Opens (or refreshes) one checkpoint per subject, so re-running a module never duplicates it."""
    from ..domain.case import Option
    existing = [c for c in await deps.cases.list(case.id, "checkpoints")
                if c.subject_ref == subject_ref and c.kind == kind and subject_ref]
    if existing:
        cp = existing[0]
        if cp.status != "open":
            return cp
    else:
        cp = Checkpoint(id=new_id("cp"), case_id=case.id, code=code, kind=kind, title=title, question=question,
                        created_at=deps.now())
    cp.title, cp.question, cp.why = title, question, why
    cp.options = [Option(value=v, label=lab) for v, lab in options]
    cp.for_person_id, cp.subject_ref, cp.payload = for_person_id, subject_ref, payload or {}
    cp.question_local, cp.language = question_local, language
    await deps.cases.put(case.id, "checkpoints", cp)
    return cp

async def screen_outside(deps: Any, text: str, *, kind: str, source: str, case_id: str | None = None,
                         subject: str | None = None, module: str = "system") -> Any:
    """Screens text from outside (a web page, a paper, an institution's reply) for instructions aimed at the
    agents. The result goes to the verification ledger, and a flagged text gets a plain warning on the case."""
    result = await deps.screen.screen(text or "", kind=kind, source=source)
    if case_id:
        # The ledger's results are pass / fail / unknown: a flagged text fails the screen, and a text the screen
        # could not fully check (a filter that did not run) is unknown. The detail names what was found.
        verdict = "fail" if result.flagged else ("unknown" if result.skipped else "pass")
        detail = ", ".join(result.reasons) or ("not fully screened" if result.skipped else source)
        await deps.ledger(case_id, subject or f"{kind}:{source}", "screen", verdict,
                          method=f"{result.engine}: planted instructions and malicious links", detail=detail)
        if result.flagged:
            what = {"web_page": "A web page", "paper": "A paper", "reply": "A reply"}.get(kind, "Some text")
            await deps.activity(case_id, module, f"{what} contained text addressed to the software rather than to "
                                f"a person ({', '.join(result.reasons)}). It is read only as material, and never "
                                "followed.", kind="warning", source=source[:300])
    return result

