"""The public site's settings, each model tier on its fastest endpoint, and the work a long, real statement brings:
a year of payees capped to the ones worth reading, and a paper too long for one request read in parts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from afterledger.domain.case import PaymentGroup
from afterledger.modules import discovery, reading
from conftest import api_client, offline_settings


def test_each_tier_goes_first_to_its_fastest_endpoint_and_keeps_the_others(tmp_path: Path,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    s = offline_settings(tmp_path, llm_tier_locations="lite=asia-south1,flash=us")
    assert s.tier_location("gemini-3.5-flash-lite") == "asia-south1"
    assert s.tier_location("gemini-3.8-flash") == "us"
    assert s.tier_location("gemini-3.1-pro-preview") is None
    flash = s.route_groups("flash")
    assert flash[0][0] == "gemini-3.8-flash@us" and "gemini-3.8-flash" in flash[0]
    assert flash[1][0] == "gemini-3.5-flash-lite@asia-south1" and "gemini-3.5-flash-lite" in flash[1]
    assert s.routes("lite")[:2] == ["gemini-3.5-flash-lite@asia-south1", "gemini-3.5-flash-lite"]
    # without the setting nothing changes
    assert offline_settings(tmp_path).route_groups("flash")[0][0] == "gemini-3.8-flash"


def test_the_conversation_agents_answer_on_flash_lite_and_a_setting_can_move_any_agent(tmp_path: Path) -> None:
    """Every agent that runs while the family waits for a reply is on Flash-Lite; AL_AGENT_TIERS moves an agent to
    another tier without a new build, and anything it does not name, or names wrongly, stays as it was."""
    from afterledger.agents.specs import AGENTS
    assert {a: AGENTS[a].tier for a in ("listener", "situation_extractor", "gap_planner", "tone_critic")} == \
        dict.fromkeys(("listener", "situation_extractor", "gap_planner", "tone_critic"), "lite")
    s = offline_settings(tmp_path, agent_tiers="situation_extractor=flash, gap_planner = pro,tone_critic=huge,nobody")
    assert s.agent_tier("situation_extractor", "lite") == "flash"
    assert s.agent_tier("gap_planner", "lite") == "pro"
    assert s.agent_tier("tone_critic", "lite") == "lite"
    assert s.agent_tier("listener", "lite") == "lite"
    assert offline_settings(tmp_path).agent_tier("gap_planner", "lite") == "lite"
    from afterledger.runtime import build_deps  # and it is the model the agent is built with
    deps = build_deps(offline_settings(tmp_path, llm="replay", llm_replay_dir=str(tmp_path), agent_tiers="gap_planner=pro"))
    assert deps.models.model_for(AGENTS["gap_planner"]).model == s.model_pro
    assert deps.models.model_for(AGENTS["situation_extractor"]).model == s.model_lite
    assert deps.models.model_for(AGENTS["reader_a"]).model == s.model_flash


def test_the_public_site_shows_only_the_chosen_families_in_their_order(tmp_path: Path) -> None:
    s = offline_settings(tmp_path, site="product", demo_families="verma, sato,unknown")
    assert s.product
    assert s.shown_families(["clarke", "nair", "sato", "verma"]) == ["verma", "sato"]
    everything = offline_settings(tmp_path)
    assert not everything.product
    assert everything.shown_families(["clarke", "verma"]) == ["clarke", "verma"]


def test_the_public_site_has_no_evaluation_runs_or_replayed_stories(tmp_path: Path) -> None:
    gen = api_client(tmp_path, site="product", demo_families="verma,nair")
    c = next(gen)
    try:
        assert c.post("/v1/eval/run").status_code == 404
        assert c.get("/v1/demo/verma/watch").status_code == 404
        keys = [f["key"] for f in c.get("/v1/demo/families").json()]
        assert keys == ["verma", "nair"]
    finally:
        gen.close()


def _group(i: int, label: str, count: int = 1, cadence: str = "once", after_death: int = 0, amount: str = "100") -> PaymentGroup:
    return PaymentGroup(id=f"g{i}", case_id="c", key=f"k{i}", label=label, direction="debit", count=count, cadence=cadence,
                        amount_typical=Decimal(amount), amount_stable=True, first=date(2026, 1, 1), last=date(2026, 6, 1),
                        after_death_count=after_death)


def test_a_year_of_payees_is_cut_to_the_ones_worth_an_agents_reading() -> None:
    few = [_group(i, f"SHOP {i}") for i in range(10)]
    assert discovery.worth_reading(few) == few
    many = [_group(i, f"UPI/SHOP {i}", amount=str(10 + i)) for i in range(200)]
    many[150] = _group(150, "PMJJBY PREM RENEWAL", count=1, amount="436")
    many[170] = _group(170, "ACH D- NACH MF SIP CAMS", count=12, cadence="monthly", amount="5000")
    many[190] = _group(190, "UPI/UNKNOWN", count=1, after_death=1, amount="20")
    kept = discovery.worth_reading(many)
    assert len(kept) == discovery.MAX_GROUPS
    ids = [g.id for g in kept]
    assert {"g150", "g170", "g190"} <= set(ids)
    assert ids == sorted(ids, key=lambda x: int(x[1:]))          # the original order is kept


async def test_a_long_paper_is_read_in_parts_and_its_rows_numbered_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 20-page statement: every part goes to the text reader, only the first part to the image reader; rows from
    later parts follow on from the earlier ones; the closing balance comes from the last part, the rest from the top."""
    from types import SimpleNamespace

    from afterledger.agents.schemas import FieldReading, ReaderOut, RowReading

    calls: list[tuple[str, int, int]] = []

    async def fake_call_agent(deps: Any, name: str, pack: dict, case_id: str | None = None, images: Any = None) -> Any:
        first, last = pack["part"]["pages"]
        calls.append((name, first, last))
        assert all(first <= ln["page"] <= last for ln in pack.get("lines", []))
        if name == "reader_b":
            return ReaderOut(fields=[FieldReading(field="account_holder", value="A HOLDER")])
        rows = [RowReading(row=k + 1, date="01-01-26", narration=f"PAGE {p}", amount="1.00", direction="debit",
                           balance=str(p), page=p) for k, p in enumerate(range(first, last + 1))]
        top = [FieldReading(field="account_holder", value="A HOLDER")] if first == 1 else []
        return ReaderOut(fields=[*top, FieldReading(field="closing_balance", value=str(last))], rows=rows)

    async def no_stage(*a: Any, **k: Any) -> None:
        return None

    notes: list[str] = []

    async def activity(case_id: str, module: str, text: str, **k: Any) -> None:
        notes.append(text)

    monkeypatch.setattr(reading, "call_agent", fake_call_agent)
    monkeypatch.setattr(reading, "set_stage", no_stage)
    pages = [SimpleNamespace(id=f"p{n}", number=n, width=1, height=1, image=None) for n in range(1, 21)]
    lines = [SimpleNamespace(id=f"l{n}", text=f"line {n}", page_id=f"p{n}", box=(0, 0, 1, 1)) for n in range(1, 21)]
    paper = SimpleNamespace(id="pp", title="A year of statements", pages=list(reversed(pages)))
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=activity)
    case = SimpleNamespace(id="c1", demo_key=None)
    a, b = await reading._read_in_parts(deps, case, paper, lines, {}, {"fields": []}, None)

    assert sorted(c for c in calls if c[0] == "reader_a") == [("reader_a", 1, 6), ("reader_a", 7, 12),
                                                                ("reader_a", 13, 18), ("reader_a", 19, 20)]
    assert [c for c in calls if c[0] == "reader_b"] == [("reader_b", 1, 6)]
    assert [r.row for r in a.rows] == list(range(1, 21))
    assert [r.page for r in a.rows] == list(range(1, 21))
    found = {f.field: f.value for f in a.fields}
    assert found == {"account_holder": "A HOLDER", "closing_balance": "20"}
    assert [f.field for f in b.fields] == ["account_holder"] and b.rows == []
    assert any("20 pages" in n and "4 parts" in n for n in notes)


def test_visitors_see_only_the_samples_and_their_own_cases(tmp_path: Path) -> None:
    gen = api_client(tmp_path, site="product", auth="off")           # a public site without accounts
    c = next(gen)
    try:
        mine = c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
        theirs = c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
        assert [x["id"] for x in c.get("/v1/cases").json() if not x["demo_key"]] == []
        listed = [x["id"] for x in c.get("/v1/cases", params={"ids": f"{mine},nope"}).json() if not x["demo_key"]]
        assert listed == [mine] and theirs not in listed
        assert c.get(f"/v1/cases/{theirs}").status_code == 200        # its address still opens it
        assert c.get("/v1/challenges").json() == []
    finally:
        gen.close()
    gen = api_client(tmp_path / "dev")                                  # a development copy lists everything
    c = next(gen)
    try:
        one = c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
        assert one in [x["id"] for x in c.get("/v1/cases").json()]
    finally:
        gen.close()


def test_the_operator_can_clear_the_site_before_a_demonstration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resets: list[str] = []

    async def fake_reset(deps: Any, key: str) -> dict:
        resets.append(key)
        return {"state": "ready", "restored": True}

    monkeypatch.setattr("afterledger.app.demo.reset", fake_reset)
    gen = api_client(tmp_path, site="product", demo_families="verma,sato", internal_token="tok-123", auth="off")
    c = next(gen)
    try:
        ids = [c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"] for _ in range(2)]
        c.portal.call(c.deps.knowledge.put, "challenges", "ch_1", {"id": "ch_1", "need": {"country": "FR", "question": "q"}})
        assert c.post("/internal/clean").status_code == 403
        assert c.post("/internal/clean", headers={"X-AfterLedger-Token": "wrong"}).status_code == 403
        out = c.post("/internal/clean", headers={"X-AfterLedger-Token": "tok-123"}).json()
        assert out == {"cases_removed": 2, "questions_removed": 1, "samples_reset": {}}
        assert resets == []                                    # the samples stay as a live run left them
        out = c.post("/internal/clean", params={"samples": "reset"}, headers={"X-AfterLedger-Token": "tok-123"}).json()
        assert out == {"cases_removed": 0, "questions_removed": 0, "samples_reset": {"verma": "ready", "sato": "ready"}}
        assert resets == ["verma", "sato"]
        for i in ids:
            assert c.get(f"/v1/cases/{i}").status_code == 404
        assert c.portal.call(c.deps.knowledge.get, "challenges", "ch_1") is None
    finally:
        gen.close()
