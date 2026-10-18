"""The six demo families, played from their first conversation to their story day with scripted responses: every
step plays, each family's day ends where its file says it does (the tasks for today, the money known, paid, in
progress and owed, the leads still open), every word the families read has a reviewed translation, and every check
of the evaluation passes on the six cases."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from afterledger.runtime import build_deps
from afterledger.seed.families import family_keys, load_family
from afterledger.seed.seeder import demo_case_id
from conftest import DemoState, offline_settings

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("key", family_keys())
def test_family_reaches_its_story_day(demo_state: DemoState, key: str) -> None:
    from afterledger.modules import followup

    report = demo_state.reports[key]
    assert not report.warnings, report.warnings
    assert report.events > 0
    expected = load_family(key).get("expected_today") or {}

    async def today() -> tuple[list, dict]:
        deps = build_deps(offline_settings(demo_state.root))
        return (await deps.cases.list(demo_case_id(key), "tasks"),
                await followup.totals(deps, demo_case_id(key)))

    tasks, totals = asyncio.run(today())
    assert len(tasks) == len(expected.get("tasks", [])), [t.title for t in tasks]
    want = expected.get("totals") or {}
    by_currency = totals["currencies"]
    if "known" in want:                                     # one currency
        got = next(iter(by_currency.values()))
        for field in ("known", "paid", "in_progress", "owed"):
            if field in want:
                assert Decimal(str(got.get(field, 0))) == Decimal(str(want[field])), field
        if "unknown_leads" in want:
            assert totals["unknown_leads"] == want["unknown_leads"]
    else:                                                   # several currencies
        for code, fields in want.items():
            for field, value in fields.items():
                assert Decimal(str(by_currency[code][field])) == Decimal(str(value)), (code, field)


def test_every_word_the_families_read_has_a_reviewed_translation(demo_state: DemoState) -> None:
    """Offline, the families' languages come from the reviewed table; a gap would show English to a family."""
    assert demo_state.misses == [], demo_state.misses[:10]


def test_the_evaluation_passes_on_the_six_cases(demo_state: DemoState) -> None:
    from afterledger.eval.runner import run_all

    async def go() -> dict:
        return await run_all(build_deps(offline_settings(demo_state.root)), seed=False)

    report = asyncio.run(go())
    failed = [c for c in report["checks"] if not c["ok"]]
    assert not failed, failed
    assert report["live_cases"] == [] and all(a["agent"] == "After a live run" for a in report["agents"])
    assert {c["country"] for c in report["coverage"] if c["papers"] == "verified"} == {"IN", "JP", "GB", "US",
                                                                                      "SG", "DE"}


def test_every_model_call_is_traced_once(demo_state: DemoState) -> None:
    """One model_call trace line for every model call, wherever it ran: a single agent, or a workflow's parallel
    workers (discovery's interpreters and investigators). Counted against the oracles' own count."""
    import json
    lines: list[dict] = []
    for f in sorted((demo_state.root / "traces").glob("*.jsonl")):
        lines += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    calls = [x for x in lines if x.get("type") == "model_call"]
    assert demo_state.answered > 0 and len(calls) == demo_state.answered
    agents = {c["agent"] for c in calls}
    assert {"narration_interpreter", "hypothesis_generator", "investigator", "devils_advocate"} <= agents
    assert all(c["status"] == "ok" for c in calls)
    assert sum(1 for c in calls if c["case_id"]) / len(calls) > 0.95      # nearly every call is tied to its case
