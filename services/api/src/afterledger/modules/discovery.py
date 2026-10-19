"""Module 5: finding what nobody listed.

Holdings named on papers go straight onto the money map. Statement lines are grouped by payee
and cadence; each group is interpreted, hypotheses are drawn up, and every hypothesis gets its own
investigator and then a critic, running in parallel as an ADK workflow. Probabilities come from the
calibration table, and each open lead goes to the family to mark (checkpoint C4).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import statistics
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from google.adk import Workflow
from pydantic import ValidationError

from ..agents.runner import AgentError, Unusable, agent_node, as_model, call_agent, run_workflow
from ..domain.calibration import lead_label
from ..domain.case import Evidence, Holding, Lead, PaymentGroup, Transaction
from ..domain.ids import new_id, stable_id
from ..domain.money import format_amount
from .common import case_block, knowledge, open_checkpoint, pack_key, snapshot
from .people import ensure_institution_id

MODULE = "discovery"
log = logging.getLogger(__name__)

DOC_HOLDING = {
    "bank_statement": ("savings", "asset", "balance_latest", "account_number"),
    "passbook": ("savings", "asset", "balance_latest", "account_number"),
    "fixed_deposit_receipt": ("fixed_deposit", "asset", "principal", "receipt_number"),
    "insurance_policy": ("life_policy", "asset", "sum_assured", "policy_number"),
    "insurance_notice": ("life_policy", "asset", "face_amount", "policy_number"),
    "demat_holding_statement": ("demat", "asset", "total_value", "client_id"),
    "card_statement": ("credit_card", "debt", "outstanding", "account_number"),
    "cpf_statement": ("cpf", "asset", "total_balance", "id_number"),
    "pension_letter": ("pension", "entitlement", None, "reference"),   # a pension in payment has no balance
}
ACCOUNT_KINDS = (("CURRENT", "current"), ("CHECKING", "current"), ("当座", "current"), ("NRE", "nre"),
                 ("NRO", "nro"), ("GIRO", "current"))
CURRENCY = {"IN": "INR", "JP": "JPY", "GB": "GBP", "US": "USD", "SG": "SGD", "DE": "EUR", "FR": "EUR"}


def narration_key(text: str) -> str:
    t = re.sub(r"[/\-_:.,*#]+", " ", text.upper())
    tokens = [w for w in t.split() if not re.fullmatch(r"\d[\d]*|\d{4}\s?\d{2}|[0-9X]{6,}", w)]
    return " ".join(tokens)[:60]


def cadence_of(dates: list[date]) -> tuple[str, float | None]:
    if len(dates) < 2:
        return "once", None
    ds = sorted(dates)
    gaps = [(b - a).days for a, b in itertools.pairwise(ds)]
    med = statistics.median(gaps)
    for name, lo, hi in [("weekly", 5, 9), ("monthly", 25, 35), ("quarterly", 80, 100), ("half_yearly", 170, 200),
                         ("yearly", 350, 380)]:
        if lo <= med <= hi:
            return name, med
    return "irregular", med


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    return digits[-4:] if len(digits) >= 4 else value[-4:]


async def holdings_from_papers(deps: Any, case_id: str) -> list[Holding]:
    snap = await snapshot(deps, case_id, ["papers", "values", "holdings", "transactions", "people"])
    out: list[Holding] = []
    existing = {h.id: h for h in snap.holdings}
    for paper in snap.papers:
        spec = DOC_HOLDING.get(paper.doc_type)
        if not spec or not paper.institution_id:
            continue
        kind, direction, value_field, id_field = spec
        vals = {v.field: v for v in snap.values_of(paper.id)}
        acct_type = (vals["account_type"].value or "").upper() if "account_type" in vals else ""
        kind = next((k for word, k in ACCOUNT_KINDS if word in acct_type), kind) if kind == "savings" else kind
        if kind == "credit_card" and any(w in acct_type for w in ("LOAN", "ローン")):
            kind = "card_loan"
        mask = _mask(vals[id_field].value if id_field in vals else None)
        hid = stable_id("h", case_id, paper.institution_id, mask or paper.id, kind)
        h = existing.get(hid) or next((x for x in existing.values() if paper.id in x.paper_ids and x.kind == kind), None)
        if h is None:
            # a lead that a paper now proves keeps its place on the money map
            h = next((x for x in existing.values() if (x.lead_id or x.source == "family")
                      and x.institution_id == paper.institution_id
                      and x.kind == kind and not x.account_mask and not x.paper_ids), None)
            if h is not None:
                h.label = _label(kind)
        h = h or Holding(id=hid, case_id=case_id, institution_id=paper.institution_id, kind=kind,
                         direction=direction, label=_label(kind), account_mask=mask)
        h.account_mask = h.account_mask or mask
        h.paper_ids = sorted(set(h.paper_ids) | {paper.id})
        v = vals.get(value_field) if value_field else None
        if v and v.value and h.settled_amount is None:
            txns = [t for t in snap.transactions if t.paper_id == paper.id]
            as_of = max((t.date for t in txns), default=None) if value_field == "balance_latest" else None
            # with several statements for one account, the latest balance stands
            if h.value is None or as_of is None or h.value_as_of is None or as_of >= h.value_as_of:
                h.value = Decimal(v.value)
                h.value_as_of = as_of or h.value_as_of
        h.currency = CURRENCY.get(paper.country or "IN", "INR")
        h.country = paper.country
        nom = vals.get("nomination") or vals.get("nominee")
        if nom and nom.value:
            text = nom.value.upper()
            h.nominee = "no" if (re.search(r"\b(NOT|NONE|NIL|NO)\b", text) or "なし" in text) else "yes"
            if h.nominee == "yes":
                from .people import person_for_name
                who = person_for_name(snap.people, re.sub(r"^REGISTERED:\s*", "", nom.value, flags=re.I))
                h.nominee_person_id = who.id if who else h.nominee_person_id
        h.evidence = [e for rv in vals.values() for e in rv.evidence if rv.field in (value_field, id_field)][:3] or \
            [Evidence(kind="paper", ref=paper.id, paper_id=paper.id, label=paper.title)]
        h.status = "confirmed"
        out.append(h)
        await ensure_institution_id(deps, case_id, paper.institution_id)
    await deps.cases.put_many(case_id, "holdings", out)
    for h in out:
        if h.id not in existing:
            inst = await knowledge(deps).institution(h.institution_id) if h.institution_id else None
            amount = format_amount(h.value, h.currency) if h.value is not None else "value not known yet"
            await deps.activity(case_id, MODULE, f"Added to the money map: {inst.name if inst else 'an institution'}, "
                                f"{h.label.lower()}{' ending ' + h.account_mask if h.account_mask else ''}, {amount}.",
                                kind="step")
    return out


DEBT_KINDS = {"loan", "card_loan", "credit_card"}


async def holdings_from_family(deps: Any, case_id: str) -> list[Holding]:
    """What the family named in the conversation ("his SBI account", "an LIC policy", "a loan from Bajaj Finance")
    goes onto the money map as theirs, with its institution identified (looked up on the web when the directory does
    not have it), so it is planned and claimed like anything a paper shows. A kind of holding named without its
    institution waits: the coverage questions ask where it is held. A paper that later shows the same thing takes
    the holding over."""
    from typing import get_args

    from ..domain.case import HoldingKind
    from .institutions import Lookups, resolve
    case = await deps.cases.get_case(case_id)
    sit = case.situation if case else None
    if case is None or case.demo_key or sit is None or not sit.holdings:
        return []
    allowed = set(get_args(HoldingKind))
    existing = await deps.cases.list(case_id, "holdings")
    lookups = Lookups(8)
    out: list[Holding] = []
    for m in sit.holdings:
        name = str(m.get("institution") or "").strip()
        if not name:
            continue
        kind = m.get("kind") if m.get("kind") in allowed else "other"
        inst = await resolve(deps, case, name, kind=_HOLDER_KIND.get(kind), lookups=lookups, must_be_org=False,
                             context=f"the family said the person who died had: {m.get('said') or name}",
                             source="named by the family")
        if inst is None:
            continue
        if any(h.institution_id == inst.id and h.kind == kind and h.status != "closed" for h in existing):
            continue                      # already on the money map, from a paper, a lead or an earlier turn
        country = (inst.country or (case.country_codes or ["IN"])[0]).split("-")[0]
        h = Holding(id=stable_id("h", case_id, "family", inst.id, kind), case_id=case_id, institution_id=inst.id,
                    kind=kind, direction="debt" if kind in DEBT_KINDS or m.get("direction") == "debt" else "asset",  # type: ignore[arg-type]
                    label=_label(kind), status="confirmed", source="family", country=country,
                    currency=CURRENCY.get(country, "INR"), note=f"The family said: “{m.get('said') or name}”.")
        out.append(h)
        existing.append(h)
        await ensure_institution_id(deps, case_id, inst.id)
    if out:
        await deps.cases.put_many(case_id, "holdings", out)
        k = knowledge(deps)
        names = []
        for h in out:
            inst = await k.institution(h.institution_id)
            names.append(f"{(inst.short_name or inst.name) if inst else 'an institution'}, {h.label.lower()}")
        await deps.activity(case_id, MODULE, "Added to the money map from what the family said: " + "; ".join(names)
                            + ".", kind="step")
        deps.changed(case_id, "holdings")
    return out


def _label(kind: str) -> str:
    return {"savings": "Savings account", "current": "Current account", "fixed_deposit": "Fixed deposit",
            "life_policy": "Life insurance policy", "demat": "Demat account (shares)", "credit_card": "Credit card",
            "cpf": "CPF savings", "pension": "Pension", "nre": "NRE savings account", "nro": "NRO savings account",
            "card_loan": "Card loan",
            }.get(kind, kind.replace("_", " ").capitalize())


async def group_payments(deps: Any, case_id: str) -> list[PaymentGroup]:
    snap = await snapshot(deps, case_id, ["transactions", "groups"])
    death = snap.case.died_on
    buckets: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for t in snap.transactions:
        buckets[(narration_key(t.narration), t.direction)].append(t)
    groups: list[PaymentGroup] = []
    for (key, direction), txns in sorted(buckets.items()):
        cad, med = cadence_of([t.date for t in txns])
        amounts = [t.amount for t in txns]
        typical = Decimal(statistics.median([float(a) for a in amounts])).quantize(Decimal("0.01"))
        gid = stable_id("g", case_id, key, direction)
        g = PaymentGroup(id=gid, case_id=case_id, key=key, label=txns[0].narration, direction=direction,
                         count=len(txns), cadence=cad, median_gap_days=med, amount_typical=typical,
                         amount_stable=len(set(amounts)) == 1, currency=txns[0].currency,
                         first=min(t.date for t in txns), last=max(t.date for t in txns),
                         after_death_count=sum(1 for t in txns if death and t.date > death),
                         transaction_ids=[t.id for t in txns])
        groups.append(g)
        for t in txns:
            t.group_id = gid
    await deps.cases.put_many(case_id, "groups", groups)
    await deps.cases.put_many(case_id, "transactions", [t for g in buckets.values() for t in g])
    return groups


# A year of a person's own statements has hundreds of different payees, most of them one-off. Above this many
# payment groups, the ones worth an agent's reading go first: what recurs, what continued after the death, words
# that name a policy, a fund, a loan or a pension, and the largest amounts.
MAX_GROUPS = 60
_TELLING = re.compile(r"PREM|POLICY|INSUR|LIC\b|ASSUR|LIFE|PMJJBY|PMSBY|SIP|MUTUAL|\bMF\b|NACH|ACH|ECS|MANDATE|"
                      r"LOAN|EMI|PENSION|SALARY|PAYROLL|DIVIDEND|INTEREST|RENT|TAX|CARD|DEMAT|NSDL|CDSL|PPF|EPF|"
                      r"NPS|CPF|BOND|FD\b|DEPOSIT|SECURITIES|STANDING", re.I)


def worth_reading(groups: list[PaymentGroup]) -> list[PaymentGroup]:
    """All the groups when they are few; above MAX_GROUPS, the most telling ones, in their original order."""
    if len(groups) <= MAX_GROUPS:
        return groups

    def rank(g: PaymentGroup) -> tuple:
        return (-(g.after_death_count > 0), -(g.count >= 2 and g.cadence != "once"), -bool(_TELLING.search(g.label)),
                -(g.count >= 2), -float(g.amount_typical))
    keep = {g.id for g in sorted(groups, key=rank)[:MAX_GROUPS]}
    return [g for g in groups if g.id in keep]


SCREEN_BATCH = 80          # payees one quick screening call looks at
SCREEN_EXTRA = 40          # the most a screening adds to the closer reading


async def screen_payees(deps: Any, case: Any, rest: list[PaymentGroup], rails: dict | None) -> list[PaymentGroup]:
    """Every payee beyond the first MAX_GROUPS gets a quick look, in batches, by a small model: what it is, and whether
    it may point to something to claim, owe or stop. Those that may are read closely too (at most SCREEN_EXTRA), so a
    policy paid once a year or a loan paid by UPI is not missed for being one payee among hundreds."""
    batches = [rest[i:i + SCREEN_BATCH] for i in range(0, len(rest), SCREEN_BATCH)]

    async def one(n: int, batch: list[PaymentGroup]) -> list:
        try:
            out = await call_agent(deps, "payee_screener", {
                "key": pack_key("payee_screener", case, str(n)), "case": case_block(case), "rails": rails,
                "groups": [{"id": g.id, "label": g.label, "direction": g.direction, "count": g.count,
                            "cadence": g.cadence, "amount": str(g.amount_typical), "currency": g.currency}
                           for g in batch]}, case_id=case.id)
            return list(out.items)
        except (AgentError, Unusable, ValidationError, json.JSONDecodeError) as e:
            log.warning("payee screening batch %d for %s failed: %s", n, case.id, e)
            return []
    found = [x for items in await asyncio.gather(*(one(n, b) for n, b in enumerate(batches))) for x in items]
    by_id = {g.id: g for g in rest}
    worth: list[PaymentGroup] = []
    for it in found:
        g = by_id.get(it.group_id)
        if g is None:
            continue
        g.counterparty = g.counterparty or it.counterparty
        g.counterparty_kind = g.counterparty_kind or (it.kind if it.kind != "unknown" else None)
        if it.worth:
            worth.append(g)
    await deps.cases.put_many(case.id, "groups", [by_id[it.group_id] for it in found if it.group_id in by_id])
    # the most telling first, as for the first MAX_GROUPS
    return sorted(worth, key=lambda g: (-(g.after_death_count > 0), -(g.count >= 2), -float(g.amount_typical)))[
        :SCREEN_EXTRA]


async def discover(deps: Any, case_id: str) -> list[Lead]:
    await holdings_from_papers(deps, case_id)
    all_groups = await group_payments(deps, case_id)
    groups = worth_reading(all_groups)
    if len(groups) < len(all_groups):
        case0 = await deps.cases.get_case(case_id)
        country0 = next(iter(case0.country_codes or []), "IN")
        rest = [g for g in all_groups if g.id not in {x.id for x in groups}]
        extra = await screen_payees(deps, case0, rest, await deps.knowledge.get("rails", country0))
        keep = {g.id for g in groups} | {g.id for g in extra}
        groups = [g for g in all_groups if g.id in keep]
        await deps.activity(case_id, MODULE, f"The statements have {len(all_groups)} different payments and payees. "
                            f"The {len(all_groups) - len(rest)} that recur, continued after the death, or name a policy, "
                            f"fund, loan or pension are looked into closely; the other {len(rest)} were screened "
                            f"quickly, and {len(extra)} of them looked worth a closer reading too.", kind="step")
    snap = await snapshot(deps, case_id)
    case = snap.case
    k = knowledge(deps)
    countries = sorted({p.country for p in snap.papers if p.country} or set(case.country_codes) or {"IN"})
    rails = {c: await deps.knowledge.get("rails", c) for c in countries}
    coverage = _one_line_each({c: await k.coverage(c) for c in countries})
    base = {"case": case_block(case)}
    txn_by_id = {t.id: t for t in snap.transactions}
    group_packs = [{"key": pack_key("narration_interpreter", case, g.key), **base,
                    "group": {"id": g.id, "label": g.label, "direction": g.direction, "count": g.count,
                              "cadence": g.cadence, "amount": str(g.amount_typical), "currency": g.currency,
                              "first": g.first.isoformat(), "last": g.last.isoformat(),
                              "after_death": g.after_death_count,
                              "dates": [txn_by_id[t].date.isoformat() for t in g.transaction_ids if t in txn_by_id]},
                    "rails": rails.get(countries[0])} for g in groups]
    if not group_packs and not snap.papers:
        return []

    for c in countries:
        if rails.get(c):
            await call_agent(deps, "rail_profiler", {"key": pack_key("rail_profiler", case, c), **base,
                                                     "country": c, "profile": rails[c]}, case_id=case_id)

    # ---- the parallel part: an ADK workflow ---------------------------------------------------------------
    async def get_rows(group_id: str) -> dict:
        """Returns the statement lines of one payment group, with dates, amounts and where they are printed."""
        g = next((x for x in groups if x.id == group_id), None)
        if g is None:
            return {"rows": []}
        rows = []
        for tid in g.transaction_ids:
            t = txn_by_id.get(tid)
            if t:
                paper = snap.paper(t.paper_id)
                rows.append({"date": t.date.isoformat(), "narration": t.narration, "amount": str(t.amount),
                             "direction": t.direction, "paper": paper.title if paper else t.paper_id,
                             "after_death": t.after_death})
        return {"rows": rows}

    async def search_knowledge(query: str) -> dict:
        """Searches the shared knowledge base for rules that apply, returning assertion ids and quotes."""
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
        scored = []
        for n, a in enumerate(await k.assertions()):
            hay = (a.text + " " + a.topic + " " + a.id).lower()
            score = sum(w in hay for w in words)
            if score >= 2:                  # the case's own countries' rules first, then the closest match
                home = (a.jurisdiction or "").split("-")[0] in countries
                scored.append((not home, -score, n, {"id": a.id, "text": a.text, "quote": a.quote,
                                                     "jurisdiction": a.jurisdiction}))
        return {"assertions": [hit for *_, hit in sorted(scored)[:10]]}

    packs_by_id = {gp["group"]["id"]: gp["group"] for gp in group_packs}

    def interpretations_to_hypotheses(node_input: list) -> dict:
        joined = [{**packs_by_id.get(g.get("group_id"), {}), **g} for g in (node_input or [])]
        return {"key": pack_key("hypothesis_generator", case, str(len(groups))), **base,
                "groups": joined, "papers": [{"id": p.id, "title": p.title, "doc_type": p.doc_type,
                                                               "institution_id": p.institution_id} for p in snap.papers],
                "holdings": [{"id": h.id, "kind": h.kind, "institution_id": h.institution_id, "label": h.label}
                             for h in snap.holdings],
                "coverage": coverage}

    def explode(node_input: dict) -> list:
        return [{"key": pack_key("investigator", case, h["key"]), **base, "hypothesis": h,
                 "deceased": {"name": case.title, "died_on": case.died_on.isoformat() if case.died_on else None}}
                for h in node_input.get("hypotheses", [])]

    def to_critic(node_input: list) -> list:
        return [{"key": pack_key("devils_advocate", case, inv["key"]), **base, "investigation": inv}
                for inv in node_input]

    interpreter = deps.models.agent("narration_interpreter")
    generator = deps.models.agent("hypothesis_generator")
    investigator = deps.models.agent("investigator", tools=[get_rows, search_knowledge])
    critic = deps.models.agent("devils_advocate")

    def seed(node_input: Any) -> list:
        return group_packs

    wf = Workflow(name="discovery", edges=[(
        "START", seed, agent_node(deps, interpreter, parallel_worker=True, max_parallel_workers=12),
        interpretations_to_hypotheses, agent_node(deps, generator), explode,
        agent_node(deps, investigator, tools=True, parallel_worker=True, max_parallel_workers=10), to_critic,
        agent_node(deps, critic, parallel_worker=True, max_parallel_workers=10))])
    try:
        out = await run_workflow(deps, wf, {"key": pack_key("discovery", case)}, case_id=case_id)
        interps = [as_model("narration_interpreter", x) for x in out.get("discovery@1/narration_interpreter@1") or []]
        hyps_raw = out.get("discovery@1/hypothesis_generator@1") or {}
        hyps_model = as_model("hypothesis_generator", hyps_raw) if hyps_raw else None
        invs = [as_model("investigator", x) for x in out.get("discovery@1/investigator@1") or []]
        crits = [as_model("devils_advocate", x) for x in out.get("discovery@1/devils_advocate@1") or []]
    except (ValidationError, json.JSONDecodeError, Unusable, AgentError) as e:
        # One unusable answer in a parallel step stops the workflow; the same steps then run one agent call at a
        # time, each with its own second chance (call_agent sends an unusable answer back once).
        log.warning("discovery workflow for %s stopped (%s); running its steps one call at a time", case_id, e)
        await deps.activity(case_id, MODULE, "One step gave an answer that could not be used, so discovery ran "
                            "again one step at a time.", kind="check")
        tools = [get_rows, search_knowledge]
        sem = asyncio.Semaphore(6)

        async def one(name: str, pack: dict, with_tools: bool = False) -> Any:
            async with sem:
                return await call_agent(deps, name, pack, tools=tools if with_tools else None, case_id=case_id)

        interps = list(await asyncio.gather(*[one("narration_interpreter", gp) for gp in group_packs]))
        hyps_model = await call_agent(deps, "hypothesis_generator",
                                      interpretations_to_hypotheses([x.model_dump() for x in interps]),
                                      case_id=case_id)
        invs = list(await asyncio.gather(*[one("investigator", pk, True)
                                           for pk in explode(hyps_model.model_dump())]))
        crits = list(await asyncio.gather(*[one("devils_advocate", pk)
                                            for pk in to_critic([x.model_dump() for x in invs])]))

    by_group = {g.id: g for g in groups}
    for it in interps:
        g = by_group.get(it.group_id)
        if g:
            g.counterparty, g.counterparty_kind = it.counterparty, it.counterparty_kind
            g.interpretation, g.alternatives = it.interpretation, it.alternatives
    await deps.cases.put_many(case_id, "groups", list(by_group.values()))

    hyps = hyps_model.hypotheses if hyps_model else []
    cov_items = [it for c in countries for it in await k.coverage(c)]
    inv_by_key = {x.key: x for x in invs}
    crit_by_key = {x.key: x for x in crits}
    existing = {x.id: x for x in snap.leads}
    papers_by_id = {x.id: x for x in snap.papers}
    leads: list[Lead] = []
    dropped: list[str] = []
    for h in hyps:
        gids, pids, grounded = _evidence_of(h.group_ids, h.paper_ids, by_group, papers_by_id)
        if not grounded:
            dropped.append(h.title)       # a guess from what families usually hold, with nothing in the papers behind it
            continue
        inv = inv_by_key.get(h.key)
        crit = crit_by_key.get(h.key)
        lid = stable_id("lead", case_id, h.key)
        # a later run of discovery names the same thing under another key: the lead found before is kept and updated
        prev = existing.get(lid) or _covering(h.title, gids, list(existing.values()), kind=h.kind)
        signals = inv.signals.model_dump(exclude_none=True) if inv else {}
        p = deps.calibrator.probability("discovery", signals)
        if crit and crit.downgrade:
            p = min(p, 0.69)
        lead = prev or Lead(id=lid, case_id=case_id, kind=h.kind, category=h.category, title=h.title, sentence="")
        lead.kind, lead.category, lead.title = h.kind, h.category, h.title
        lead.holding_kind = _valid_kind(h.holding_kind) or lead.holding_kind
        lead.category = canonical_category(h.category, lead.holding_kind, cov_items)
        lead.title_local = h.title_local or lead.title_local
        lead.group_ids = gids
        lead.paper_ids = pids
        lead.institution_id = h.institution_id or lead.institution_id or \
            await _institution_of(k, gids, by_group, countries, case_id=case_id)
        if inv:
            lead.sentence, lead.investigator_note = inv.sentence, inv.investigator_note
            lead.sentence_local = inv.sentence_local or lead.sentence_local
            lead.how_to_confirm, lead.next_step_note, lead.cites = inv.how_to_confirm, inv.next_step_note, inv.cites
            if inv.value and lead.value is None:
                lead.value = _amount(inv.value, case.country_codes[0] if case.country_codes else None)
        if crit:
            lead.critic_note = crit.critic_note
        lead.probability = round(p, 3)
        lead.signals = {k_: str(v) for k_, v in signals.items()}
        lead.label = lead_label(p)  # type: ignore[assignment]
        lead.lang_local = (case.languages or ["en"])[0] if (lead.title_local or lead.sentence_local) else ""
        lead.evidence = _lead_evidence(lead, by_group, txn_by_id, snap)
        lead.currency = next((by_group[g].currency for g in lead.group_ids if g in by_group), None)
        if any(x.id == lead.id for x in leads) or _covered(lead.title, lead.group_ids, leads, kind=lead.kind):
            continue                      # two hypotheses for one thing (a live model proposed a deposit twice)
        leads.append(lead)
    if dropped:
        log.info("discovery for %s: %d hypotheses had no evidence in the case and were left out: %s", case_id,
                 len(dropped), "; ".join(dropped))
    # debts
    debts = await call_agent(deps, "liability_scout", {"key": pack_key("liability_scout", case, str(len(groups))),
                                                       **base, "groups": [g.model_dump(mode="json", exclude=(
                                                           {"transaction_ids"} if len(all_groups) > MAX_GROUPS
                                                           else None)) for g in groups],
                                                       "leads": [x.key for x in hyps]}, case_id=case_id)
    for d in debts.debts:
        lid = stable_id("lead", case_id, d.key)
        gids = [g for g in d.group_ids if g in by_group]
        if not gids or any(x.id == lid for x in leads) or _covered(d.title, gids, leads):
            continue                      # no payments behind it, or already proposed under another name
        # a subscription or mandate that keeps taking money is stopped, not owed
        kind = "stop_payment" if (d.category or "") == "subscription" else "debt"
        prev = existing.get(lid) or _covering(d.title, gids, list(existing.values()))
        if prev is not None and (prev.kind != kind or any(x.id == prev.id for x in leads)):
            continue                      # an earlier run found these payments as something else
        lead = prev or Lead(id=lid, case_id=case_id, kind=kind, category=d.category or "loan", title=d.title,
                            sentence=d.sentence)
        lead.group_ids, lead.cites = gids, d.cites
        lead.institution_id = d.institution_id or lead.institution_id or \
            await _institution_of(k, gids, by_group, countries, case_id=case_id)
        lead.holding_kind = _valid_kind(d.holding_kind) or lead.holding_kind
        lead.category = canonical_category(d.category or lead.category, lead.holding_kind, cov_items)
        lead.title_local, lead.sentence_local = d.title_local, d.sentence_local
        lead.lang_local = (case.languages or ["en"])[0] if (d.title_local or d.sentence_local) else ""
        if d.monthly:
            lead.monthly = _amount(d.monthly, case.country_codes[0] if case.country_codes else None)
        lead.probability, lead.label = 0.97, "sure"
        lead.evidence = _lead_evidence(lead, by_group, txn_by_id, snap)
        leads.append(lead)
    await name_institutions(deps, case, leads, by_group)
    cov = await call_agent(deps, "coverage_auditor", {"key": pack_key("coverage_auditor", case, str(len(leads))), **base,
                                                      "coverage": coverage, "holdings": [h.kind for h in snap.holdings],
                                                      "leads": [x.category for x in leads]}, case_id=case_id)
    case = await deps.cases.get_case(case_id)
    case.coverage = [c.model_dump() for c in cov.items]
    await deps.cases.put_case(case)
    await deps.cases.put_many(case_id, "leads", leads)
    waiting = await _leads_that_wait(deps, case, leads, snap)
    for lead in leads:
        if lead.status == "open" and lead.family_mark is None and lead.id not in waiting:
            await open_checkpoint(deps, case, code="C4", kind="lead", title=lead.title,
                                  question="Do you know about this?", options=[("know", "We know this"),
                                                                               ("check", "Check this"),
                                                                               ("not_relevant", "Not relevant")],
                                  subject_ref=f"lead:{lead.id}", why=lead.sentence)
    n_new = sum(1 for x in leads if x.id not in existing)
    if n_new:
        await deps.activity(case_id, MODULE, f"Found {n_new} thing{'s' if n_new != 1 else ''} the papers point to that "
                            "nobody listed. Each was looked into by its own investigator, then questioned by a critic.",
                            agent="investigator", kind="done")
    deps.changed(case_id, "leads", "groups", "holdings", "checkpoints")
    return leads


def _valid_kind(kind: str | None) -> str | None:
    """A model's holding_kind, when it is one the money map knows; otherwise none (the category stands in)."""
    from typing import get_args

    from ..domain.case import HoldingKind
    return kind if kind in set(get_args(HoldingKind)) else None


def canonical_category(category: str, holding_kind: str | None, items: list[dict]) -> str:
    """A model's category in the knowledge's own words: a coverage item's name becomes the category it stands for
    (a live run wrote "lockers" for "locker"), and a plural its singular, when that is a category the knowledge has.
    A lead category the plans know (planning.LEAD_KINDS: "employer", "gold") is already in the knowledge's words."""
    from .planning import LEAD_KINDS
    known = {c for it in items for c in it.get("categories", [])} | set(LEAD_KINDS)
    if not category or category in known:
        return category
    item = next((it for it in items if it.get("id") == category), None)
    if item is not None:
        cats = item.get("categories", [])
        if holding_kind in cats:
            return str(holding_kind)
        if len(cats) == 1:
            return cats[0]
    if category.endswith("s") and category[:-1] in known:
        return category[:-1]
    return category


# the kind of institution that holds (or is owed) each kind of holding, as a hint for naming it
_HOLDER_KIND = {"savings": "bank", "current": "bank", "fixed_deposit": "bank", "recurring_deposit": "bank", "nre": "bank",
                "nro": "bank", "locker": "bank", "life_policy": "insurer", "accident_cover": "insurer",
                "government_cover": "government", "mutual_fund": "fund_house", "demat": "depository",
                "shares_physical": "registrar", "provident_fund": "pension", "pension": "pension",
                "family_pension": "pension", "cpf": "pension", "loan": "lender", "card_loan": "lender",
                "credit_card": "bank", "employer_benefit": "employer", "salary": "employer", "chit_fund": "chit_fund",
                "social_security": "government"}


async def name_institutions(deps: Any, case: Any, leads: list[Lead], by_group: dict) -> None:
    """Each lead the family could claim or settle needs the institution behind it. Where the directory has none, the
    payee the payments name (or the institution the lead's own title names) is identified on the web, a few at a time
    and at most a dozen per run; a lead whose payee is only a bare name keeps no institution, and its card asks the
    family where it is held."""
    from .institutions import ORG_COUNTERPARTIES, Lookups, looks_like_org, resolve
    from .planning import holding_kind_of
    lookups = Lookups()

    async def one(lead: Lead) -> None:
        if lead.institution_id or lead.kind in ("stop_payment", "refund") or lead.status == "closed":
            return
        hk = holding_kind_of(lead)
        if hk is None:
            return
        hint = _HOLDER_KIND.get(hk)
        names: list[tuple[str, str | None]] = []
        for gid in lead.group_ids:
            g = by_group.get(gid)
            if g is not None and g.counterparty and (g.counterparty_kind in ORG_COUNTERPARTIES
                                                     or looks_like_org(g.counterparty)):
                names.append((g.counterparty, g.counterparty_kind))
        if looks_like_org(lead.title):
            names.append((lead.title, None))
        tried: set[str] = set()
        for name, cp_kind in names:
            if name.lower() in tried:
                continue
            tried.add(name.lower())
            what = (f"{lead.title}: the payments go to or come from {name}" if cp_kind else
                    f"the institution named in “{lead.title}”")
            if hk == "provident_fund":
                what += ". The family will claim the person's provident fund; name the body they claim it from"
            inst = await resolve(deps, case, name, kind=cp_kind if cp_kind in ("employer",) and hk != "provident_fund"
                                 else hint, context=what, source=f"named in the payments behind “{lead.title}”",
                                 lookups=lookups)
            if inst is not None:
                lead.institution_id = inst.id
                return

    await asyncio.gather(*(one(lead) for lead in leads))
    if lookups.done:
        await deps.activity(case.id, MODULE, f"Named the institution behind what was found: looked up "
                            f"{lookups.done} name{'s' if lookups.done != 1 else ''} the directory did not have"
                            + (f"; {lookups.skipped} more are kept as printed for now" if lookups.skipped else "")
                            + ".", agent="institution_researcher", kind="research")


async def _institution_of(k: Any, group_ids: list[str], by_group: dict, countries: list[str], *,
                          case_id: str | None = None) -> str | None:
    """The one institution every payment of a lead names (its narration matched against the knowledge's names and
    statement tokens), when the model left it out; None when the payments name none, or more than one."""
    found: set[str] = set()
    for gid in group_ids:
        g = by_group.get(gid)
        if g is None:
            continue
        hit = None
        for text in (g.label, g.counterparty):       # the narration first, then the payee the interpreter named
            for country in countries or [None]:
                inst, score = await k.find_institution(text, country, case_id=case_id) if text else (None, 0.0)
                if inst is not None and score >= 0.9:
                    hit = inst.id
                    break
            if hit:
                break
        if hit:
            found.add(hit)
    return found.pop() if len(found) == 1 else None


# Papers that say who someone is, or that they died, but nothing about what they held: no lead rests on one alone.
NOT_EVIDENCE = {"death_certificate", "identity_card", "birth_certificate", "marriage_certificate"}


def _evidence_of(group_ids: list[str], paper_ids: list[str], by_group: dict,
                 papers: dict) -> tuple[list[str], list[str], bool]:
    """The payment groups and papers a hypothesis cites that are in the case, and whether any of them can show a
    holding. A live model proposed a locker, a provident fund and a post office account from a death certificate
    alone, because families often have them; those are the coverage auditor's questions, not leads."""
    gids = [g for g in group_ids if g in by_group]
    pids = [p for p in paper_ids if p in papers]
    grounded = bool(gids) or any(papers[p].doc_type not in NOT_EVIDENCE for p in pids)
    return gids, pids, grounded


def _covering(title: str, group_ids: list[str], leads: list[Lead], kind: str | None = None) -> Lead | None:
    """The lead that already proposes this: the same title, or the same payments for the same kind of thing."""
    name = " ".join(title.lower().split())
    groups = set(group_ids)
    return next((x for x in leads if " ".join(x.title.lower().split()) == name or
                 (groups and set(x.group_ids) == groups and (kind is None or x.kind == kind))), None)


def _covered(title: str, group_ids: list[str], leads: list[Lead], kind: str | None = None) -> bool:
    return _covering(title, group_ids, leads, kind) is not None


async def _leads_that_wait(deps: Any, case: Any, leads: list[Lead], snap: Any) -> set[str]:
    """The decision controller on open leads (AL_DECISIONS=controller). The family can usually say at once whether they
    know of something, so most leads are asked about (checkpoint C4); a lead about an institution whose reply to a
    claim is already due waits for that reply, which will say; a lead is never acted on without the family hearing of
    it. Every decision and its scores go to the trace. Returns the leads that wait."""
    if getattr(deps.settings, "decisions", "controller") != "controller":
        return set()
    from ..domain.decision import Fork, choose
    today = deps.today_for(case)
    due: dict[str, date] = {}
    for c in snap.claims:
        if c.status not in ("submitted", "acknowledged") or not c.institution_id:
            continue
        for clock in c.clocks:                # the institution's own clock: when its answer is due
            if clock.owner == "institution" and clock.status == "running" and clock.due_on:
                due[c.institution_id] = min(due.get(c.institution_id, clock.due_on), clock.due_on)
    wait: set[str] = set()
    for lead in leads:
        if lead.status != "open" or lead.family_mark is not None:
            continue
        reply_in = (due[lead.institution_id] - today).days if lead.institution_id in due else None
        fork = Fork(tier="B", p_now=float(lead.probability), p_if_asked=0.95, p_if_researched=0.0,
                    research_minutes=0.0, stakes=0.8 if lead.kind in ("asset", "entitlement") else 0.5,
                    family_can_answer=True,
                    event_due_days=float(reply_in) if reply_in is not None and reply_in >= 0 else None,
                    p_if_waited=0.97 if reply_in is not None and reply_in >= 0 else 0.0)
        move, scores = choose(fork)
        if move == "act":
            move = "ask"                  # told, never assumed: the family hears of every lead
        await deps.trace.record({"type": "decision", "id": new_id("dc"), "case_id": case.id, "module": MODULE,
                                 "subject": f"lead:{lead.id}", "move": move, "scores": scores,
                                 "p_now": round(fork.p_now, 3), "event_due_days": fork.event_due_days})
        if move == "wait":
            wait.add(lead.id)
            lead.next_step_note = (f"Waiting for the reply on the claim already with this institution, due "
                                   f"{due[lead.institution_id].isoformat()}; it will say.")
            await deps.cases.put(case.id, "leads", lead)
            await deps.activity(case.id, MODULE, f"Not asking about “{lead.title}” yet: the institution's reply to the "
                                "claim already with it is due soon and will settle it.", kind="decision")
    return wait


def _amount(text: str | None, country: str | None) -> Decimal | None:
    """An amount from words such as '₹2,00,000' or 'about 38,200'; None when there is no number in it."""
    from .reading import extract_amount
    v = extract_amount(text, country) if text else None
    return Decimal(v) if v is not None else None


def _lead_evidence(lead: Lead, groups: dict, txns: dict, snap: Any) -> list[Evidence]:
    ev = []
    for gid in lead.group_ids:
        g = groups.get(gid)
        if not g:
            continue
        for tid in g.transaction_ids[:4]:
            t = txns.get(tid)
            if not t:
                continue
            paper = snap.paper(t.paper_id)
            page = next((p.number for p in paper.pages if p.id == t.page_id), None) if paper else None
            line = next((ln for ln in [t.line_id] if ln), None)
            ev.append(Evidence(kind="row", ref=t.id, paper_id=t.paper_id, page_id=t.page_id,
                               line_ids=[line] if line else [], quote=f"{t.date.strftime('%d-%m-%y')} | {t.narration} | "
                               f"{t.amount:,.2f}", label=f"{paper.title if paper else 'Statement'}, page {page}"))
    return ev


async def mark_lead(deps: Any, case_id: str, lead_id: str, mark: str, *, by: str | None, value: str | None = None,
                    note: str | None = None) -> Lead:
    """The family marks a lead (checkpoint C4): we know this, check this, or not relevant."""
    lead: Lead = await deps.cases.get(case_id, "leads", lead_id)
    lead.family_mark, lead.marked_by = mark, by  # type: ignore[assignment]
    if value:
        lead.value = _amount(value, None)   # "about ₹38,200 left": the family's figure, shown as theirs
    if mark == "know":
        lead.status = "confirmed"
    elif mark == "not_relevant":
        lead.status, lead.closed_reason = "closed", note or "Marked not relevant by the family."
    await deps.cases.put(case_id, "leads", lead)
    for cp in await deps.cases.list(case_id, "checkpoints"):
        if cp.subject_ref == f"lead:{lead_id}" and cp.status == "open":
            cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", mark, by, deps.now()
            cp.answer_note = note
            await deps.cases.put(case_id, "checkpoints", cp)
    await deps.activity(case_id, MODULE, f"{lead.title}: marked “{ {'know': 'we know this', 'check': 'check this', 'not_relevant': 'not relevant'}[mark] }”"
                        f"{' - ' + note if note else ''}.", kind="decision")
    deps.changed(case_id, "leads", "checkpoints")
    return lead


def _one_line_each(coverage: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """What families in each country usually hold, with a kind that two countries share (bank accounts in India
    and in Singapore) kept as one line, under the first country, that counts the other's holdings too."""
    seen: dict[str, dict] = {}
    out: dict[str, list[dict]] = {}
    for country, items in coverage.items():
        out[country] = []
        for it in items or []:
            first = seen.get(it["id"])
            if first is None:
                first = seen[it["id"]] = {**it, "categories": list(it.get("categories", []))}
                out[country].append(first)
            else:
                first["categories"] += [c for c in it.get("categories", []) if c not in first["categories"]]
                first["ask"] = first.get("ask") or it.get("ask")
    return out


async def answer_coverage(deps: Any, case_id: str, item_id: str, answer: str, *, by: str | None = None) -> None:
    """The family answers a "did they have…?" question from the coverage list. A yes becomes a lead to check;
    a no closes the line for good."""
    case = await deps.cases.get_case(case_id)
    item = next((c for c in case.coverage if c.get("id") == item_id), None)
    if item is None:
        raise KeyError(f"no coverage item {item_id}")
    who = await deps.cases.get(case_id, "people", by) if by else None
    name = (who.name_en or who.display_name).split()[0] if who else "The family"
    label = item.get("label", item_id)
    if answer == "no":
        item.update(status="not_applicable", detail=f"{name} says there was none.", ask=None)
    elif answer == "unsure":
        item.update(detail=f"{name} is not sure. It is asked again if a paper mentions one.", ask=None)
    else:
        lead = Lead(id=stable_id("lead", case_id, "coverage", item_id), case_id=case_id, kind="asset", category=item_id,
                    title=label, sentence=f"{name} says there is one. No paper shows it yet, so a passbook, a "
                                          f"certificate or a letter about it is the way to find where it is held.",
                    probability=0.9, label="likely", status="open", family_mark="check", marked_by=by,
                    how_to_confirm="A passbook, certificate or letter that names the institution.")
        await deps.cases.put(case_id, "leads", lead)
        item.update(status="lead", detail=f"{name} says there is one", ask=None)
    case.updated_at = deps.now()
    await deps.cases.put_case(case)
    await deps.activity(case_id, MODULE, f"{label}: {name} answered “{ {'yes': 'yes', 'no': 'no', 'unsure': 'not sure'}.get(answer, answer) }”.",
                        kind="decision")
    deps.changed(case_id, "leads", "case")


async def close_lead(deps: Any, case_id: str, lead_id: str, reason: str, cites: list[str] | None = None) -> None:
    lead: Lead = await deps.cases.get(case_id, "leads", lead_id)
    lead.status, lead.closed_reason = "closed", reason
    lead.cites = sorted(set(lead.cites) | set(cites or []))
    await deps.cases.put(case_id, "leads", lead)
    for cp in await deps.cases.list(case_id, "checkpoints"):
        if cp.subject_ref == f"lead:{lead_id}" and cp.status == "open":
            cp.status, cp.answer_note = "expired", reason
            await deps.cases.put(case_id, "checkpoints", cp)
    await deps.activity(case_id, MODULE, f"Closed: {lead.title}. {reason}", kind="decision", cites=cites or [])
