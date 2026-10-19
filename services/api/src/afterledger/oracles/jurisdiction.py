"""Offline answers for the jurisdiction agents."""
from __future__ import annotations

from ..adapters.llm.models import OracleRequest, ToolCalls
from ..domain.countries import country_name
from . import Ctx, oracle

_SUCCESSION_RULES = {
    "IN": [("in_hsa_s8_class1", "IN", "A Hindu who dies without a will: the heirs come from the Hindu Succession Act.")],
    "DE": [("eu_650_art21_1", "DE", "EU Succession Regulation: the law of the last habitual residence.")],
    "JP": [("jp_national_law", "JP", "Japan's Act on General Rules: the law of the deceased's nationality.")],
}


@oracle("connecting_factors")
def connecting_factors(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    factors = []
    if fam:
        d = fam.deceased
        factors.append({"kind": "death_place", "value": d.get("death_country", ""), "evidence": "death certificate"})
        for r in d.get("residences", []):
            factors.append({"kind": "residence", "value": r["country"], "evidence": "the family"})
        for n in d.get("nationalities", []):
            factors.append({"kind": "nationality", "value": n, "evidence": "the family"})
        if d.get("personal_law"):
            factors.append({"kind": "personal_law", "value": d["personal_law"], "evidence": "the family"})
    else:
        dec = p.get("deceased") or {}
        if dec.get("death_country"):
            factors.append({"kind": "death_place", "value": dec["death_country"], "evidence": "the family"})
    for h in p.get("holdings", []):
        if h.get("country"):
            factors.append({"kind": "holding_country", "value": h["country"], "evidence": h.get("label", "")})
    return {"factors": factors}


@oracle("jurisdiction_researcher")
def jurisdiction_researcher(ctx: Ctx, req: OracleRequest) -> dict:
    fam = ctx.family(req)
    if fam and (fam.get("jurisdiction") or {}).get("findings"):
        if not req.tool_results:
            return ToolCalls([("search_knowledge", {"query": "succession law applicable habitual residence nationality heirs"})])
        return {"findings": fam["jurisdiction"]["findings"], "gaps": fam["jurisdiction"].get("gaps", [])}
    p = ctx.payload(req)
    countries = {f["value"] for f in p.get("factors", []) if f["kind"] in ("death_place", "residence", "nationality")}
    findings = []
    for c in sorted(countries):
        for aid, to, rule in _SUCCESSION_RULES.get(c, []):
            findings.append({"assertion_ids": [aid], "rule": rule, "points_to": to})
    return {"findings": findings, "gaps": [] if findings else ["No conflict-of-laws rule found offline for these countries."]}


@oracle("jurisdiction_verifier")
def jurisdiction_verifier(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    found = set(p.get("quotes_found", []))
    keep = [aid for f in p.get("findings", []) for aid in f.get("assertion_ids", []) if aid in found]
    return {"contested": False, "decision": f"{len(keep)} finding(s) supported by their quotes.", "keep": keep,
            "contested_ids": [], "reasons": ""}


@oracle("jurisdiction_planner")
def jurisdiction_planner(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    if fam and fam.get("jurisdiction"):
        j = fam["jurisdiction"]
        legs = []
        for h in p.get("holdings", []):
            leg = next((x for x in j.get("legs", []) if h.get("country") and x.get("country") == h["country"]), None)
            if leg is None:
                continue
            legs.append({"holding_id": h["id"], "label": h["label"], "succession_law": leg.get("succession_law"),
                         "procedure_jurisdiction": leg["country"], "papers_from": leg.get("papers_from", []),
                         "certification": leg.get("certification", []), "cites": leg.get("cites", []),
                         "contested": leg.get("contested", False), "note": leg.get("note", "")})
        return {"legs": legs, "succession_law": j.get("succession_law"), "contested": j.get("contested", False),
                "summary": j.get("summary", ""), "summary_local": j.get("summary_local", ""), "cites": j.get("cites", [])}
    legs = []
    countries = set()
    for h in p.get("holdings", []):
        c = h.get("country") or (p.get("deceased") or {}).get("death_country") or "unknown"
        countries.add(c)
        legs.append({"holding_id": h["id"], "label": h["label"], "succession_law": c, "procedure_jurisdiction": c,
                     "papers_from": [c], "certification": [], "cites": [], "contested": False, "note": ""})
    names = sorted(country_name(c) if len(c) == 2 else c for c in countries)
    summary = (f"{', '.join(names)}, for every holding so far." if countries else
               "No holdings yet; whose rules apply is worked out as they are found.")
    return {"legs": legs, "succession_law": next(iter(countries)) if len(countries) == 1 else None,
            "contested": False, "summary": summary, "cites": []}


@oracle("jurisdiction_adjudicator")
def jurisdiction_adjudicator(ctx: Ctx, req: OracleRequest) -> dict:
    fam = ctx.family(req)
    if fam and (fam.get("jurisdiction") or {}).get("adjudication"):
        return fam["jurisdiction"]["adjudication"]
    ids = [aid for f in ctx.payload(req).get("findings", []) for aid in f.get("assertion_ids", [])]
    return {"contested": True, "decision": "Two countries' rules point to different laws; both are shown and a lawyer "
            "should settle which applies.", "keep": ids, "contested_ids": ids, "reasons": ""}
