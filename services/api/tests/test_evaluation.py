"""The evaluation: the checks pass on a demo family, baselines are computed, and agents are measured only on cases a
live model prepared (a case prepared with scripted responses measures nothing, and the report says so). The
calibration refit follows the same rule, and changes only bins that hold enough items."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from afterledger.config import get_settings
from afterledger.eval import calibrate, runner
from afterledger.runtime import build_deps
from afterledger.seed.seeder import demo_case_id, seed_family

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def clarke(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, dict[str, str]]:
    """The Clarkes (a pack with forms, a covering letter and an objection), seeded once for this module."""
    s = get_settings().model_copy(update={"mode": "offline", "store": "local", "llm": "scripted", "seed_on_start": False,
                                          "state_dir": tmp_path_factory.mktemp("eval"), "demo_bundles": None})
    deps = build_deps(s)
    rep = await seed_family(deps, "clarke")
    assert not rep.warnings, rep.warnings
    return deps, {"clarke": demo_case_id("clarke")}


async def test_checks_pass(clarke: tuple[Any, dict[str, str]]) -> None:
    deps, cases = clarke
    for check in (await runner.check_routes(deps, cases), runner.check_clocks(),
                  await runner.check_readback(deps, cases), await runner.check_letters(deps, cases)):
        assert check["ok"], check


async def test_a_draft_plan_replaced_by_research_is_not_compared(clarke: tuple[Any, dict[str, str]],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """When the stored plan is an unverified draft, a live run researches the institution's own procedure in its
    place (Verma's family pension on 15 October). The hand-worked route is for the draft's steps, so that claim is
    named and left out of the count; a claim whose verified plan was swapped for another still fails."""
    import copy
    import dataclasses

    from afterledger.knowledge import Knowledge

    deps, cases = clarke
    real_load, real_plan = runner.load_family, Knowledge.plan

    def truth(key: str, day: Any = None) -> Any:
        fam = real_load(key, day)
        data = copy.deepcopy(fam.data)
        for c in data["claims"]:
            if c["id"] == "c_nsi":           # written against a stored plan the case no longer uses
                c["plan"], c["expected_route"] = "GB/premium_bonds", ["a_step_of_that_plan"]
        return dataclasses.replace(fam, data=data)

    def stored_as(level: str, unverified: bool) -> Any:
        async def plan(self: Any, key: str) -> Any:
            if key == "GB/premium_bonds":
                own = await real_plan(self, "GB/premium_bonds@gb_nsandi")
                return own.model_copy(update={"key": key, "level": level, "unverified": unverified})
            return await real_plan(self, key)
        return plan

    monkeypatch.setattr(runner, "load_family", truth)
    monkeypatch.setattr(Knowledge, "plan", stored_as("draft", True))
    check = await runner.check_routes(deps, cases)
    assert check["ok"] and "clarke/c_nsi" in check["result"] and "not compared" in check["result"], check

    monkeypatch.setattr(Knowledge, "plan", stored_as("gold", False))
    check = await runner.check_routes(deps, cases)
    assert not check["ok"] and any(d.startswith("clarke/c_nsi: got") for d in check["detail"]), check


def test_a_value_is_right_as_printed_or_as_its_own_value() -> None:
    """The nine values the live run's evaluation called wrong were all read right: the clean value of a printed line,
    or a name without the honorific printed beside it."""
    from afterledger.eval.runner import right_value

    ref = {"value": "LMP/07714/C", "as_printed": "Our reference: LMP/07714/C"}
    assert right_value("text", "LMP/07714/C", ref, "GB") and right_value("text", "Our reference: LMP/07714/C", ref, "GB")
    assert not right_value("text", "LMP/07714/D", ref, "GB")
    late = {"value": None, "as_printed": "स्व. मोहन लाल वर्मा / LATE MOHAN LAL VERMA"}
    assert right_value("name", "मोहन लाल वर्मा / MOHAN LAL VERMA", late, "IN")
    assert right_value("name", "佐藤 健二", {"value": None, "as_printed": "佐藤 健二 様"}, "JP")
    assert right_value("name", "Margaret Anne Clarke", {"value": "Mrs Margaret Anne Clarke",
                                                         "as_printed": "Mrs Margaret Anne Clarke"}, "GB")
    assert not right_value("name", "Margaret Clarke", {"value": None, "as_printed": "Mrs Margaret Anne Clarke"}, "GB")
    assert not right_value("text", "LATE MOHAN LAL VERMA", {"value": None, "as_printed": "MOHAN LAL VERMA"}, "IN")
    assert right_value("amount", "₹2,00,000", {"value": "200000.00", "as_printed": "Rs. 2,00,000/-"}, "IN")


async def test_baselines_are_measured(clarke: tuple[Any, dict[str, str]]) -> None:
    deps, cases = clarke
    rd = await runner.reading_baseline(deps, cases)
    assert rd["fields"] > 0 and 0 <= rd["right"] <= rd["fields"]
    dc = await runner.discovery_baseline(deps, cases)
    assert dc["hidden"] > 0 and dc["found"] <= dc["hidden"]


async def test_report_says_when_nothing_was_measured(clarke: tuple[Any, dict[str, str]],
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    deps, _ = clarke
    monkeypatch.setattr(runner, "family_keys", lambda: ["clarke"])
    report = await runner.run_all(deps, seed=False)
    assert report["mode"] == "scripted" and report["live_cases"] == []
    assert all(a["agent"] == "After a live run" for a in report["agents"])
    assert "No demo case here was prepared by a live model" in report["note"]
    assert all(c["ok"] for c in report["checks"]), [c for c in report["checks"] if not c["ok"]]


async def test_agents_are_measured_only_on_live_cases(clarke: tuple[Any, dict[str, str]],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    deps, cases = clarke
    assert await runner.live_cases(deps, cases) == {}
    case = await deps.cases.get_case(cases["clarke"])
    case.model_mode = "gemini"            # as if a live model had prepared it
    await deps.cases.put_case(case)
    try:
        assert await runner.live_cases(deps, cases) == cases
        reading = await runner.reading_scores(deps, cases)
        assert reading["fields"] > 0 and reading["right"] + reading["wrong"] + reading["asked"] <= reading["fields"]
        assert reading["typed"] == reading["papers"] > 0
        discovery = await runner.discovery_scores(deps, cases)
        assert discovery["hidden"] > 0 and 0 <= discovery["found"] <= discovery["hidden"]
        monkeypatch.setattr(runner, "family_keys", lambda: ["clarke"])
        report = await runner.run_all(deps, seed=False)
        assert report["live_cases"] == ["clarke"] and report["note"] == ""
        reading_row = next(a for a in report["agents"] if a["id"] == "reading")
        assert "on 1 live case" in reading_row["agent"]
    finally:
        case.model_mode = "scripted"
        await deps.cases.put_case(case)


def test_refit_changes_only_bins_with_enough_items() -> None:
    table = {"base_rate": 0.5, "bins": [{"signals": {"status": "matched"}, "p": 0.9, "n": 0},
                                        {"signals": {"status": "matched", "photo": True}, "p": 0.8, "n": 0}]}
    items = [({"status": "matched", "photo": False}, True)] * (calibrate.MIN_ITEMS - 1)
    items += [({"status": "matched", "photo": True}, i % 2 == 0) for i in range(30)]
    items += [({"status": "disagree"}, False)] * calibrate.MIN_ITEMS
    new, rows = calibrate.refit(table, items)
    assert [r["items"] for r in rows] == [calibrate.MIN_ITEMS - 1, 30, calibrate.MIN_ITEMS]
    assert not rows[0]["fitted"] and new["bins"][0]["p"] == 0.9            # too few items: kept
    assert rows[1]["fitted"] and new["bins"][1]["p"] == round(16 / 32, 3)  # the most specific bin, Laplace
    assert rows[2]["signals"] == {} and new["base_rate"] == round(1 / 22, 3)
    assert table["bins"][1]["p"] == 0.8 and table["base_rate"] == 0.5        # the input is not changed


async def test_calibrate_measures_only_live_cases(clarke: tuple[Any, dict[str, str]], tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    deps, cases = clarke
    monkeypatch.setattr(calibrate, "family_keys", lambda: ["clarke"])
    path = tmp_path / "calibration.json"
    tables = json.loads((deps.settings.knowledge_dir / "calibration.json").read_text(encoding="utf-8"))
    tables["_status"] = "default"                       # the tables as set by hand, before any refit
    tables.pop("_fitted", None)
    path.write_text(json.dumps(tables, ensure_ascii=False), encoding="utf-8")
    report = await calibrate.calibrate(deps, write=True, path=path)
    assert report["live_cases"] == [] and "No demo case" in report["note"]
    assert json.loads(path.read_text(encoding="utf-8"))["_status"] == "default"
    case = await deps.cases.get_case(cases["clarke"])
    case.model_mode = "gemini"
    await deps.cases.put_case(case)
    try:
        report = await calibrate.calibrate(deps, write=True, path=path)
        assert report["live_cases"] == ["clarke"]
        for module in ("reading", "discovery"):
            m = report["modules"][module]
            assert m["items"] > 0 and sum(r["items"] for r in m["bins"]) == m["items"], module
            assert all(r["p"] == r["start"] for r in m["bins"] if not r["fitted"]), module
        fitted = [r for m in report["modules"].values() for r in m["bins"] if r["fitted"]]
        saved = json.loads(path.read_text(encoding="utf-8"))
        if fitted:
            assert report["written"] == str(path) and saved["_status"] == "fitted"
            assert saved["_fitted"]["live_cases"] == ["clarke"]
        else:
            assert "written" not in report and saved["_status"] == "default"
    finally:
        case.model_mode = "scripted"
        await deps.cases.put_case(case)
