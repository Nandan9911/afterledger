"""The money map: everything held or owed, by country and institution, each amount in its own currency."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from ...modules import followup
from ...modules.common import knowledge, snapshot
from ..schemas import CountryGroup, HoldingView, InstitutionGroup, JurisdictionNote, MoneyView
from .common import chip, cite_views, institution_name, money
from .intake import pronoun
from .localize import localize
from .today import totals_view

_DEBT_WORDS = ("card", "loan", "debt", "owed", "owe", "balance is paid", "repay", "instalment", "借入", "ローン")


def _summary_for(summary: str, h: Any, claim: Any) -> str:
    """A claim's summary, cut to what concerns this one holding when the claim covers money held and money owed."""
    if claim is None or len(claim.holding_ids) < 2:
        return summary
    sentences = [s.strip() for s in re.split(r"(?<=[.。])\s*", summary) if s.strip()]
    debt = [s for s in sentences if any(w in s.lower() for w in _DEBT_WORDS)]
    rest = [s for s in sentences if s not in debt]
    pick = debt if h.direction == "debt" else rest
    return " ".join(pick) if pick else summary

CURRENCY_COUNTRY = {"INR": "IN", "JPY": "JP", "GBP": "GB", "USD": "US", "SGD": "SG", "EUR": "DE"}


def _tag(h: Any, claim: Any) -> str:
    if h.status == "closed":
        return "closed"
    if claim is not None and claim.status == "settled":
        return "paid"                     # paid to the family, or a debt the estate has paid
    if h.direction == "debt":
        return "owed"
    if h.status == "lead":
        return "lead"
    return "confirmed"


async def money_map(deps: Any, case_id: str, lang: str = "en") -> MoneyView:
    snap = await snapshot(deps, case_id)
    k = knowledge(deps)
    case = snap.case
    claimant = snap.claimant()
    his = pronoun(snap, await deps.cases.list(case_id, "utterances"))
    groups: dict[str, dict[str, InstitutionGroup]] = {}
    totals: dict[str, dict[str, Decimal]] = {}
    currency_of: dict[str, str] = {}
    for h in sorted(snap.holdings, key=lambda h: (h.direction == "debt", h.status == "lead", -(h.value or 0))):
        if h.status == "closed":
            continue
        inst = await k.institution(h.institution_id) if h.institution_id else None
        country = (h.country or (inst.country if inst else None) or CURRENCY_COUNTRY.get(h.currency) or
                   (case.country_codes or ["IN"])[0]).split("-")[0]
        currency_of.setdefault(country, h.currency)
        claim = snap.claim(h.claim_id)
        plan = snap.plan_for_claim(claim.id) if claim else None
        lead = snap.lead(h.lead_id) if h.lead_id else None
        tag = _tag(h, claim)
        if h.note:
            note = h.note
        elif lead is not None and h.status == "lead":
            note = lead.sentence
        elif plan is not None and plan.summary:
            note = _summary_for(plan.summary, h, claim)
        else:
            note = ""
        if h.value is None:
            certainty = "unknown"
        elif h.source == "lead" or h.status == "lead":
            certainty = "check"           # the family's own figure, or an estimate: shown as theirs
        else:
            certainty = "sure"
        hv = HoldingView(holding=h, value=money(h.settled_amount if tag == "paid" and h.settled_amount else h.value,
                                                h.currency),
                         certainty=certainty, tag=tag, note=note, claim_id=claim.id if claim else None,  # type: ignore[arg-type]
                         claim_status=claim.status if claim else None, plan_id=plan.id if plan else None, lead=lead)
        paper_id = next(iter([*(lead.paper_ids if lead else []), *h.paper_ids]), None)
        paper = snap.paper(paper_id) if paper_id and not inst else None
        key = h.institution_id or (f"paper:{paper.id}" if paper else f"none:{h.id}")
        name = institution_name(inst, "") if inst else (paper.title if paper else (lead.title if lead else h.label))
        grp = groups.setdefault(country, {}).setdefault(key, InstitutionGroup(
            institution_id=h.institution_id, name=inst.name if inst else name, short_name=inst.short_name if inst else None,
            kind=inst.kind if inst else None, role=f"{his} employer" if inst and inst.kind == "employer" else "",
            from_paper=paper.title if paper else None, holdings=[]))
        grp.holdings.append(hv)
        t = totals.setdefault(country, {"known": Decimal(0), "owed": Decimal(0)})
        if h.value is not None and not (h.direction == "debt" and tag == "paid"):
            t["owed" if h.direction == "debt" else "known"] += h.value
    countries = []
    order = list(dict.fromkeys([*(case.country_codes or []), *groups.keys()]))
    for country in order:
        if country not in groups:
            continue
        cur = currency_of[country]
        people_here = [p for p in snap.family() if (p.country or "").split("-")[0] == country]
        handler = next((p for p in people_here if "claimant" in p.roles), None) or \
            next((p for p in people_here if "helper" in p.roles), None) or claimant
        insts = sorted(groups[country].values(), key=lambda g: (all(hv.tag in ("owed",) for hv in g.holdings),
                                                                all(hv.tag == "lead" for hv in g.holdings)))
        countries.append(CountryGroup(country=country, currency=cur, total_known=money(totals[country]["known"], cur),
                                      owed=money(totals[country]["owed"], cur) if totals[country]["owed"] else None,
                                      claimant=chip(handler), institutions=insts))
    notes: list[JurisdictionNote] = []
    jur = case.jurisdiction
    cite_ids: set[str] = set()
    if jur is not None:
        seen: set[str] = set()
        for leg in jur.legs:
            if leg.note and leg.note not in seen and (leg.certification or leg.contested):
                seen.add(leg.note)
                notes.append(JurisdictionNote(country=leg.procedure_jurisdiction, text=leg.note, cites=leg.cites,
                                              contested=leg.contested))
                cite_ids |= set(leg.cites)
        cite_ids |= set(jur.cites)
    raw = await followup.totals(deps, case_id)
    view = MoneyView(countries=countries, totals=totals_view(raw), notes=notes,
                     summary=jur.summary if jur else "", summary_local=jur.summary_local if jur else "",
                     lang_local=jur.lang_local if jur else "", contested=bool(jur and jur.contested),
                     cites=await cite_views(deps, cite_ids, deps.today_for(case)))
    # the words the server wrote, in the page's language; names and amounts stay as they are
    holdings = [hv for c in view.countries for g in c.institutions for hv in g.holdings]
    await localize(deps, lang, [(hv.holding, "label") for hv in holdings] + [(hv, "note") for hv in holdings]
                   + [(g, "role") for c in view.countries for g in c.institutions] + [(n, "text") for n in view.notes])
    return view
