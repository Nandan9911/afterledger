"""The agent harness around every model and tool call: a second chance for an unusable answer, fallback models,
metering and budgets, safe addresses, and background steps that retry and report."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict

from afterledger.adapters.llm.harness import FallbackLlm, MeteredLlm, overloaded
from afterledger.agents.context import working_on
from afterledger.agents.meter import BudgetExceeded, Meter
from afterledger.agents.runner import AgentError, call_agent
from afterledger.health import ServiceFailure, ServiceHealth
from afterledger.netsafe import allowed
from conftest import offline_settings


class FakeLlm(BaseLlm):
    """Answers with the next item of a script: text, a usage-bearing text, or an exception to raise."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "fake-model"
    script: list[Any] = []
    seen: list[str] = []

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.model or "")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        tokens = item[1] if isinstance(item, tuple) else None
        text = item[0] if isinstance(item, tuple) else item
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(
                              prompt_token_count=tokens[0], candidates_token_count=tokens[1],
                              thoughts_token_count=tokens[2]) if tokens else None)


class Overloaded(Exception):
    code = 429


def _request(model: str = "gemini-3.1-pro-preview") -> LlmRequest:
    return LlmRequest(model=model, contents=[types.Content(role="user", parts=[types.Part(text="{}")])])


async def _collect(llm: BaseLlm, request: LlmRequest) -> list[LlmResponse]:
    return [r async for r in llm.generate_content_async(request)]


# ---- a second chance for an unusable answer ------------------------------------------------------------------

def _scripted_router(deps: Any, answers: list[Any]) -> None:
    """intent_router answers from this list instead of its oracle, one answer per call."""
    original = deps.models._oracle

    def oracle_for(name: str):
        if name != "intent_router":
            return original(name)
        return lambda req: answers.pop(0)
    deps.models._oracle = oracle_for


async def test_an_unusable_answer_is_sent_back_once(deps: Any) -> None:
    _scripted_router(deps, [{"intent": "a question about the weather"},          # not one of the allowed intents
                            {"intent": "case_question", "topic": "payment"}])
    out = await call_agent(deps, "intent_router", {"key": "intent_router/t1", "question": "When will the bank pay?"})
    assert out.intent == "case_question"
    lines = (deps.settings.state_dir / "traces").glob("*.jsonl")
    text = "".join(f.read_text(encoding="utf-8") for f in lines)
    assert '"unusable_answer"' in text and '"tries": 2' in text


async def test_two_unusable_answers_fail_the_step_loudly(deps: Any) -> None:
    _scripted_router(deps, [{"intent": "weather"}, {"topic": "no intent at all"}])
    with pytest.raises(AgentError, match="no usable answer after 2 tries"):
        await call_agent(deps, "intent_router", {"key": "intent_router/t2", "question": "?"})


async def test_with_reask_off_one_unusable_answer_fails(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, reask_malformed=0))
    _scripted_router(deps, [{"intent": "weather"}, {"intent": "case_question"}])
    with pytest.raises(AgentError, match="after 1 tries"):
        await call_agent(deps, "intent_router", {"key": "intent_router/t3", "question": "?"})


# ---- fallback models -----------------------------------------------------------------------------------------

async def test_an_overloaded_model_falls_back_and_says_so() -> None:
    health = ServiceHealth()
    pro = FakeLlm(model="gemini-3.1-pro-preview", script=[Overloaded("RESOURCE_EXHAUSTED")], seen=[])
    flash = FakeLlm(model="gemini-3.8-flash", script=['{"ok": true}'], seen=[])
    llm = FallbackLlm(model="gemini-3.1-pro-preview", agent="reader_c", health=health,
                      chain=[("gemini-3.1-pro-preview", pro), ("gemini-3.8-flash", flash)])
    out = await _collect(llm, _request())
    assert out[0].custom_metadata == {"model": "gemini-3.8-flash", "fallback_from": "gemini-3.1-pro-preview"}
    assert flash.seen == ["gemini-3.8-flash"]                      # the request names the model that answers
    assert health.snapshot()["failures"]["gemini:gemini-3.1-pro-preview"]["count"] == 1


async def test_a_short_error_spell_is_waited_out_before_a_smaller_model() -> None:
    """Gemini 3.1 Pro answered 500 INTERNAL three times on 15 October and normally a minute later: the call waits a
    moment and asks Pro once more, so a strict run does not stop, and no smaller model answers."""
    class Internal(Exception):
        code = 500

    health = ServiceHealth(strict=True)
    pro = FakeLlm(model="gemini-3.1-pro-preview", script=[Internal("500 INTERNAL"), '{"ok": true}'], seen=[])
    flash = FakeLlm(model="gemini-3.8-flash", script=[], seen=[])
    llm = FallbackLlm(model="gemini-3.1-pro-preview", agent="research_adjudicator", health=health,
                      overload_pause_s=0.01, chain=[("gemini-3.1-pro-preview", pro), ("gemini-3.8-flash", flash)])
    out = await _collect(llm, _request())
    assert out[0].custom_metadata is None and flash.seen == [] and health.snapshot()["failures"] == {}


async def test_a_strict_live_run_stops_instead_of_falling_back() -> None:
    pro = FakeLlm(model="gemini-3.1-pro-preview", script=[Overloaded("RESOURCE_EXHAUSTED")], seen=[])
    flash = FakeLlm(model="gemini-3.8-flash", script=['{"ok": true}'], seen=[])
    llm = FallbackLlm(model="gemini-3.1-pro-preview", agent="reader_c", health=ServiceHealth(strict=True),
                      chain=[("gemini-3.1-pro-preview", pro), ("gemini-3.8-flash", flash)])
    with pytest.raises(ServiceFailure, match=r"gemini:gemini-3\.1-pro-preview"):
        await _collect(llm, _request())
    assert flash.seen == []


async def test_a_bad_request_is_not_retried_on_another_model() -> None:
    bad = ValueError("400 INVALID_ARGUMENT: schema")
    assert not overloaded(bad)
    pro = FakeLlm(model="p", script=[bad], seen=[])
    flash = FakeLlm(model="f", script=['{}'], seen=[])
    llm = FallbackLlm(model="p", agent="x", health=ServiceHealth(), chain=[("p", pro), ("f", flash)])
    with pytest.raises(ValueError):
        await _collect(llm, _request("p"))


class PastDeadline(Exception):
    code = 499

    def __str__(self) -> str:
        return "499 CANCELLED. The operation was cancelled."


async def test_a_passed_deadline_is_a_retry_not_a_failure() -> None:
    from afterledger.adapters.llm.harness import ModelTimeout
    seen: list[str] = []
    health = ServiceHealth(strict=True)
    llm = FallbackLlm(model="gemini-3.8-flash", agent="listener", health=health,
                      chain=[("gemini-3.8-flash", FakeLlm(script=[PastDeadline()], seen=seen)),
                             ("gemini-3.5-flash-lite", FakeLlm(script=["unused"], seen=seen))])
    with pytest.raises(ModelTimeout):
        await _collect(llm, _request("gemini-3.8-flash"))
    first, second = llm.chain[0][1], llm.chain[1][1]
    assert first.seen == ["gemini-3.8-flash"] and second.seen == []                 # no fallback to the next tier
    assert health.snapshot()["failures"] == {}                                      # and nothing marked as failing
    assert isinstance(ModelTimeout("x"), TimeoutError)                              # the node retries it


class Busy(Exception):
    code = 504

    def __str__(self) -> str:
        return "504 DEADLINE_EXCEEDED. Deadline expired before operation could complete."


class NotServed(Exception):
    code = 404

    def __str__(self) -> str:
        return "404 NOT_FOUND. Publisher model is not found."


def _flash_routes(health: Any, main: list[Any], backup: list[Any], lite: list[Any] = ()) -> FallbackLlm:
    return FallbackLlm(model="gemini-3.8-flash", agent="listener", health=health, cooldown_s=300, chain=[
        ("gemini-3.8-flash", FakeLlm(model="gemini-3.8-flash", script=list(main), seen=[])),
        ("gemini-3.8-flash@us", FakeLlm(model="gemini-3.8-flash", script=list(backup), seen=[])),
        ("gemini-3.5-flash-lite", FakeLlm(model="gemini-3.5-flash-lite", script=list(lite), seen=[]))])


async def test_a_slow_endpoint_gives_way_to_the_same_model_on_another() -> None:
    """Gemini 3.8 Flash slow on the global endpoint (15 October): the call is answered by the same model on the us
    endpoint, the global one is passed over for the next calls, and nothing is marked as failing (a strict run goes
    on: the answer comes from the same model)."""
    health = ServiceHealth(strict=True)
    llm = _flash_routes(health, main=[Busy()], backup=['{"ok": 1}', '{"ok": 2}'])
    out = await _collect(llm, _request("gemini-3.8-flash"))
    assert out[0].custom_metadata == {"model": "gemini-3.8-flash@us", "fallback_from": "gemini-3.8-flash"}
    assert await _collect(llm, _request("gemini-3.8-flash"))                       # straight to us this time
    main, backup, lite = (llm.chain[i][1] for i in range(3))
    assert main.seen == ["gemini-3.8-flash"] and backup.seen == ["gemini-3.8-flash"] * 2 and lite.seen == []
    snap = health.snapshot()
    assert snap["failures"] == {} and snap["slow"]["gemini-3.8-flash"]["passed_over"] is True
    assert snap["ok"]["gemini:gemini-3.8-flash@us"] == 2


class Silent(FakeLlm):
    """An endpoint that takes its time: answers only after `delay` seconds (or is cancelled before)."""
    delay: float = 5.0
    cancelled: bool = False

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.seen.append(llm_request.model or "")
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text='{"late": true}')]))


async def test_a_silent_endpoint_is_hedged_on_the_backup_and_the_first_answer_wins() -> None:
    """No answer from global after the hedge delay: the same request goes to us too, us answers first, the global
    request is cancelled, and global is passed over for the next call."""
    health = ServiceHealth(strict=True)
    main = Silent(model="gemini-3.8-flash", seen=[], delay=5.0)
    backup = FakeLlm(model="gemini-3.8-flash", script=['{"ok": 1}', '{"ok": 2}'], seen=[])
    llm = FallbackLlm(model="gemini-3.8-flash", agent="listener", health=health, cooldown_s=300, hedge_after_s=0.05,
                      chain=[("gemini-3.8-flash", main), ("gemini-3.8-flash@us", backup)])
    t0 = asyncio.get_running_loop().time()
    out = await _collect(llm, _request("gemini-3.8-flash"))
    assert asyncio.get_running_loop().time() - t0 < 1.0                             # seconds, not the deadline
    assert out[0].content.parts[0].text == '{"ok": 1}' and out[0].custom_metadata["model"] == "gemini-3.8-flash@us"
    assert main.cancelled and health.snapshot()["slow"]["gemini-3.8-flash"]["passed_over"]
    assert "answered first" in health.snapshot()["slow"]["gemini-3.8-flash"]["last"]["error"]
    await _collect(llm, _request("gemini-3.8-flash"))
    assert main.seen == ["gemini-3.8-flash"] and len(backup.seen) == 2              # straight to us next time
    assert health.snapshot()["failures"] == {}


async def test_an_endpoint_asked_late_is_not_called_slow_for_losing() -> None:
    """The main endpoint answers just after the hedge went out: it wins, and the backup, asked a moment ago, is not
    marked slow."""
    health = ServiceHealth()
    main = Silent(model="gemini-3.8-flash", seen=[], delay=0.15)
    backup = Silent(model="gemini-3.8-flash", seen=[], delay=5.0)
    llm = FallbackLlm(model="gemini-3.8-flash", agent="reader_a", health=health, hedge_after_s=0.1,
                      chain=[("gemini-3.8-flash", main), ("gemini-3.8-flash@us", backup)])
    out = await _collect(llm, _request("gemini-3.8-flash"))
    assert out[0].custom_metadata is None and backup.seen == ["gemini-3.8-flash"]
    assert health.snapshot()["slow"] == {}


async def test_a_peer_model_answers_when_the_model_is_slow_everywhere() -> None:
    """Gemini 3.8 Flash silent on both endpoints: its peer, Gemini 3.6 Flash, is asked next and answers under its
    own name; nothing is marked as failing, because the answer is no worse."""
    health = ServiceHealth(strict=True)
    peer = FakeLlm(model="gemini-3.6-flash", script=['{"ok": "peer"}'], seen=[])
    llm = FallbackLlm(model="gemini-3.8-flash", agent="listener", health=health, hedge_after_s=0.05,
                      chain=[("gemini-3.8-flash", Silent(seen=[], delay=5.0)),
                             ("gemini-3.8-flash@us", Silent(seen=[], delay=5.0)),
                             ("gemini-3.6-flash", peer),
                             ("gemini-3.5-flash-lite", FakeLlm(script=[], seen=[]))],
                      groups=[["gemini-3.8-flash", "gemini-3.8-flash@us", "gemini-3.6-flash"], ["gemini-3.5-flash-lite"]])
    out = await _collect(llm, _request("gemini-3.8-flash"))
    assert out[0].custom_metadata == {"model": "gemini-3.6-flash", "fallback_from": "gemini-3.8-flash"}
    assert peer.seen == ["gemini-3.6-flash"] and health.snapshot()["failures"] == {}
    assert set(health.snapshot()["slow"]) == {"gemini-3.8-flash", "gemini-3.8-flash@us"}


async def test_a_quick_answer_is_never_hedged() -> None:
    main = Silent(model="gemini-3.8-flash", seen=[], delay=0.01)
    backup = FakeLlm(model="gemini-3.8-flash", script=[], seen=[])
    llm = FallbackLlm(model="gemini-3.8-flash", agent="listener", health=ServiceHealth(), hedge_after_s=1.0,
                      chain=[("gemini-3.8-flash", main), ("gemini-3.8-flash@us", backup)])
    out = await _collect(llm, _request("gemini-3.8-flash"))
    assert out[0].content.parts[0].text == '{"late": true}' and out[0].custom_metadata is None and backup.seen == []


def test_hedging_waits_longer_for_slow_agents(tmp_path: Any) -> None:
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, llm="gemini", project="test-project", llm_hedge_after_s=30,
                                       llm_hedge_interactive_s=0))
    assert deps.models.model_for(AGENTS["listener"]).inner.hedge_after_s == 30
    assert deps.models.model_for(AGENTS["reader_c"]).inner.hedge_after_s == 60
    # a person waiting on an answer, typed or spoken: sooner, even for an agent that uses tools
    quick = build_deps(offline_settings(tmp_path / "quick", llm="gemini", project="test-project", llm_hedge_after_s=30,
                                        llm_hedge_interactive_s=8))
    assert quick.models.model_for(AGENTS["case_answerer"]).inner.hedge_after_s == 8
    assert quick.models.model_for(AGENTS["listener"]).inner.hedge_after_s == 8
    assert quick.models.model_for(AGENTS["reader_c"]).inner.hedge_after_s == 60
    off = build_deps(offline_settings(tmp_path / "off", llm="gemini", project="test-project", llm_hedge_after_s=0))
    assert off.models.model_for(AGENTS["listener"]).inner.hedge_after_s is None


async def test_slow_everywhere_is_tried_again_later_not_given_to_a_smaller_model() -> None:
    from afterledger.adapters.llm.harness import ModelTimeout
    health = ServiceHealth(strict=True)
    llm = _flash_routes(health, main=[Busy()], backup=[PastDeadline()], lite=["unused"])
    with pytest.raises(ModelTimeout):
        await _collect(llm, _request("gemini-3.8-flash"))
    assert llm.chain[2][1].seen == [] and health.snapshot()["failures"] == {}


async def test_an_endpoint_that_does_not_serve_the_model_is_skipped() -> None:
    """A preview model runs only on the global endpoint: the us route answers 404 once and is not tried again."""
    health = ServiceHealth()
    llm = FallbackLlm(model="gemini-3.1-pro-preview", agent="reader_c", health=health, chain=[
        ("gemini-3.1-pro-preview", FakeLlm(script=[Overloaded("RESOURCE_EXHAUSTED"), Busy()], seen=[])),
        ("gemini-3.1-pro-preview@us", FakeLlm(script=[NotServed()], seen=[])),
        ("gemini-3.8-flash", FakeLlm(script=['{"ok": true}'], seen=[]))])
    out = await _collect(llm, _request())
    assert out[0].custom_metadata["model"] == "gemini-3.8-flash"                   # overloaded everywhere: a tier down
    assert health.snapshot()["failures"]["gemini:gemini-3.1-pro-preview"]["count"] == 1
    assert health.route_missing("gemini-3.1-pro-preview@us")
    from afterledger.adapters.llm.harness import ModelTimeout
    with pytest.raises(ModelTimeout):          # next call: pro on global only (asking us again would find no script)
        await _collect(llm, _request())


def test_a_slow_endpoint_is_passed_over_longer_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import afterledger.health as h
    now = [1000.0]
    monkeypatch.setattr(h.time, "monotonic", lambda: now[0])
    health = ServiceHealth()
    waits = []
    for _ in range(6):                                  # slow again each time it is tried after its pause
        waits.append(health.route_slow("gemini-3.8-flash", "504", 300))
        now[0] += waits[-1] + 1
    assert waits == [300, 600, 1200, 2400, 3600, 3600]
    health.route_ok("gemini-3.8-flash")
    calls_under_way = [health.route_slow("gemini-3.8-flash", "504", 300) for _ in range(3)]
    assert calls_under_way == [300, 300, 300]           # found slow by three calls at once: one spell, not three
    health.route_ok("gemini-3.8-flash")
    assert not health.route_avoided("gemini-3.8-flash")
    assert health.route_slow("gemini-3.8-flash", "504", 300) == 300                 # a success started it over


def test_routes_put_each_model_on_its_backup_endpoint(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    s = offline_settings(tmp_path)
    assert s.route_groups("flash") == [["gemini-3.8-flash", "gemini-3.8-flash@us", "gemini-3.6-flash"],
                                       ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite@us"]]
    assert s.route_groups("pro")[0] == ["gemini-3.1-pro-preview", "gemini-3.1-pro-preview@us"]
    assert offline_settings(tmp_path, model_flash_peers="").routes("flash")[:3] == [
        "gemini-3.8-flash", "gemini-3.8-flash@us", "gemini-3.5-flash-lite"]
    assert offline_settings(tmp_path, llm_backup_location="").routes("lite") == ["gemini-3.5-flash-lite"]
    assert offline_settings(tmp_path, fallback=False).routes("flash") == ["gemini-3.8-flash"]
    from afterledger.agents.meter import Prices
    prices = Prices.load(s.knowledge_dir)
    assert prices.cost("gemini-3.8-flash@us", 1_000_000, 0) == pytest.approx(0.825)
    assert prices.cost("gemini-3.8-flash", 1_000_000, 0) == pytest.approx(0.75)


def test_live_requests_carry_a_deadline(tmp_path: Any) -> None:
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, llm="gemini", project="test-project", llm_http_timeout_s=90))
    assert deps.models.model_for(AGENTS["listener"]).http_timeout_s == 90
    assert deps.models.model_for(AGENTS["reader_b"]).http_timeout_s == 180            # it reads page images
    assert deps.models.model_for(AGENTS["reader_c"]).http_timeout_s == 180            # a pro agent
    offline = build_deps(offline_settings(tmp_path / "offline"))
    assert offline.models.model_for(AGENTS["listener"]).http_timeout_s is None


def test_every_live_model_falls_back_down_the_tiers(tmp_path: Any) -> None:
    s = offline_settings(tmp_path)
    assert s.fallback_chain("pro") == [s.model_pro, s.model_flash, s.model_lite]
    assert s.fallback_chain("lite") == [s.model_lite]
    assert offline_settings(tmp_path, fallback=False).fallback_chain("pro") == [s.model_pro]


def test_wrappers_carry_the_real_model_id(tmp_path: Any) -> None:
    """ADK names the request's model after the agent's model object, so a wrapper must carry the real id."""
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, llm="gemini", llm_record_dir=tmp_path / "cassettes",
                                       project="test-project"))
    for name in ("reader_c", "investigator", "triage"):
        spec = AGENTS[name]
        m = deps.models.model_for(spec)
        assert isinstance(m, MeteredLlm) and m.model == deps.settings.model_for(spec.tier)
        inner = m.inner
        first = inner.chain[0][1] if isinstance(inner, FallbackLlm) else inner
        assert first.model == deps.settings.model_for(spec.tier)


# ---- metering and budgets ------------------------------------------------------------------------------------

async def test_every_call_is_metered_with_tokens_and_cost(deps: Any) -> None:
    inner = FakeLlm(model="gemini-3.8-flash", script=[("ok", (1_000_000, 100_000, 100_000))], seen=[])
    llm = MeteredLlm(model="gemini-3.8-flash", agent="explainer", inner=inner, meter=deps.meter)
    with working_on("case-1"):
        await _collect(llm, _request("gemini-3.8-flash"))
    u = deps.meter.by_case["case-1"]
    assert (u.calls, u.tokens_in, u.tokens_out, u.tokens_thinking) == (1, 1_000_000, 100_000, 100_000)
    assert u.cost_usd == pytest.approx(0.75 + 0.2 * 3.75)          # thinking is billed as output
    item = await deps.activity("case-1", "planning", "Planned.")
    assert item.detail["usage"]["calls"] == 1                       # the step's cost rides on its activity line
    assert (await deps.activity("case-1", "planning", "Again.")).detail.get("usage") is None


async def test_a_spent_budget_stops_extra_rounds_but_not_the_case(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, case_budget_usd=0.5))
    spend = FakeLlm(model="gemini-3.8-flash", script=[("ok", (1_000_000, 0, 0))], seen=[])
    llm = MeteredLlm(model="gemini-3.8-flash", agent="explainer", inner=spend, meter=deps.meter)
    with working_on("case-2"):
        await _collect(llm, _request("gemini-3.8-flash"))           # $0.75 of core work: allowed
    more = MeteredLlm(model="gemini-3.8-flash", agent="researcher_primary",
                      inner=FakeLlm(script=["x", "y"], seen=[]), meter=deps.meter)
    with working_on("case-2", "research"), pytest.raises(BudgetExceeded):
        await _collect(more, _request("gemini-3.8-flash"))
    with working_on("case-2"):
        assert await _collect(more, _request("gemini-3.8-flash"))  # the case's own work carries on


async def test_activity_says_when_most_of_the_budget_is_spent(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, case_budget_usd=1.0))
    await deps.cases.put_case(await _a_case(deps, "case-n"))
    for _ in range(2):                                              # $0.75 (no notice), then $1.50 (one notice)
        with working_on("case-n"):
            await _collect(MeteredLlm(model="gemini-3.8-flash", agent="explainer", meter=deps.meter,
                                      inner=FakeLlm(script=[("ok", (1_000_000, 0, 0))], seen=[])),
                           _request("gemini-3.8-flash"))
    notices = [a for a in await deps.cases.list("case-n", "activity") if "budget" in a.text]
    assert len(notices) == 1 and notices[0].kind == "warning" and "$1.50 of its $1.00" in notices[0].text


async def test_the_spend_comes_back_after_a_restart(tmp_path: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, case_budget_usd=0.5))
    await deps.cases.put_case(await _a_case(deps, "case-3"))
    with working_on("case-3"):
        await _collect(MeteredLlm(model="gemini-3.8-flash", agent="explainer", meter=deps.meter,
                                  inner=FakeLlm(script=[("ok", (1_000_000, 0, 0))], seen=[])),
                       _request("gemini-3.8-flash"))
    await deps.activity("case-3", "planning", "Planned.")
    fresh = Meter(deps)
    with pytest.raises(BudgetExceeded):
        await fresh.check("case-3", "repair")


async def _a_case(deps: Any, case_id: str) -> Any:
    from afterledger.domain.case import CaseFile
    return CaseFile(id=case_id, title="Test", created_at=deps.now())


@pytest.fixture
def client(tmp_path: Any) -> Any:
    from conftest import api_client
    yield from api_client(tmp_path)


def test_usage_and_service_health_are_reported(client: Any) -> None:
    from afterledger.health import health_of
    deps = client.deps

    async def a_costly_step() -> None:
        await deps.cases.put_case(await _a_case(deps, "case-u"))
        with working_on("case-u"):
            await _collect(MeteredLlm(model="gemini-3.8-flash", agent="explainer", meter=deps.meter,
                                      inner=FakeLlm(script=[("ok", (40_000, 2_000, 1_000))], seen=[])),
                           _request("gemini-3.8-flash"))
        await deps.activity("case-u", "planning", "Planned.")
    client.portal.call(a_costly_step)
    used = client.get("/v1/cases/case-u/activity").json()["usage"]
    assert (used["calls"], used["tokens_in"], used["tokens_out"], used["tokens_thinking"]) == (1, 40_000, 2_000, 1_000)
    report = client.get("/v1/eval/usage").json()
    assert report["prices_as_of"] and report["since"]
    assert report["cases"][0]["case_id"] == "case-u" and report["cases"][0]["usage"]["cost_usd"] > 0
    assert report["by_agent"]["explainer"]["calls"] == 1 and report["by_model"]["gemini-3.8-flash"]["calls"] == 1
    health_of(deps).failed("document_ai", RuntimeError("403 permission denied"))
    failures = client.get("/v1/meta").json()["health"]["failures"]
    assert failures["document_ai"]["count"] == 1 and "403" in failures["document_ai"]["error"]


# ---- addresses an agent may make AfterLedger read ------------------------------------------------------------

@pytest.mark.parametrize("url", ["http://localhost:8080/v1/meta", "http://10.0.0.1/", "http://169.254.169.254/x",
                                 "http://[::1]/", "file:///etc/passwd", "https://user:pw@www.rbi.org.in/",
                                 "http://metadata.google.internal/computeMetadata/v1/", "ftp://example.org/a",
                                 "http://192.168.1.10/", "http://100.64.0.5/"])
def test_private_and_odd_addresses_are_refused(url: str) -> None:
    ok, why = allowed(url, resolve=False)
    assert not ok and why


def test_public_pages_are_allowed() -> None:
    assert allowed("https://www.rbi.org.in/Scripts/NotificationUser.aspx", resolve=False) == (True, "")


async def test_the_guard_answers_for_a_refused_fetch(deps: Any) -> None:
    from afterledger.agents.plugins import GuardPlugin

    class Tool:
        name = "fetch_page"
    result = await GuardPlugin(deps).before_tool_callback(tool=Tool(), tool_args={"url": "http://127.0.0.1:8080/"},
                                                          tool_context=None)
    assert result and "Not read" in result["error"]
    assert await GuardPlugin(deps).before_tool_callback(tool=Tool(), tool_args={"url": "https://www.gov.uk/"},
                                                        tool_context=None) is None


async def test_an_agent_that_keeps_calling_tools_is_told_to_answer(tmp_path: Any) -> None:
    from afterledger.agents.plugins import GuardPlugin
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, max_tool_calls=3))

    class Tool:
        name = "search_knowledge"

    class Turn:
        invocation_id, agent_name, _node_path = "inv-1", "investigator", "discover/investigate@2"

    class OtherWorker(Turn):
        _node_path = "discover/investigate@3"
    guard = GuardPlugin(deps)
    answers = [await guard.before_tool_callback(tool=Tool(), tool_args={"query": "x"}, tool_context=Turn())
               for _ in range(4)]
    assert answers[:3] == [None, None, None] and "Tool limit reached" in answers[3]["error"]
    assert await guard.before_tool_callback(tool=Tool(), tool_args={"query": "x"}, tool_context=OtherWorker()) is None
    # a live model went on calling the refused tool: its next request goes without tools, so it can only answer
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types
    spent = LlmRequest(config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=[
        types.FunctionDeclaration(name="search_knowledge")])]))
    await guard.before_model_callback(callback_context=Turn(), llm_request=spent)
    assert spent.config.tool_config.function_calling_config.mode == types.FunctionCallingConfigMode.NONE
    fresh = LlmRequest(config=types.GenerateContentConfig(tools=spent.config.tools))
    await guard.before_model_callback(callback_context=OtherWorker(), llm_request=fresh)
    assert fresh.config.tool_config is None


# ---- background steps ----------------------------------------------------------------------------------------

async def test_a_failing_background_step_is_retried_then_reported() -> None:
    from afterledger.adapters.local.services import InProcessTaskQueue
    q = InProcessTaskQueue(attempts=3, backoff=0.01)
    calls: list[int] = []
    reported: list[str] = []

    async def flaky(payload: dict) -> None:
        calls.append(1)
        if len(calls) < 3:
            raise RuntimeError("flaky")

    async def broken(payload: dict) -> None:
        raise RuntimeError("always")

    async def on_failure(kind: str, payload: dict, error: BaseException) -> None:
        reported.append(kind)

    q.on_failure = on_failure
    q.register("flaky", flaky)
    q.register("broken", broken)
    await q.enqueue("flaky", {})
    await q.enqueue("broken", {})
    await q.drain()
    assert len(calls) == 3 and reported == ["broken"]


async def test_a_missing_case_is_not_retried() -> None:
    from afterledger.adapters.local.services import InProcessTaskQueue
    q = InProcessTaskQueue(attempts=3, backoff=0.01)
    calls: list[int] = []

    class NotFound(Exception):
        pass

    async def gone(payload: dict) -> None:
        calls.append(1)
        raise NotFound("no case")

    q.register("gone", gone)
    await q.enqueue("gone", {})
    await q.drain()
    await asyncio.sleep(0)
    assert calls == [1]


async def test_an_agent_that_ignores_the_tool_limit_still_has_to_answer(tmp_path: Any) -> None:
    """Gemini 3.8 Flash called a refused search more than a hundred times in one turn on 15 October, until the
    node's ten-minute timeout: past the limit the agent is asked without its tools, through a real ADK run."""

    from google.adk import Agent, Workflow
    from google.adk.models._capabilities import LlmCapabilities
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from afterledger.agents.runner import agent_node, run_workflow
    from afterledger.agents.schemas import JurisdictionResearchOut
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, max_tool_calls=3))

    class Looper(BaseLlm):
        model: str = "looper"
        asked: int = 0

        @property
        def capabilities(self) -> LlmCapabilities:
            return LlmCapabilities(output_schema_and_tools=True)

        async def generate_content_async(self, llm_request: LlmRequest,
                                         stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            self.asked += 1
            tc = llm_request.config.tool_config if llm_request.config else None
            if tc is not None and tc.function_calling_config.mode == types.FunctionCallingConfigMode.NONE:
                part = types.Part(text='{"findings": [], "gaps": ["No rule on the applicable law was found."]}')
            else:
                part = types.Part(function_call=types.FunctionCall(name="search_knowledge",
                                                                   args={"query": "applicable law succession"}))
            yield LlmResponse(content=types.Content(role="model", parts=[part]))

    async def search_knowledge(query: str) -> dict:
        """Searches the stored rules."""
        return {"assertions": []}
    model = Looper()
    agent = Agent(name="jurisdiction_researcher", model=model, instruction="Find the rules.",
                  output_schema=JurisdictionResearchOut, tools=[search_knowledge], include_contents="none")
    wf = Workflow(name="loop", edges=[("START", agent_node(deps, agent, tools=True))])
    out = await run_workflow(deps, wf, {"key": "loop"})
    assert out["__last__"]["gaps"] == ["No rule on the applicable law was found."]
    assert model.asked <= 6


def test_a_dropped_connection_is_retried() -> None:
    """On 15 October a token refresh lost its connection through the proxy mid-run (google.auth's TransportError over
    http.client's RemoteDisconnected), and the step failed outright: the agent node now retries it like a timeout."""
    from http.client import RemoteDisconnected

    from google.adk.workflow import RetryConfig
    from google.adk.workflow._node_state import NodeState
    from google.adk.workflow.utils._retry_utils import _should_retry_node
    from google.auth.exceptions import TransportError

    from afterledger.agents.runner import TRANSIENT
    config = RetryConfig(max_attempts=2, exceptions=TRANSIENT)
    state = NodeState(attempt_count=1)
    for error in (TransportError("Connection aborted."), RemoteDisconnected("closed"), TimeoutError()):
        assert _should_retry_node(error, config, state), error
    assert not _should_retry_node(ValueError("a bad answer is re-asked, not retried"), config, state)


async def test_a_node_that_answers_on_its_retry_is_not_failed(tmp_path: Any) -> None:
    """ADK reports a node's failed try as an event and then retries the node. On 15 October a statement's reader timed
    out once, its retry read the statement, and the run was still failed on the first try's error, so the family's
    statement went unread. A retry that answers wins, alone or as one of several parallel workers; a node that fails
    every try still fails the run."""
    from google.adk import Workflow
    from google.adk.workflow import RetryConfig, node

    from afterledger.agents.runner import TRANSIENT, run_workflow
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(tmp_path))
    retry = RetryConfig(max_attempts=2, initial_delay=0.01, exceptions=TRANSIENT)
    tries: dict[Any, int] = {}

    async def slow_once(node_input: Any) -> dict:
        tries["one"] = tries.get("one", 0) + 1
        if tries["one"] == 1:
            raise TimeoutError("the first try ran past its deadline")
        return {"read": True}

    out = await run_workflow(deps, Workflow(name="one", edges=[("START", node(slow_once, retry_config=retry))]), {})
    assert out["__last__"] == {"read": True} and tries["one"] == 2

    def pages(node_input: Any) -> list:
        return [{"page": 1}, {"page": 2}, {"page": 3}]

    async def read(node_input: dict) -> dict:
        p = node_input["page"]
        tries[p] = tries.get(p, 0) + 1
        if p == 2 and tries[p] == 1:
            raise TimeoutError("page 2 ran past its deadline")
        return {"page": p}

    wf = Workflow(name="fan", edges=[("START", pages, node(read, parallel_worker=True, retry_config=retry))])
    out = await run_workflow(deps, wf, {})
    assert out["__last__"] == [{"page": 1}, {"page": 2}, {"page": 3}]

    async def always_slow(node_input: Any) -> dict:
        raise TimeoutError("still past its deadline")

    with pytest.raises((AgentError, TimeoutError), match="still past its deadline"):
        await run_workflow(deps, Workflow(name="never", edges=[("START", node(always_slow, retry_config=retry))]), {})
