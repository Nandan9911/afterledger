"""Module 2: whose rules apply.

The connecting factors (where the person died and lived, their nationalities, the country of each
holding and paper) are listed, the conflict-of-laws rules researched and checked against their
quotes, and one leg built per holding. Where two countries' rules point different ways, the map
says so, shows both sources, and the plans still prepare each institution's steps.
"""
from __future__ import annotations

from typing import Any

from ..agents.runner import call_agent
from ..domain.case import JurisdictionLeg, JurisdictionMap
from ..domain.countries import country_name
from .common import case_block, knowledge, pack_key, snapshot

MODULE = "jurisdiction"


async def map_jurisdiction(deps: Any, case_id: str) -> JurisdictionMap:
    snap = await snapshot(deps, case_id, ["people", "holdings", "papers"])
    case = snap.case
    k = knowledge(deps)
    dec = snap.deceased
    insts = {}
    for h in snap.holdings:
        if h.institution_id and h.institution_id not in insts:
            insts[h.institution_id] = await k.institution(h.institution_id)
    base = {"case": case_block(case),
            "deceased": {"name": dec.display_name if dec else case.title, "died_in": case.died_in,
                         "death_country": (case.situation.country_codes[0] if case.situation and case.situation.country_codes
                                           else None),
                         "nationalities": dec.nationalities if dec else [],
                         "residences": [r.model_dump(mode="json") for r in dec.residences] if dec else []},
            "holdings": [{"id": h.id, "label": h.label, "kind": h.kind,
                          "country": h.country or (insts.get(h.institution_id).country if insts.get(h.institution_id) else None),
                          "institution": insts.get(h.institution_id).name if insts.get(h.institution_id) else None}
                         for h in snap.holdings if h.status != "closed"],
            "papers": [{"id": p.id, "title": p.title, "country": p.country} for p in snap.papers]}
    factors = await call_agent(deps, "connecting_factors", {"key": pack_key("connecting_factors", case,
                                                                            str(len(snap.holdings))), **base},
                               case_id=case_id)

    async def search_knowledge(query: str, jurisdiction: str | None = None) -> dict:
        """Searches the shared knowledge base for rules (assertions) matching the query."""
        found = await k.assertions_for(jurisdiction=jurisdiction, topic_prefix=None)
        words = [w for w in query.lower().split() if len(w) > 3]
        hits = [a for a in found if any(w in (a.text + " " + a.topic).lower() for w in words)][:12]
        return {"assertions": [{"id": a.id, "text": a.text, "quote": a.quote, "jurisdiction": a.jurisdiction,
                                "level": a.level} for a in hits]}

    research = await call_agent(deps, "jurisdiction_researcher", {
        "key": pack_key("jurisdiction_researcher", case, str(len(snap.holdings))), **base,
        "factors": [f.model_dump() for f in factors.factors]}, tools=[search_knowledge], case_id=case_id)
    # citation check by code: every cited assertion must exist and its quote must be on its page
    checked: list[str] = []
    for finding in research.findings:
        for aid in finding.assertion_ids:
            a = await k.assertion(aid)
            ok = False
            if a is not None:
                ok, _ = await k.verify_quote(a)
            await deps.ledger(case_id, f"assertion:{aid}", "quote_check", "pass" if ok else "fail",
                              method="quote found word for word on the page snapshot")
            if ok:
                checked.append(aid)
    verdict = await call_agent(deps, "jurisdiction_verifier", {
        "key": pack_key("jurisdiction_verifier", case, str(len(snap.holdings))), **base,
        "findings": [f.model_dump() for f in research.findings], "quotes_found": checked}, case_id=case_id)
    plan = await call_agent(deps, "jurisdiction_planner", {
        "key": pack_key("jurisdiction_planner", case, str(len(snap.holdings))), **base,
        "findings": [f.model_dump() for f in research.findings], "verified": verdict.keep}, case_id=case_id)
    contested = plan.contested
    points = sorted({f.points_to.split("-")[0] for f in research.findings if f.points_to not in ("", "contested")})
    if len({f.points_to for f in research.findings if f.points_to != "contested"}) > 1:
        adj = await call_agent(deps, "jurisdiction_adjudicator", {
            "key": pack_key("jurisdiction_adjudicator", case), **base,
            "findings": [f.model_dump() for f in research.findings]}, case_id=case_id)
        contested = adj.contested
        if not (case.jurisdiction and case.jurisdiction.contested == contested):
            await deps.activity(case_id, MODULE, _disagree(points) if contested else adj.decision,
                                agent="jurisdiction_adjudicator", kind="decision" if not contested else "warning",
                                cites=adj.contested_ids)
    lang = (case.languages or ["en"])[0]
    legs = [JurisdictionLeg(**leg.model_dump()) for leg in plan.legs]
    summary, summary_local, succession = plan.summary, plan.summary_local, plan.succession_law
    if contested:
        # The family is never told that one law decides when the countries' rules disagree (a model wrote "Japanese
        # law decides who inherits" in a live run): the words for a contested case are code's, not a model's.
        succession, summary = "contested", _contested_summary(points)
        summary_local = await _in_language(deps, summary, lang)
        legs = [leg.model_copy(update={"succession_law": "contested", "contested": True}) for leg in legs]
    jm = JurisdictionMap(domestic=len({leg.procedure_jurisdiction.split('-')[0] for leg in plan.legs}) <= 1,
                         countries=sorted({leg.procedure_jurisdiction for leg in plan.legs} | set(case.country_codes)),
                         succession_law=succession, contested=contested, legs=legs, summary=summary,
                         summary_local=summary_local, lang_local=lang if summary_local else "",
                         cites=sorted(set(plan.cites) | set(checked)))
    case = await deps.cases.get_case(case_id)
    # said already: the same words about the same question (a new holding adds a leg, not a new finding)
    unchanged = case.jurisdiction is not None and case.jurisdiction.summary == jm.summary and \
        case.jurisdiction.contested == jm.contested
    case.jurisdiction = jm
    case.country_codes = sorted(set(case.country_codes) | {c.split("-")[0] for c in jm.countries})
    await deps.cases.put_case(case)
    if not unchanged:
        await deps.activity(case_id, MODULE, summary or "Worked out whose rules apply to each holding.",
                            agent="jurisdiction_planner", kind="warning" if contested else "step")
    deps.changed(case_id, "case")
    return jm


def _countries(points: list[str]) -> str:
    names = [country_name(c) for c in points] or ["two countries"]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _contested_summary(points: list[str]) -> str:
    return (f"The rules of {_countries(points)} disagree about which law decides who inherits. Both sources are "
            "shown, and each institution's steps are prepared; a lawyer should confirm which law applies before any "
            "institution pays.")


def _disagree(points: list[str]) -> str:
    return (f"The rules of {_countries(points)} point to different laws for who inherits. Neither is set aside here: "
            "both are shown, and a lawyer should confirm which applies.")


async def _in_language(deps: Any, text: str, lang: str) -> str:
    """The family's own language for a sentence code wrote; empty when no translation can be had."""
    if not lang or lang.split("-")[0] == "en":
        return ""
    try:
        out = (await deps.translator.translate([text], lang, "en"))[0]
    except Exception:  # the English stays; the page says so in its language switch
        return ""
    return out if out and out != text else ""
