"""Adaptive research: questions go to researchers in groups, and what a round leaves open becomes the next round's
questions, until nothing is open, the rounds run out, the same questions stay open twice, or the budget is spent."""
from __future__ import annotations

from typing import Any

from afterledger.domain.checks import quote_in_page
from afterledger.modules.research import read_page, research
from conftest import offline_settings

FR = {"country": "FR", "holding_kind": "life_policy", "lang": "fr",
      "question": "Mon père est décédé à Lyon en août. Avait-il une assurance-vie ?"}
EG = {"country": "EG", "holding_kind": "savings", "lang": "ar", "question": "ما المستندات التي يطلبها البنك؟"}


async def test_france_finds_the_missing_step_in_a_second_round(deps: Any) -> None:
    run = await research(deps, dict(FR))
    assert run.rounds == 2 and run.status == "done" and run.gold and run.gold["match"]
    rounds = [e for e in run.events if e["stage"] == "round"]
    assert rounds[0]["text"] == "Round 2: 1 question still open; searching in French."
    assert rounds[-1]["text"].startswith("Round 2: kept 1 more quote") and "nothing is left open" in rounds[-1]["text"]
    assert "fr_agira_over_10" in {x["id"] for x in run.kept}
    assert [x["id"] for x in run.dropped] == ["fr_trap_agira_delay"]           # the misquote never reaches the plan
    for item in run.kept:                                                     # every kept quote, word for word
        page = await read_page(deps, item["source_url"])
        assert page is not None and quote_in_page(item["quote"], page.text), item["id"]
    groups = {e["who"] for e in run.events if e["stage"] == "search"}
    assert groups == {"Researcher A", "Researcher B"}


async def test_egypt_stops_when_a_round_makes_no_progress(deps: Any) -> None:
    run = await research(deps, dict(EG))
    assert run.rounds == 2 and run.plan and run.plan["unverified"] is True
    stop = [e for e in run.events if e.get("stop")]
    assert [e["stop"] for e in stop] == ["no_progress"]
    assert run.plan["steps"][0]["id"] == "ask_bank"                            # it starts by asking the bank


async def test_a_spent_budget_stops_research_after_the_first_round(tmp_path: Any) -> None:
    from afterledger.agents.meter import Usage
    from afterledger.domain.case import CaseFile
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, case_budget_usd=0.01))
    await deps.cases.put_case(CaseFile(id="case-b", title="Test", created_at=deps.now()))
    deps.meter.by_case["case-b"] = Usage(calls=50, cost_usd=0.02)            # already past its budget
    deps.meter._loaded.add("case-b")
    run = await research(deps, dict(FR), case_id="case-b")
    assert run.rounds == 1
    assert [e["stop"] for e in run.events if e.get("stop")] == ["budget"]
    lines = [a.text for a in await deps.cases.list("case-b", "activity")]
    assert any("reached its budget for model work" in t for t in lines)


async def test_fixed_research_is_one_pass_by_two_researchers(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, research_mode="fixed"))
    run = await research(deps, dict(FR))
    assert run.rounds == 1 and not [e for e in run.events if e["stage"] == "round"]
    assert run.status == "done" and run.gold and run.gold["match"]


async def test_a_quick_depth_sends_one_researcher_once(deps: Any) -> None:
    run = await research(deps, dict(FR), depth="quick")
    assert run.rounds == 1 and {e["who"] for e in run.events if e["stage"] == "search"} == {"Researcher A"}
    assert not [e for e in run.events if e["stage"] == "researcher_b"]


def _live_like_plan(boundary: str) -> Any:
    """France as Gemini compiled it on 15 October: its own step names, in French, with a Ciclade claim that waits
    on whether the search found a contract (a question the gold plan does not ask)."""
    from afterledger.domain.plan_language import ProcedurePlan
    under, over = ("years_since_death <= 10", "years_since_death > 10") if boundary == "le" else \
        ("years_since_death < 10", "years_since_death >= 10")
    return ProcedurePlan.model_validate({
        "key": "FR/life_insurance_search", "jurisdiction": "FR", "holding_kinds": ["life_policy"], "documents": [],
        "clocks": [],
        "variables": [{"name": "years_since_death", "type": "number", "derive": "years_since_death"},
                      {"name": "ciclade_positive", "type": "tri", "question": "Ciclade a-t-il trouvé un contrat ?"}],
        "steps": [
            {"id": "step_agira_submit", "kind": "submit", "actor": "family", "when": under,
             "text": "Envoyez une demande de recherche gratuite à l'AGIRA avec l'acte de décès."},
            {"id": "step_agira_wait", "kind": "wait", "actor": "institution", "when": under,
             "text": "L'AGIRA informe les assureurs."},
            {"id": "step_ciclade_search", "kind": "follow_up", "actor": "family", "when": over,
             "text": "Faites une recherche gratuite sur le site Ciclade."},
            {"id": "step_ciclade_submit", "kind": "submit", "actor": "family",
             "when": f"{over} and ciclade_positive = yes",
             "text": "Si la recherche Ciclade est positive, transmettez votre demande de restitution."}]})


async def test_a_plan_is_compared_with_gold_by_what_its_steps_do_not_their_names(deps: Any) -> None:
    from afterledger.modules.research import gold_check
    same = await gold_check(deps, _live_like_plan("lt"))
    assert same["match"] and same["aligned"] == {"step_agira_submit": "ask_agira", "step_ciclade_submit": "ask_ciclade"}
    # the source says "less than 10 years" and "more than 10 years": AGIRA at exactly ten is not held against it
    assert (await gold_check(deps, _live_like_plan("le")))["match"]
    no_ciclade = _live_like_plan("lt")
    no_ciclade.steps = [s for s in no_ciclade.steps if "ciclade" not in s.id]
    short = await gold_check(deps, no_ciclade)                               # nothing for an older death
    assert not short["match"] and short["agree"] == short["cases"] - 3
    assert {m["years_since_death"] for m in short["mismatches"]} == {11}


async def test_a_step_taken_in_every_case_is_not_paired_with_one_taken_in_a_few(deps: Any) -> None:
    """A live plan of 15 October went on past the search, to a tax return taken in every case: sharing a kind and an
    actor with the gold plan's Ciclade request is not enough to stand for it."""
    from afterledger.modules.research import gold_check
    broad = _live_like_plan("lt")
    broad.steps = [s for s in broad.steps if s.id != "step_ciclade_submit"]
    tax = broad.steps[0].model_copy(update={"id": "step_tax_return", "when": None,
                                            "text": "Déposez la déclaration de succession au service des impôts."})
    broad.steps.append(tax)
    out = await gold_check(deps, broad)
    assert "step_tax_return" not in out["aligned"] and not out["match"]
