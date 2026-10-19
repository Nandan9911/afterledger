"""Unknowns resolved at run time, within bounds: an institution the directory lacks is identified (shared only when a
page confirmed it), a claim no stored plan covers has its rules researched once per situation in the background, a
related situation's checked rules are leads for one researcher only, and a research run stops adding rounds at its
cost cap."""
from __future__ import annotations

import asyncio
from typing import Any

from afterledger.domain.case import CaseFile, Claim


async def test_one_research_run_per_situation_and_no_retry_too_soon(deps: Any) -> None:
    from afterledger.modules import rules_research
    started: list[str] = []

    async def fake_run(deps_: Any, sig: str, run: Any, depth: str) -> None:
        started.append(sig)
        await asyncio.sleep(0.05)
        rules_research._inflight(deps_).pop(sig, None)
        await deps_.knowledge.put("research_attempts", sig, {"id": sig, "status": "inconclusive", "run_id": run.id,
                                                              "at": "2999-01-01T00:00:00+00:00"})

    rules_research._run = fake_run  # type: ignore[assignment]
    try:
        await deps.cases.put_case(CaseFile(id="c1", title="A", created_at=deps.now(), country_codes=["IN"]))
        a = Claim(id="claim_a", case_id="c1", institution_id="x", title="A loan", kind="debt")
        b = Claim(id="claim_b", case_id="c1", institution_id="y", title="Another loan", kind="debt")
        s1 = await rules_research.ensure(deps, "c1", a, country="IN", kind="loan")
        s2 = await rules_research.ensure(deps, "c1", b, country="IN", kind="loan")
        assert s1["status"] == s2["status"] == "running" and s1["run_id"] == s2["run_id"]
        await asyncio.gather(*deps.extras["rules_research_tasks"])
        assert started == ["IN/loan"]                       # the second claim joined the first run
        s3 = await rules_research.ensure(deps, "c1", a, country="IN", kind="loan")
        assert s3["status"] == "inconclusive" and started == ["IN/loan"]   # tried lately: not again yet
    finally:
        import importlib
        importlib.reload(rules_research)


async def test_claim_research_can_be_switched_off(deps: Any) -> None:
    from afterledger.modules import rules_research
    deps.settings = deps.settings.model_copy(update={"claim_research": "off"})
    claim = Claim(id="claim_c", case_id="c2", institution_id="x", title="A card", kind="debt")
    state = await rules_research.ensure(deps, "c2", claim, country="IN", kind="credit_card")
    assert state == {"signature": "IN/credit_card", "status": "off"}


async def test_related_rules_are_leads_for_researcher_a_only(deps: Any) -> None:
    from afterledger.modules import research
    known = await research.related_knowledge(deps, {"country": "IN", "holding_kind": "locker",
                                                    "question": "How does a family get into a bank locker?"})
    assert known and all(k["id"].startswith("in_") for k in known)
    assert any("locker" in (k["text"] + k["topic"]).lower() for k in known)
    assert not await research.related_knowledge(deps, {"country": "ZZ", "holding_kind": "locker"})

    seen: dict[str, dict] = {}
    real = research.call_agent

    async def spy(deps_: Any, name: str, pack: dict, **kw: Any) -> Any:
        if name.startswith("researcher_"):
            seen[name] = pack
        return await real(deps_, name, pack, **kw)

    research.call_agent = spy  # type: ignore[assignment]
    try:
        run = await research.research(deps, {"country": "IN", "holding_kind": "locker", "question": "A bank locker",
                                             "lang": "en", "key": "IN/locker_test"}, depth="standard")
    finally:
        research.call_agent = real  # type: ignore[assignment]
    assert seen["researcher_primary"].get("known") and "known" not in seen["researcher_second"]
    assert any(e["stage"] == "known" for e in run.events)


async def test_a_research_run_stops_adding_rounds_at_its_cost_cap(deps: Any) -> None:
    from afterledger.modules import research
    deps.settings = deps.settings.model_copy(update={"research_budget_usd": 0.01, "research_mode": "adaptive",
                                                     "research_max_rounds": 3})
    real = research._over_budget
    research._over_budget = lambda deps_, run: 0.02  # type: ignore[assignment]
    try:
        run = await research.research(deps, {"country": "FR", "holding_kind": "life_policy", "question": "x",
                                             "lang": "fr"}, depth="deep")
    finally:
        research._over_budget = real  # type: ignore[assignment]
    assert run.rounds <= 1
    assert any(e.get("stop") == "budget" for e in run.events) or run.rounds == 1


async def test_an_unknown_institution_is_kept_with_the_case_until_a_page_confirms_it(deps: Any) -> None:
    from afterledger.knowledge import learned_case_id
    from afterledger.modules.common import knowledge
    from afterledger.modules.institutions import display_name, looks_like_org, resolve, same_organisation
    case = CaseFile(id="case_x1", title="A", created_at=deps.now(), country_codes=["IN"])
    await deps.cases.put_case(case)
    inst = await resolve(deps, case, "INCRED FINANCIAL SERVICES LTD", kind="lender", context="loan instalments")
    assert inst is not None and learned_case_id(inst.id) == "case_x1" and inst.kind == "lender"
    assert inst.name == "Incred Financial Services Ltd" and not inst.fictional
    assert await knowledge(deps).institution(inst.id) == inst                   # found by every screen
    assert (await knowledge(deps).find_institution("ACH D- INCRED FINANCIAL SERVICES LTD EMI", "IN",
                                                   case_id="case_x1"))[0] == inst
    assert (await knowledge(deps).find_institution("INCRED FINANCIAL SERVICES LTD", "IN"))[0] is None  # other cases
    assert await resolve(deps, case, "RAMESH KUMAR", context="a transfer") is None   # a person: never looked up
    assert looks_like_org("Capital Float") and not looks_like_org("Sunita Devi")
    assert display_name("ICICI BANK LTD") == "ICICI Bank Ltd"
    assert same_organisation("INCRED FINANCIAL SER", "InCred Financial Services Limited")
    assert not same_organisation("YODAPLUS TECHNOLOGIES PVT LTD", "Employees' Provident Fund Organisation")


async def test_short_names_match_only_as_words(deps: Any) -> None:
    from afterledger.knowledge import name_score
    assert name_score("SBI", "NEFT/SBIN0001234/RAMESH") == 0.0       # a branch code, not the bank
    assert name_score("SBI", "SBI LIFE PREMIUM") == 0.9
    assert name_score("Bajaj Finance", "ACH BAJAJFINANCE EMI") >= 0.9
    assert name_score("ICICI Prudential Life", "ICICI PRUDENTIAL LIFE PREM") > name_score("ICICI", "x")


async def test_a_sample_case_stays_on_the_interim_plan(deps: Any) -> None:
    """Samples are scripted stories: a claim of theirs with no stored plan does not start research (research run
    while the samples are built is scripted, and its plans would exist only where they were built)."""
    from afterledger.modules import rules_research
    await deps.cases.put_case(CaseFile(id="demo-x", title="S", created_at=deps.now(), country_codes=["IN"],
                                       demo_key="x"))
    claim = Claim(id="claim_s", case_id="demo-x", institution_id="x", title="A loan", kind="debt")
    state = await rules_research.ensure(deps, "demo-x", claim, country="IN", kind="loan")
    assert state["status"] == "off" and state.get("reason") == "sample"
    assert not deps.extras.get("rules_research_tasks")
