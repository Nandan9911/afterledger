"""Record and replay: a live run's model calls are written as cassettes, and a replay plays them back exactly. A call
with no cassette falls back to the scripted answer (and the case is labelled scripted), or, in a pure replay, fails.

Offline, the "live" model is the scripted one: what is tested is the recording, the cassette files and the replay,
over text answers, tool calls and a whole family's case."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import pytest
from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict

from afterledger.adapters.llm.models import RecordingLlm, ReplayLlm
from conftest import offline_settings


class Answers(BaseLlm):
    """A stand-in live model: answers with the next item, a text or a list of (tool, args) calls."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "gemini-3.8-flash"
    script: list[Any] = []

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        item = self.script.pop(0)
        if isinstance(item, list):            # signed, as Gemini 3 signs each function call it makes
            parts = [types.Part(function_call=types.FunctionCall(name=n, args=a), thought_signature=b"sig:" + n.encode())
                     for n, a in item]
        else:
            parts = [types.Part(text=item)]
        yield LlmResponse(content=types.Content(role="model", parts=parts),
                          usage_metadata=types.GenerateContentResponseUsageMetadata(
                              prompt_token_count=1200, candidates_token_count=80, thoughts_token_count=40))


def _turn(payload: dict, tool_results: list[tuple[str, dict]] = ()) -> LlmRequest:
    contents = [types.Content(role="user", parts=[types.Part(text=json.dumps(payload))])]
    for name, response in tool_results:
        contents.append(types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(name=name))]))
        contents.append(types.Content(role="user", parts=[types.Part(
            function_response=types.FunctionResponse(name=name, response=response))]))
    return LlmRequest(model="gemini-3.8-flash", contents=contents)


async def _parts(llm: BaseLlm, req: LlmRequest) -> list[dict]:
    out = []
    async for r in llm.generate_content_async(req):
        for p in r.content.parts:
            if p.function_call:
                out.append({"call": p.function_call.name, "args": dict(p.function_call.args or {})})
            else:
                out.append({"text": p.text})
    return out


async def test_answers_and_tool_calls_replay_exactly(tmp_path: Path) -> None:
    live = Answers(script=[[("fetch_page", {"url": "https://www.gov.uk/x"})], '{"answer": "two steps"}',
                           '{"answer": "a plain answer"}'])
    rec = RecordingLlm(model="gemini-3.8-flash", agent="researcher_primary", inner=live, record_dir=tmp_path)
    a = {"key": "research/fr/q1"}
    first = await _parts(rec, _turn(a))                                             # step 0: a tool call
    second = await _parts(rec, _turn(a, [("fetch_page", {"text": "Article 1"})]))   # step 1: the answer
    other = await _parts(rec, _turn({"no": "key"}))                                 # keyed by the pack's hash
    files = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.json")}
    keyed = {"researcher_primary/research_fr_q1__0.json", "researcher_primary/research_fr_q1__1.json"}
    assert keyed < files and len(files) == 3
    assert next(iter(files - keyed)).startswith("researcher_primary/h_")
    cassette = json.loads((tmp_path / "researcher_primary/research_fr_q1__0.json").read_text(encoding="utf-8"))
    assert cassette["usage"] == {"tokens_in": 1200, "tokens_out": 80, "tokens_thinking": 40}

    replay = ReplayLlm(model="gemini-3.8-flash", agent="researcher_primary", replay_dir=tmp_path, strict=True)
    assert await _parts(replay, _turn(a)) == first
    assert await _parts(replay, _turn(a, [("fetch_page", {"text": "Article 1"})])) == second
    assert await _parts(replay, _turn({"no": "key"})) == other
    with pytest.raises(FileNotFoundError):
        await _parts(replay, _turn({"key": "never/recorded"}))


async def test_one_key_for_two_requests_keeps_both_answers(tmp_path: Path) -> None:
    """The same key for different requests (say, the day's note for two family members) keeps both cassettes, told
    apart by the request's fingerprint, which ignores record ids and clock times."""
    rec = RecordingLlm(model="gemini-3.8-flash", agent="supervisor", record_dir=tmp_path,
                       inner=Answers(script=['{"greeting": "Good morning, Anna."}', '{"greeting": "Good morning, Kenta."}']))
    anna = {"key": "supervisor/tanaka/2026-10-14", "viewer": "Anna", "task": "task_0123456789ab", "at": "2026-10-14T09:00:00+02:00"}
    kenta = {**anna, "viewer": "Kenta"}
    await _parts(rec, _turn(anna))
    await _parts(rec, _turn(kenta))
    assert len(list(tmp_path.rglob("*.json"))) == 2
    replay = ReplayLlm(model="gemini-3.8-flash", agent="supervisor", replay_dir=tmp_path, strict=True)
    again = {**anna, "task": "task_ba9876543210", "at": "2026-10-14T09:00:05+02:00"}   # new ids, a later clock
    assert await _parts(replay, _turn(again)) == [{"text": '{"greeting": "Good morning, Anna."}'}]
    assert await _parts(replay, _turn(kenta)) == [{"text": '{"greeting": "Good morning, Kenta."}'}]


async def test_a_cut_off_recording_resumes_where_it_stopped(tmp_path: Path) -> None:
    """Run again with resume on, a recording answers what it already recorded from its cassettes (the same key, step
    and request), and only the rest goes to the live model; a request that changed goes live again."""
    a = {"key": "research/fr/q1", "task": "task_0123456789ab"}
    first = RecordingLlm(model="gemini-3.8-flash", agent="researcher_primary", record_dir=tmp_path,
                         inner=Answers(script=[[("fetch_page", {"url": "https://www.gov.uk/x"})]]))
    before = await _parts(first, _turn(a))                                          # the run is cut off after step 0
    live = Answers(script=['{"answer": "two steps"}', '{"answer": "asked again"}'])
    again = RecordingLlm(model="gemini-3.8-flash", agent="researcher_primary", record_dir=tmp_path, inner=live,
                         resume=True)
    replayed = [r async for r in again.generate_content_async(_turn({**a, "task": "task_ba9876543210"}))]
    assert [p.function_call.name for p in replayed[0].content.parts] == ["fetch_page"] == [x["call"] for x in before]
    assert replayed[0].custom_metadata == {"replayed": True} and replayed[0].usage_metadata is None
    assert replayed[0].content.parts[0].thought_signature == b"sig:fetch_page"     # sent back with the next step
    assert len(live.script) == 2                                                    # step 0 never went live
    assert await _parts(again, _turn(a, [("fetch_page", {"text": "Article 1"})])) == [{"text": '{"answer": "two steps"}'}]
    other_page = await _parts(RecordingLlm(model="gemini-3.8-flash", agent="researcher_primary", record_dir=tmp_path,
                                           inner=Answers(script=['{"answer": "read the new page"}']), resume=True),
                              _turn(a, [("fetch_page", {"text": "Article 1, amended"})]))
    assert other_page == [{"text": '{"answer": "read the new page"}'}]             # the tool said otherwise: live
    changed = {**a, "question": "a different question"}                            # same key, another request
    assert await _parts(again, _turn(changed)) == [{"text": '{"answer": "asked again"}'}]
    assert not live.script and not list(tmp_path.rglob("*.part"))


async def test_a_resumed_run_asks_again_when_the_instructions_changed(tmp_path: Path) -> None:
    """A prompt edited between two pieces of a run: the recorded answer was given to other instructions, so the
    resumed run asks the live model again rather than play it."""
    def told(text: str) -> LlmRequest:
        req = _turn({"key": "explain/x"})
        req.config = types.GenerateContentConfig(system_instruction=text)
        return req
    await _parts(RecordingLlm(model="gemini-3.8-flash", agent="explainer", record_dir=tmp_path,
                              inner=Answers(script=['{"answer": "old"}'])), told("Explain it."))
    live = Answers(script=['{"answer": "new"}'])
    again = RecordingLlm(model="gemini-3.8-flash", agent="explainer", record_dir=tmp_path, inner=live, resume=True)
    assert await _parts(again, told("Explain it.")) == [{"text": '{"answer": "old"}'}]           # the same: played
    assert await _parts(again, told("Explain it in one sentence.")) == [{"text": '{"answer": "new"}'}]
    assert not live.script


async def test_an_unsigned_recorded_tool_call_is_made_again_when_resuming(tmp_path: Path) -> None:
    """A cassette from before signatures were kept has an unsigned tool call; Gemini 3 would refuse the next step
    with it, so a resumed run asks the live model again instead."""
    path = tmp_path / "investigator" / "find_x__0.json"
    path.parent.mkdir(parents=True)
    pack = {"key": "find/x"}
    from afterledger.adapters.llm.models import _digest
    path.write_text(json.dumps({"agent": "investigator", "key": "find/x", "step": 0, "digest": _digest(pack),
                                "parts": [{"function_call": {"name": "search_knowledge", "args": {"q": "x"}}}]}),
                    encoding="utf-8")
    live = Answers(script=[[("search_knowledge", {"q": "x"})]])
    rec = RecordingLlm(model="gemini-3.8-flash", agent="investigator", record_dir=tmp_path, inner=live, resume=True)
    out = [r async for r in rec.generate_content_async(_turn(pack))]
    assert not live.script and out[0].custom_metadata is None                     # asked live, and recorded signed
    assert json.loads(path.read_text(encoding="utf-8"))["parts"][0]["thought_signature"]


async def test_a_missing_cassette_falls_back_and_is_counted(tmp_path: Path) -> None:
    missing: list[tuple[str, str]] = []
    replay = ReplayLlm(model="gemini-3.8-flash", agent="explainer", replay_dir=tmp_path,
                       fallback=Answers(script=['{"answer": "scripted"}']),
                       on_fallback=lambda agent, key: missing.append((agent, key)))
    assert await _parts(replay, _turn({"key": "q/1"})) == [{"text": '{"answer": "scripted"}'}]
    assert missing == [("explainer", "q/1")]


def test_the_provider_builds_a_pure_replay_when_asked(tmp_path: Path) -> None:
    from afterledger.adapters.llm.harness import MeteredLlm
    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps
    deps = build_deps(offline_settings(tmp_path, llm="replay", llm_replay_dir=tmp_path, llm_replay_strict=True))
    m = deps.models.model_for(AGENTS["explainer"])
    assert isinstance(m, MeteredLlm) and isinstance(m.inner, ReplayLlm) and m.inner.strict


# ---- a whole family, recorded and replayed ------------------------------------------------------------------------

_ID = re.compile(r"\b[a-z][a-z0-9]{0,7}_[0-9a-f]{12}\b")
_MOMENT = re.compile(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b")
_VARYING = {"ms", "seconds", "duration_ms", "recorded_at",     # timings differ from run to run
            "model_mode"}                                       # the case's label: gemini, then replay


def _norm(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items() if k not in _VARYING}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    if isinstance(value, str):
        return _MOMENT.sub("<t>", _ID.sub("<id>", value))
    return value


def _collections(state: Path, case_id: str) -> dict[str, list[str]]:
    """Every collection of the case, each record without its random ids and timings, in a stable order."""
    out = {}
    for f in sorted((state / "cases" / case_id).glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        records = list(data.values()) if f.stem != "case" else [data]
        out[f.stem] = sorted(json.dumps(_norm(r), ensure_ascii=False, sort_keys=True) for r in records)
    return out


@pytest.mark.slow
def test_a_recorded_family_replays_to_the_same_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tanaka (the smallest family) is played with a recording model, then replayed with no scripted fallback
    allowed: every collection of the case comes out the same."""
    from afterledger.adapters.llm.models import ScriptedLlm
    from afterledger.agents.factory import ModelProvider
    from afterledger.runtime import build_deps
    from afterledger.seed.seeder import demo_case_id, seed_family

    # the "live" model of the recorded run is the scripted one, so the run is offline and its answers are known
    monkeypatch.setattr(ModelProvider, "_live",
                        lambda self, model_id, spec, **_: ScriptedLlm(agent=spec.name, oracle=self._oracle(spec.name)))
    cassettes, recorded, replayed = tmp_path / "cassettes", tmp_path / "recorded", tmp_path / "replayed"

    async def play(state: Path, **settings: Any) -> Any:
        deps = build_deps(offline_settings(state, **settings))
        report = await seed_family(deps, "tanaka")
        await deps.tasks.drain()
        return report

    first = asyncio.run(play(recorded, llm="gemini", llm_record_dir=cassettes))
    assert not first.warnings, first.warnings
    assert len(list(cassettes.rglob("*.json"))) > 20
    second = asyncio.run(play(replayed, llm="replay", llm_replay_dir=cassettes, llm_replay_strict=True))
    assert not second.warnings, second.warnings          # no call went without a cassette
    case_id = demo_case_id("tanaka")
    modes = [json.loads((s / "cases" / case_id / "case.json").read_text(encoding="utf-8"))["model_mode"]
             for s in (recorded, replayed)]
    assert modes == ["gemini", "replay"]                 # each case says which kind of model made it
    a, b = _collections(recorded, case_id), _collections(replayed, case_id)
    assert sorted(a) == sorted(b)
    for name in a:
        assert len(a[name]) == len(b[name]), name
        for x, y in zip(a[name], b[name], strict=True):
            assert x == y, (name, _first_difference(json.loads(x), json.loads(y)))


def _first_difference(x: Any, y: Any, path: str = "") -> str:
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y)):
            if x.get(k) != y.get(k):
                return _first_difference(x.get(k), y.get(k), f"{path}.{k}")
    if isinstance(x, list) and isinstance(y, list) and len(x) == len(y):
        for i, (u, v) in enumerate(zip(x, y, strict=True)):
            if u != v:
                return _first_difference(u, v, f"{path}[{i}]")
    return f"{path}: {str(x)[:200]!r} != {str(y)[:200]!r}"


async def test_a_resumed_run_sees_the_same_search_results_and_pages(tmp_path: Path) -> None:
    """Google Search answers differently from one minute to the next; a run made in pieces keeps what the first
    piece saw, so the model calls after a search are the same calls again."""
    from datetime import UTC, datetime

    from afterledger.adapters.recorded import RecordedPages, RecordedSearch
    from afterledger.ports import PageSnapshot, SearchHit

    class Web:
        def __init__(self) -> None:
            self.n = 0
            self.snapshots = self

        async def search(self, query: str, **kw: Any) -> list[SearchHit]:
            self.n += 1
            return [SearchHit(url=f"https://www.service-public.fr/{self.n}", title="Assurance-vie", snippet="", tier=1)]

        async def fetch(self, url: str) -> None:            # no stored snapshot for it
            return None

        async def fetch_live(self, url: str) -> PageSnapshot:
            self.n += 1
            return PageSnapshot(url=url, title="Assurance-vie", text=f"version {self.n}", fetched_at=datetime.now(UTC))

    web = Web()
    first = await RecordedSearch(web, tmp_path).search("capital décès", lang="fr")
    again = await RecordedSearch(web, tmp_path).search("capital décès", lang="fr")   # the next piece
    assert [h.url for h in again] == [h.url for h in first] and web.n == 1
    page = await RecordedPages(web, tmp_path).fetch_live("https://www.service-public.fr/1")
    assert (await RecordedPages(web, tmp_path).fetch("https://www.service-public.fr/1")).text == page.text
    assert web.n == 2
