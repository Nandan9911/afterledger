"""The public site's settings, each model tier on its fastest endpoint, and the work a long, real statement brings:
a year of payees capped to the ones worth reading, and a paper too long for one request read in parts."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from afterledger.concurrency import work_now
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
    a, b, gaps = await reading._read_in_parts(deps, case, paper, lines, {}, {"fields": []}, None)

    assert gaps == []
    assert sorted(c for c in calls if c[0] == "reader_a") == [("reader_a", 1, 6), ("reader_a", 7, 12),
                                                                ("reader_a", 13, 18), ("reader_a", 19, 20)]
    assert [c for c in calls if c[0] == "reader_b"] == [("reader_b", 1, 6)]
    assert [r.row for r in a.rows] == list(range(1, 21))
    assert [r.page for r in a.rows] == list(range(1, 21))
    found = {f.field: f.value for f in a.fields}
    assert found == {"account_holder": "A HOLDER", "closing_balance": "20"}
    assert [f.field for f in b.fields] == ["account_holder"] and b.rows == []
    assert any("20 pages" in n and "4 parts" in n for n in notes)


def test_a_dense_statement_is_read_a_page_at_a_time_and_a_sparse_one_six_pages_at_a_time() -> None:
    """Live on 19 October 2026 a real bank statement (about 200 text lines a page) was read six pages to a request:
    70,000 tokens in, every request ran past its deadline, and the statement could not be read."""
    from types import SimpleNamespace
    pages = [SimpleNamespace(id=f"p{n}") for n in range(1, 9)]
    assert [len(x) for x in reading.split_pages(pages, {p.id: 20 for p in pages})] == [6, 2]
    assert [len(x) for x in reading.split_pages(pages, {p.id: 210 for p in pages})] == [1] * 8
    mixed = {"p1": 100, "p2": 30, "p3": 20, "p4": 200, "p5": 10}
    assert [[p.id for p in x] for x in reading.split_pages(pages[:5], mixed)] == [["p1", "p2"], ["p3"], ["p4"], ["p5"]]
    assert reading.LONG_PAPER_LINES > 556            # the longest demo paper is still read in one request


async def test_a_part_goes_under_short_line_ids_and_comes_back_with_the_real_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from afterledger.agents.schemas import FieldReading, ReaderOut, RowReading

    packs: list[dict] = []

    async def fake_call_agent(deps: Any, name: str, pack: dict, case_id: str | None = None, images: Any = None) -> Any:
        if name == "reader_b":
            return ReaderOut(fields=[FieldReading(field="account_holder", value="A HOLDER")])
        packs.append(pack)
        ids = [ln["id"] for ln in pack["lines"]]
        page = pack["part"]["pages"][0]
        return ReaderOut(fields=[FieldReading(field="closing_balance", value=str(page), line_ids=[ids[-1]])],
                         rows=[RowReading(row=1, date="01-01-26", narration=f"PAGE {page}", amount="1.00",
                                          direction="debit", page=page, line_ids=[ids[0], ids[-1]])])

    async def no_stage(*a: Any, **k: Any) -> None:
        return None

    notes: list[str] = []

    async def activity(case_id: str, module: str, text: str, **k: Any) -> None:
        notes.append(text)

    monkeypatch.setattr(reading, "call_agent", fake_call_agent)
    monkeypatch.setattr(reading, "set_stage", no_stage)
    pages = [SimpleNamespace(id=f"p{n}", number=n, width=1, height=1, image=None) for n in range(1, 4)]
    lines = [SimpleNamespace(id=f"real-{n}-{k}", text="x", page_id=f"p{n}", box=(0.12345, 0.5, 0.9, 0.51))
             for n in range(1, 4) for k in range(200)]
    paper = SimpleNamespace(id="pp", title="A dense statement", pages=pages)
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=activity)
    a, b, gaps = await reading._read_in_parts(deps, SimpleNamespace(id="c1", demo_key=None), paper, lines, {},
                                              {"fields": []}, None)
    assert gaps == [] and [f.field for f in b.fields] == ["account_holder"]   # page 1, also read from its image
    assert len(packs) == 3 and all(len(p["lines"]) == 200 for p in packs)
    assert packs[0]["lines"][0] == {"id": "L1", "text": "x", "page": 1, "box": [0.123, 0.5, 0.9, 0.51]}
    assert [r.line_ids for r in a.rows] == [[f"real-{n}-0", f"real-{n}-199"] for n in range(1, 4)]
    assert [r.row for r in a.rows] == [1, 2, 3]
    assert {f.field: f.line_ids for f in a.fields} == {"closing_balance": ["real-3-199"]}
    assert any("3 parts" in n and "the first page also from its image" in n for n in notes)


def _paper_of(n_pages: int, lines_per_page: int) -> tuple[Any, list]:
    from types import SimpleNamespace
    pages = [SimpleNamespace(id=f"p{n}", number=n, width=1, height=1, image=None) for n in range(1, n_pages + 1)]
    lines = [SimpleNamespace(id=f"l{n}-{k}", text="x", page_id=f"p{n}", box=(0, 0, 1, 1))
             for n in range(1, n_pages + 1) for k in range(lines_per_page)]
    return SimpleNamespace(id="pp", title="A statement", pages=pages), lines


def _reader_failing_on(bad: set[int], calls: list | None = None) -> Any:
    """A reader A that reads one row a page, and fails on any part that holds a page in `bad`."""
    from afterledger.agents.schemas import ReaderOut, RowReading

    async def fake_call_agent(deps: Any, name: str, pack: dict, case_id: str | None = None, images: Any = None) -> Any:
        if name == "reader_b":
            return ReaderOut()
        first, last = pack["part"]["pages"]
        if calls is not None:
            calls.append((first, last))
        if bad & set(range(first, last + 1)):
            raise TimeoutError(f"pages {first}-{last}: no answer")
        return ReaderOut(rows=[RowReading(row=k + 1, date="01-01-26", narration=f"PAGE {p}", amount="1.00",
                                          direction="debit", page=p) for k, p in enumerate(range(first, last + 1))])
    return fake_call_agent


async def test_a_part_that_fails_is_read_again_in_halves_and_only_an_unreadable_page_is_left_out(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Live on 19 October 2026 one part that could not be read failed a 90-page statement whole."""
    from types import SimpleNamespace

    async def no_stage(*a: Any, **k: Any) -> None:
        return None

    notes: list[str] = []

    async def activity(case_id: str, module: str, text: str, **k: Any) -> None:
        notes.append(text)

    calls: list[tuple[int, int]] = []
    monkeypatch.setattr(reading, "call_agent", _reader_failing_on({9}, calls))
    monkeypatch.setattr(reading, "set_stage", no_stage)
    paper, lines = _paper_of(12, 20)                           # two parts of six pages
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=activity)
    a, _b, gaps = await reading._read_in_parts(deps, SimpleNamespace(id="c1", demo_key=None), paper, lines, {},
                                               {"fields": []}, None)
    assert gaps == [9]
    assert [r.narration for r in a.rows] == [f"PAGE {p}" for p in (1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 12)]
    assert [r.row for r in a.rows] == list(range(1, 12))
    assert sorted(calls) == [(1, 6), (7, 7), (7, 9), (7, 12), (8, 8), (8, 9), (9, 9), (10, 12)]
    assert any(n.startswith("Page 9 of") and "could not be read" in n for n in notes)


async def test_a_paper_mostly_unreadable_is_not_called_read(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    async def no_stage(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(reading, "call_agent", _reader_failing_on(set(range(1, 8))))
    monkeypatch.setattr(reading, "set_stage", no_stage)
    paper, lines = _paper_of(12, 20)
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=None)
    with pytest.raises(reading.ReadingFailed, match="7 of 12 pages"):
        await reading._read_in_parts(deps, SimpleNamespace(id="c1", demo_key=None), paper, lines, {},
                                     {"fields": []}, None)


async def test_a_reading_stopped_from_outside_stops_every_part_and_a_failed_image_reading_stops_none(
        monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from afterledger.agents.schemas import ReaderOut, RowReading

    finished: list[int] = []

    async def slow_reader(deps: Any, name: str, pack: dict, case_id: str | None = None, images: Any = None) -> Any:
        if name == "reader_b":
            raise RuntimeError("the page images could not be read")
        await asyncio.sleep(0.2)
        finished.append(pack["part"]["pages"][0])
        return ReaderOut(rows=[RowReading(row=1, date="01-01-26", narration="x", amount="1.00", direction="debit")])

    async def no_stage(*a: Any, **k: Any) -> None:
        return None

    async def activity(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(reading, "call_agent", slow_reader)
    monkeypatch.setattr(reading, "set_stage", no_stage)
    paper, lines = _paper_of(6, 200)                           # six dense pages: six parts
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=activity)
    case = SimpleNamespace(id="c1", demo_key=None)
    task = asyncio.ensure_future(reading._read_in_parts(deps, case, paper, lines, {}, {"fields": []}, None))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert finished == []                                      # no part read on after the stop
    a, b, gaps = await reading._read_in_parts(deps, case, paper, lines, {}, {"fields": []}, None)
    assert len(a.rows) == 6 and b.fields == [] and gaps == []  # the text reading stands alone


def test_the_statement_reader_gets_the_time_its_long_answers_take(tmp_path: Path) -> None:
    """Live, the statement reader's requests had 90 s and Gemini cut them off (504 DEADLINE_EXCEEDED)."""
    from afterledger.agents.runner import SLOW_READERS
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    assert "reader_a" in SLOW_READERS                       # the agent's own limit is doubled too
    deps = build_deps(offline_settings(tmp_path, llm="gemini", project="test-project", llm_hedge_after_s=20,
                                       llm_hedge_interactive_s=0, llm_http_timeout_s=90))
    for name in ("reader_a", "reader_b"):                  # the longest answers: five minutes a request
        reader = deps.models.model_for(AGENTS[name])
        assert reader.http_timeout_s == 300 and reader.inner.hedge_after_s == 120
    assert deps.models.model_for(AGENTS["reader_c"]).inner.hedge_after_s == 40


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


# ---- the server under load: limits that keep it answering everything else -------------------------------------

async def test_reading_requests_are_limited_across_the_whole_server(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from afterledger.agents.schemas import ReaderOut

    now = {"in": 0, "most": 0}

    async def counting_reader(deps: Any, name: str, pack: dict, case_id: str | None = None, images: Any = None) -> Any:
        now["in"] += 1
        now["most"] = max(now["most"], now["in"])
        await asyncio.sleep(0.02)
        now["in"] -= 1
        return ReaderOut()

    async def nothing(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(reading, "call_agent", counting_reader)
    monkeypatch.setattr(reading, "set_stage", nothing)
    monkeypatch.setattr(reading, "READ_CALLS_AT_ONCE", 3)
    deps = SimpleNamespace(models=SimpleNamespace(mode="scripted"), activity=nothing, extras={})
    case = SimpleNamespace(id="c1", demo_key=None)
    two_papers = [_paper_of(8, 200), _paper_of(8, 200)]       # sixteen parts and two image readings at once
    await asyncio.gather(*(reading._read_in_parts(deps, case, paper, lines, {}, {"fields": []}, None)
                           for paper, lines in two_papers))
    assert now["most"] == 3


async def test_questions_wait_their_turn_and_leave_memory_once_stored(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from afterledger.modules import challenge
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    started: list[str] = []

    async def research(deps: Any, run: Any) -> None:
        started.append(run.id)
        await asyncio.sleep(0.1)
        run.status = "done"

    monkeypatch.setattr(challenge, "_challenge", research)
    monkeypatch.setattr(challenge, "RESEARCH_AT_ONCE", 1)
    monkeypatch.setattr(challenge, "KEEP_FINISHED_S", 0.05)
    first = challenge.launch(deps, country="FR", question="Une assurance-vie ?")
    second = challenge.launch(deps, country="DE", question="Ein Sparbuch?")
    await asyncio.sleep(0.03)
    assert started == [first.id] and [e["stage"] for e in second.events] == ["waiting"]
    await asyncio.sleep(0.5)
    assert started == [first.id, second.id]
    assert challenge.running_run(deps, first.id) is None                  # out of memory once stored...
    assert (await challenge.get_challenge(deps, first.id))["status"] == "done"   # ...and still there to open


async def test_the_live_web_keeps_a_bounded_number_of_pages_in_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from afterledger.adapters.gcp import web
    from afterledger.ports import PageSnapshot

    class NoSnapshots:
        async def fetch(self, url: str) -> None:
            return None

    pages = web.LivePages(snapshots=NoSnapshots())

    async def fetched(url: str) -> tuple[PageSnapshot, bool]:
        return PageSnapshot(url=url, title="t", text="x" * 4_000), False

    monkeypatch.setattr(pages, "_get", fetched)
    monkeypatch.setattr(web, "_CACHE_PAGES", 3)
    monkeypatch.setattr(web, "_CACHE_CHARS", 10_000)
    for n in range(6):
        await pages.fetch_live(f"https://www.example.gov/{n}")
    assert list(pages._cache) == ["https://www.example.gov/4", "https://www.example.gov/5"]
    monkeypatch.setattr(web, "_CACHE_CHARS", 100_000)
    for n in range(6):
        await pages.fetch_live(f"https://www.example.gov/{n}")
    assert len(pages._cache) == 3


def test_an_upload_too_big_for_the_server_is_refused_with_what_to_do(tmp_path: Path) -> None:
    import pymupdf
    doc = pymupdf.open()
    for _ in range(201):
        doc.new_page()
    long_pdf = doc.tobytes()
    gen = api_client(tmp_path)
    c = next(gen)
    try:
        case_id = c.post("/v1/cases", json={"lang": "en"}).json()["case"]["id"]
        r = c.post(f"/v1/cases/{case_id}/papers", files=[("files", ("year.pdf", long_pdf, "application/pdf"))])
        assert r.status_code == 413 and "201 pages" in r.json()["detail"] and "in parts" in r.json()["detail"]
        r = c.post(f"/v1/cases/{case_id}/papers", files=[("files", ("broken.pdf", b"not a pdf", "application/pdf"))])
        assert r.status_code == 415
        many = [("files", (f"p{n}.png", b"x", "image/png")) for n in range(21)]
        assert c.post(f"/v1/cases/{case_id}/papers", files=many).status_code == 413
    finally:
        gen.close()


async def test_reading_leaves_the_case_free_for_the_family_and_the_rest_holds_it(tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Live, every reply, answer and upload on a case waited while its statement was being read (minutes)."""
    from afterledger.app import actions
    from afterledger.modules import pipeline
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    actions.register_handlers(deps)
    held: dict[str, bool] = {}

    async def read_papers(deps_: Any, case_id: str, paper_ids: list[str]) -> None:
        held["reading"] = actions.case_lock(deps, case_id).locked()

    async def after_reading(deps_: Any, case_id: str) -> None:
        held["after"] = actions.case_lock(deps, case_id).locked()

    async def nothing(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(pipeline, "read_papers", read_papers)
    monkeypatch.setattr(pipeline, "after_reading", after_reading)
    monkeypatch.setattr(actions.followup, "plan_today", nothing)
    await deps.tasks._handlers["after_papers"]({"case_id": "c1", "paper_ids": ["p1"]})
    assert held == {"reading": False, "after": True}


async def test_a_paper_is_read_once_even_when_its_job_comes_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from afterledger.modules import pipeline

    papers = {"p1": SimpleNamespace(status="queued"), "p2": SimpleNamespace(status="read")}
    reads: list[str] = []

    async def slow_read(deps: Any, case_id: str, paper_id: str) -> None:
        reads.append(paper_id)
        await asyncio.sleep(0.05)
        papers[paper_id].status = "read"

    async def get(case_id: str, collection: str, paper_id: str) -> Any:
        return papers.get(paper_id)

    monkeypatch.setattr(pipeline, "_read", slow_read)
    deps = SimpleNamespace(extras={}, cases=SimpleNamespace(get=get))
    await asyncio.gather(pipeline.read_papers(deps, "c1", ["p1", "p2"]), pipeline.read_papers(deps, "c1", ["p1"]))
    assert reads == ["p1"] and deps.extras["papers_being_read"] == {}


def test_each_model_and_endpoint_keeps_one_client(tmp_path: Path) -> None:
    """Every call used to make its own Gemini client: a new connection, and a new sign-in, each time."""
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    models = build_deps(offline_settings(tmp_path, llm="gemini", project="test-project")).models
    us = models._live("gemini-3.8-flash", AGENTS["reader_a"], "us")
    assert models._live("gemini-3.8-flash", AGENTS["investigator"], "us") is us
    assert models._live("gemini-3.8-flash", AGENTS["reader_a"], None) is not us
    one, two = models.model_for(AGENTS["listener"]), models.model_for(AGENTS["listener"])
    assert one is not two and one.inner.chain[0][1] is two.inner.chain[0][1]


# ---- removing a paper, and the case saying what it is working on ----------------------------------------------

async def test_removing_a_paper_takes_what_was_found_only_in_it_and_keeps_the_rest(tmp_path: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date as d
    from decimal import Decimal as D

    from afterledger.app import actions
    from afterledger.domain import case as m
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    store = deps.cases
    await store.put_case(m.CaseFile(id="c1", title="A family"))
    ev = lambda pid: [m.Evidence(kind="paper", ref=pid, paper_id=pid)]          # noqa: E731
    for pid in ("pp_gone", "pp_kept"):
        await store.put("c1", "papers", m.Paper(id=pid, case_id="c1", title=pid, status="read", synthetic=False))
        await store.put_many("c1", "lines", [m.OcrLine(id=f"{pid}-l{n}", case_id="c1", paper_id=pid, page_id=f"{pid}-p1",
                                                       text="x", box=[0, 0, 1, 1]) for n in range(700)])
        await store.put("c1", "values", m.ReadValue(id=f"rv_{pid}", case_id="c1", paper_id=pid, field="holder", label="Holder"))
        await store.put("c1", "transactions", m.Transaction(id=f"tx_{pid}", case_id="c1", paper_id=pid, date=d(2026, 1, 1),
                                                            narration="NACH LOAN", amount=D("10"), direction="debit"))
        await store.put("c1", "groups", m.PaymentGroup(id=f"g_{pid}", case_id="c1", key=pid, label="NACH LOAN",
                                                       direction="debit", count=1, cadence="once", amount_typical=D("10"),
                                                       amount_stable=True, first=d(2026, 1, 1), last=d(2026, 1, 1),
                                                       transaction_ids=[f"tx_{pid}"]))
        await store.put("c1", "facts", m.Fact(id=f"f_{pid}", case_id="c1", subject="holding:h", predicate="balance",
                                              value="1", evidence=ev(pid)))
    leads = [m.Lead(id="ld_only", case_id="c1", kind="debt", category="loan", title="A loan", sentence="s",
                    paper_ids=["pp_gone"], holding_id="h_only"),
             m.Lead(id="ld_marked", case_id="c1", kind="asset", category="bank", title="An account", sentence="s",
                    paper_ids=["pp_gone"], status="confirmed"),
             m.Lead(id="ld_both", case_id="c1", kind="asset", category="bank", title="A fund", sentence="s",
                    paper_ids=["pp_gone", "pp_kept"])]
    await store.put_many("c1", "leads", leads)
    await store.put_many("c1", "holdings", [
        m.Holding(id="h_only", case_id="c1", kind="loan", label="A loan", source="lead", lead_id="ld_only"),
        m.Holding(id="h_claimed", case_id="c1", kind="savings", label="Claimed", source="paper", evidence=ev("pp_gone")),
        m.Holding(id="h_paper", case_id="c1", kind="savings", label="Read", source="paper", evidence=ev("pp_gone"))])
    await store.put("c1", "claims", m.Claim(id="cl1", case_id="c1", institution_id="in_x", title="t", holding_ids=["h_claimed"]))
    await store.put_many("c1", "checkpoints", [
        m.Checkpoint(id="cp_value", case_id="c1", code="C2", kind="value", title="t", question="q",
                     subject_ref="read_value:rv_pp_gone"),
        m.Checkpoint(id="cp_lead", case_id="c1", code="C3", kind="lead", title="t", question="q", subject_ref="lead:ld_only"),
        m.Checkpoint(id="cp_other", case_id="c1", code="C3", kind="lead", title="t", question="q", subject_ref="lead:ld_both")])
    dropped: list[str] = []

    async def delete_prefix(prefix: str) -> int:
        dropped.append(prefix)
        return 1

    monkeypatch.setattr(deps.blobs, "delete_prefix", delete_prefix, raising=False)
    queued: list[dict] = []

    async def enqueue(kind: str, payload: dict, **k: Any) -> str:
        queued.append({"kind": kind, **payload})
        return "t1"

    monkeypatch.setattr(deps.tasks, "enqueue", enqueue)
    out = await actions.delete_paper(deps, "c1", "pp_gone")
    assert out == {"deleted": "pp_gone", "rows": 1, "values": 1, "leads": 1, "holdings": 2}

    async def ids(collection: str) -> set[str]:
        return {x.id for x in await store.list("c1", collection)}

    assert await ids("papers") == {"pp_kept"}
    assert await ids("leads") == {"ld_marked", "ld_both"}                 # the family acted on one; another paper has one
    assert await ids("holdings") == {"h_claimed"}                         # a claim was started on it
    assert await ids("checkpoints") == {"cp_other"}
    assert await ids("transactions") == {"tx_pp_kept"} and await ids("groups") == {"g_pp_kept"}
    assert await ids("values") == {"rv_pp_kept"} and await ids("facts") == {"f_pp_kept"}
    assert len(await ids("lines")) == 700                                 # the other paper's text lines, untouched
    assert dropped == ["cases/c1/papers/pp_gone/"]
    assert [{k: v for k, v in q.items() if k != "work"} for q in queued] == [
        {"kind": "after_papers", "case_id": "c1", "paper_ids": []}]
    # the header says the case is being worked through again from the moment the paper is gone
    assert work_now(deps, "c1") == {"step": "finding", "since": work_now(deps, "c1")["since"]}
    with pytest.raises(actions.NotFound):
        await actions.delete_paper(deps, "c1", "pp_gone")
    reading = m.Paper(id="pp_busy", case_id="c1", title="t", status="reading", synthetic=False)
    await store.put("c1", "papers", reading)
    with pytest.raises(actions.Conflict, match="still being read"):
        await actions.delete_paper(deps, "c1", "pp_busy")


async def test_the_case_says_what_its_background_job_is_doing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live, a statement was read and then nothing showed for four and a half minutes while discovery ran."""
    from afterledger.app import actions
    from afterledger.modules import pipeline
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    actions.register_handlers(deps)
    seen: list[Any] = []

    def work() -> Any:
        return (work_now(deps, "c1") or {}).get("step")

    async def read_papers(deps_: Any, case_id: str, paper_ids: list[str]) -> None:
        seen.append(work())

    async def after_reading(deps_: Any, case_id: str) -> None:
        seen.append(work())

    async def nothing(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(pipeline, "read_papers", read_papers)
    monkeypatch.setattr(pipeline, "after_reading", after_reading)
    monkeypatch.setattr(actions.followup, "plan_today", nothing)
    await deps.tasks._handlers["after_papers"]({"case_id": "c1", "paper_ids": ["p1"]})
    assert seen == ["reading", "finding"] and work() is None
    await deps.tasks._handlers["after_papers"]({"case_id": "c1", "paper_ids": []})   # after a paper was removed
    assert seen[2:] == ["finding"] and work() is None


def test_a_payment_after_the_death_says_who_was_paid(deps: Any) -> None:
    """Live, the payments after the death were listed with their raw lines ("Debit on 5 September: Nach Dr Bajaj
    Finance Ltd Car Loan 4Xx1187")."""
    import asyncio

    from afterledger.domain import case as m
    from afterledger.domain.ids import stable_id
    from afterledger.modules.discovery import name_later_payments
    from afterledger.modules.reading import after_death_label, payee_of

    d = date(2026, 9, 5)
    assert payee_of("NACH DR BAJAJ FINANCE LTD CAR LOAN 4XX1187") == "Bajaj Finance Ltd Car Loan"
    assert payee_of("TO TRANSFER-UPI/DR/628431/RAJESH KUMAR/HDFC/rajesh@okhdfc/Payment") == "Rajesh Kumar"
    assert payee_of("NEFT DR LIC OF INDIA PREMIUM 123456789") == "LIC of India Premium"
    assert payee_of("ﾌﾘｺﾐ ﾔﾏﾀﾞ ﾀﾛｳ") == "ヤマダ タロウ" and payee_of("UPI/123/456") is None
    assert after_death_label("UPI/PRIYA DESHPANDE/BENGALURU", d) == "Paid to Priya Deshpande on 5 September"
    assert after_death_label("ATM WDL KOTHRUD 0417", d) == "Cash taken on 5 September"
    assert after_death_label("123456", d) == "Debit on 5 September"

    async def go() -> str:
        store = deps.cases
        await store.put_case(m.CaseFile(id="c1", title="t"))
        tx = m.Transaction(id=stable_id("tx", "pp1", "7"), case_id="c1", paper_id="pp1", date=d,
                           narration="NACH DR BAJAJ FINANCE LTD CAR LOAN 4XX1187", amount=Decimal("14230"),
                           direction="debit", currency="INR", after_death=True)
        await store.put("c1", "transactions", tx)
        await store.put("c1", "values", m.ReadValue(
            id=stable_id("rv", "pp1", "row7", "after_death"), case_id="c1", paper_id="pp1", field="row_7_debit",
            label=after_death_label(tx.narration, d), kind="amount", value="14230.00"))
        group = PaymentGroup(id="g1", case_id="c1", key="k", label=tx.narration, direction="debit", count=1,
                             cadence="once", amount_typical=Decimal("14230"), amount_stable=True, first=d, last=d,
                             transaction_ids=[tx.id], counterparty="Bajaj Finance")
        await name_later_payments(deps, "c1", [group])
        return (await store.list("c1", "values"))[0].label

    assert asyncio.run(go()) == "Paid to Bajaj Finance on 5 September"


async def test_today_shows_only_what_code_offered_and_keeps_its_facts(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor chooses and words Today's tasks; what they rest on is worked out by code. A task none of the
    candidates offered is not shown, a figure the candidate does not have sends the candidate's own words, the date,
    the claim and the sources stay the candidate's, and a greeting with a figure Today does not have is left out
    for the page's own plain hello."""
    from afterledger.agents.schemas import SupervisorOut, TaskOut
    from afterledger.domain import case as m
    from afterledger.domain.ids import stable_id
    from afterledger.modules import followup
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    await deps.cases.put_case(m.CaseFile(id="c1", title="Anil Deshpande", died_on=date(2026, 8, 28)))
    offered: list[dict] = []
    real = followup.call_agent

    async def supervisor(deps_: Any, agent: str, payload: dict, **kw: Any) -> Any:
        if agent != "supervisor":
            return await real(deps_, agent, payload, **kw)
        offered.extend(payload["candidates"])
        first = payload["candidates"][0]
        return SupervisorOut(greeting="Meera, ₹5,00,000 is waiting for you.", tasks=[
            TaskOut(key="sell_the_house", kind="decide", title="Sell the house", body="It is worth ₹90,00,000."),
            TaskOut(key=first["key"], kind="handoff", title="Claim ₹7,77,777 today", body="Before 3 March.",
                    due_on="2026-03-03", claim_id="claim_invented", cites=["made_up"], owner_person_ids=["p_nobody"])])

    monkeypatch.setattr(followup, "call_agent", supervisor)
    tasks = await followup.plan_today(deps, "c1")
    first = offered[0]
    assert [t.id for t in tasks] == [stable_id("task", "c1", first["key"])]
    t = tasks[0]
    assert (t.title, t.body) == (first["title"], first.get("body", ""))
    assert t.kind == first["kind"] and t.claim_id == first.get("claim_id") and t.cites == first.get("cites", [])
    assert (t.due_on.isoformat() if t.due_on else None) == first.get("due_on")
    assert "p_nobody" not in t.owner_person_ids
    case = await deps.cases.get_case("c1")
    assert case.today_note == ""


async def test_a_settled_debt_is_paid_off_everywhere(tmp_path: Path) -> None:
    """Live-like, a car loan settled on the tracker still showed as owed on Today and the money map, its next
    instalment stayed among the deadlines, and the activity said the lender had paid the family."""
    from afterledger.app.views import money as money_view
    from afterledger.domain import case as m
    from afterledger.modules import followup
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    await deps.cases.put_case(m.CaseFile(id="c1", title="Anil Deshpande", died_on=date(2026, 8, 28)))
    await deps.cases.put("c1", "holdings", m.Holding(id="h1", case_id="c1", institution_id="in_bajaj_finance",
                                                     kind="loan", direction="debt", label="Loan",
                                                     value=Decimal("120000"), claim_id="k1"))
    await deps.cases.put("c1", "claims", m.Claim(id="k1", case_id="c1", institution_id="in_bajaj_finance", kind="debt",
                                                 holding_ids=["h1"], title="Bajaj Finance: loan", status="submitted"))
    await deps.cases.put("c1", "groups", PaymentGroup(
        id="g1", case_id="c1", key="k", label="NACH DR BAJAJ FINANCE LTD CAR LOAN", direction="debit", count=6,
        cadence="monthly", amount_typical=Decimal("14230"), amount_stable=True, first=date(2026, 3, 5),
        last=date(2026, 9, 5)))
    await deps.cases.put("c1", "leads", m.Lead(id="l1", case_id="c1", kind="debt", category="loan",
                                               title="A loan with monthly instalments at Bajaj Finance", sentence="",
                                               group_ids=["g1"], status="confirmed", holding_id="h1",
                                               institution_id="in_bajaj_finance"))
    await deps.cases.put("c1", "correspondence", m.Correspondence(
        id="corr1", case_id="c1", claim_id="k1", direction="out", on=date(2026, 10, 12), title="Request to Bajaj Finance",
        letter_text="Please tell me in writing what you still need.", status="sent", expected_on=date(2026, 11, 12),
        expected_title="Reply from Bajaj Finance"))
    before = await followup.deadlines(deps, "c1", refresh=False)
    assert [d["title"] for d in before if d["kind"] == "debt"] == ["Next instalment of ₹14,230"]
    assert [d["title"] for d in before if d["kind"] == "expected"] == ["Reply from Bajaj Finance"]
    assert (await followup.totals(deps, "c1"))["currencies"]["INR"]["owed"] == "120000"

    await followup.record_claim_event(deps, "c1", "k1", "settled", date(2026, 10, 19), amount="120000", rework=False)
    assert not [d for d in await followup.deadlines(deps, "c1", refresh=False) if d["kind"] in ("debt", "expected")]
    from afterledger.app.views import tracker as tracker_view
    track = await tracker_view.tracker(deps, "c1")
    assert not track.letters and not track.expected and [lv.letter.id for lv in track.received] == ["corr1"]
    assert (await followup.totals(deps, "c1"))["currencies"]["INR"]["owed"] == "0"
    view = await money_view.money_map(deps, "c1")
    loan = view.countries[0].institutions[0].holdings[0]
    assert loan.tag == "paid" and not view.countries[0].owed
    said = [a.text for a in await deps.cases.list("c1", "activity")]
    assert any(t.startswith("The debt to") and t.endswith("was settled: ₹1,20,000 paid.") for t in said), said


async def test_what_an_institution_said_waits_for_the_family_and_ends_when_recorded(tmp_path: Path) -> None:
    """A reply or a desk entry proposes an event; nothing changes until the family says yes. A later word on the same
    event replaces the earlier one, and a payment the family records themselves settles every proposal still waiting
    on the claim."""
    from afterledger.domain import case as m
    from afterledger.modules import followup
    from afterledger.modules.common import snapshot
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    await deps.cases.put_case(m.CaseFile(id="c1", title="Anil Deshpande", died_on=date(2026, 8, 28)))
    claim = m.Claim(id="k1", case_id="c1", institution_id="in_test_bank", title="Test Bank: savings account",
                    status="submitted")
    await deps.cases.put("c1", "claims", claim)
    paid = await followup.propose_claim_event(deps, "c1", claim, "settled", date(2026, 10, 19), amount="5000.00",
                                              source="desk", correspondence_id="corr1")
    every = await followup.propose_claim_event(deps, "c1", claim, "complete_set_confirmed", date(2026, 10, 10),
                                               source="reply", correspondence_id="corr2")
    assert paid.title.startswith("Did ") and "₹5,000 on 19 October 2026" in paid.question
    assert paid.payload["amount_text"] == "₹5,000" and every.payload["amount_text"] is None
    assert (await deps.cases.get("c1", "claims", "k1")).status == "submitted"
    # the desk corrects its amount: the family is asked once, about the new one
    fixed = await followup.propose_claim_event(deps, "c1", claim, "settled", date(2026, 10, 19), amount="5500.50",
                                               source="desk", correspondence_id="corr3")
    snap = await snapshot(deps, "c1")
    assert [(c.id, c.payload["amount_text"]) for c in followup.to_confirm(snap) if c.payload["event"] == "settled"] \
        == [(fixed.id, "₹5,500.50")]
    await followup.record_claim_event(deps, "c1", "k1", "settled", date(2026, 10, 19), amount="5500.50", rework=False)
    statuses = {c.id: c.status for c in await deps.cases.list("c1", "checkpoints")}
    assert statuses == {paid.id: "expired", every.id: "expired", fixed.id: "expired"}
    assert not followup.to_confirm(await snapshot(deps, "c1"))
    # a question still open about a claim that is finished records nothing, whatever the answer
    stale = await followup.propose_claim_event(deps, "c1", claim, "complete_set_confirmed", date(2026, 10, 10),
                                               source="reply", correspondence_id="corr4")
    assert await followup.confirm_claim_event(deps, "c1", stale, "yes") is None
    assert (await deps.cases.get("c1", "checkpoints", stale.id)).status == "expired"
    assert (await deps.cases.get("c1", "claims", "k1")).complete_set_on is None
