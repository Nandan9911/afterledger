"""Model adapters for ADK agents.

- ScriptedLlm answers each agent from an offline oracle (afterledger.oracles). Oracles derive
  their answers from the demo families' truth files and the knowledge fixtures, with deliberate
  noise where a demo needs two readers to disagree. This is how the whole flow runs offline.
- RecordingLlm wraps a live Gemini model and writes every call as a cassette.
- ReplayLlm plays cassettes back, so a live run can be repeated without calling Gemini.

Every agent input is a JSON context pack whose "key" names the request semantically
(e.g. "reader_a/verma/narmada_passbook/p2"), so scripted, recorded and replayed responses line up.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict, PrivateAttr

from ...domain.ids import sha256_text

log = logging.getLogger(__name__)


@dataclass
class ToolCalls:
    """An oracle's way of asking ADK to run tools before it answers."""
    calls: list[tuple[str, dict]]


@dataclass
class OracleRequest:
    agent: str
    payload: Any                     # the parsed context pack (dict) or raw text
    key: str | None
    system: str
    tools: list[str]
    tool_results: list[dict] = field(default_factory=list)   # [{"name":..., "response":...}]
    history: list[dict] = field(default_factory=list)        # earlier turns for chat agents
    schema: Any = None


Oracle = Callable[[OracleRequest], Any]


def _text_of(content: types.Content | None) -> str:
    if not content or not content.parts:
        return ""
    return "".join(p.text or "" for p in content.parts if getattr(p, "text", None))


def _parse_payload(text: str) -> Any:
    t = text.strip()
    if t.startswith("{") or t.startswith("["):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            return t
    return t


def _request_parts(req: LlmRequest) -> tuple[Any, list[dict], list[dict], str]:
    """Return (payload of the turn's input, tool results since, earlier history, system text)."""
    contents = req.contents or []
    # The turn starts at the last user message that carries text (not a function response).
    start = 0
    for i in range(len(contents) - 1, -1, -1):
        c = contents[i]
        if c.role == "user" and any(getattr(p, "text", None) for p in (c.parts or [])):
            start = i
            break
    payload = _parse_payload(_text_of(contents[start])) if contents else ""
    results = []
    for c in contents[start + 1:]:
        for p in c.parts or []:
            if getattr(p, "function_response", None):
                results.append({"name": p.function_response.name, "response": p.function_response.response})
    history = [{"role": c.role, "text": _text_of(c)} for c in contents[:start] if _text_of(c)]
    system = ""
    if req.config and req.config.system_instruction:
        si = req.config.system_instruction
        system = si if isinstance(si, str) else _text_of(si) if isinstance(si, types.Content) else str(si)
    return payload, results, history, system


def _key_of(payload: Any, fallback_text: str) -> str:
    if isinstance(payload, dict) and payload.get("key"):
        return str(payload["key"])
    return "h_" + sha256_text(fallback_text)[:16]


def _response_from(result: Any) -> LlmResponse:
    if isinstance(result, ToolCalls):
        parts = [types.Part(function_call=types.FunctionCall(name=n, args=a)) for n, a in result.calls]
    else:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        parts = [types.Part(text=text)]
    return LlmResponse(content=types.Content(role="model", parts=parts),
                       usage_metadata=types.GenerateContentResponseUsageMetadata(
                           prompt_token_count=0, candidates_token_count=0, total_token_count=0))


class ScriptedLlm(BaseLlm):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "scripted"
    agent: str = ""
    oracle: Any = None
    _calls: int = PrivateAttr(default=0)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        payload, results, history, system = _request_parts(llm_request)
        tools = []
        if llm_request.config and llm_request.config.tools:
            for t in llm_request.config.tools:
                for fd in getattr(t, "function_declarations", None) or []:
                    tools.append(fd.name)
        key = _key_of(payload, json.dumps(payload, ensure_ascii=False, default=str))
        req = OracleRequest(agent=self.agent, payload=payload, key=key, system=system, tools=tools,
                            tool_results=results, history=history,
                            schema=llm_request.config.response_schema if llm_request.config else None)
        if self.oracle is None:
            raise RuntimeError(f"No offline oracle for agent '{self.agent}'. Run in live mode or add one.")
        self._calls += 1
        result = self.oracle(req)
        if hasattr(result, "__await__"):
            result = await result
        yield _response_from(result)


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:150]


_ID = re.compile(r"\b[a-z][a-z0-9]{0,7}_[0-9a-f]{12}\b")
_MOMENT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")


def _digest(payload: Any) -> str:
    """The request's fingerprint, with record ids and clock times set aside (they differ between a recorded run and
    its replay). Two calls with the same key but different requests are told apart by it."""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_text(_MOMENT.sub("<t>", _ID.sub("<id>", text)))[:12]


def _cassette(root: Path, agent: str, key: str, step: int, digest: str | None = None) -> Path:
    tail = f"~{digest}" if digest else ""
    return root / _safe(agent) / f"{_safe(key)}{tail}__{step}.json"


def _response_of(data: dict) -> LlmResponse:
    """A recorded turn played back: its text and tool calls (a replay used no tokens, so it reports none)."""
    parts = []
    for p in data["parts"]:
        # Gemini 3 checks the signature of every function call sent back to it: a resumed run's next live step
        # carries the recorded call, so its signature is kept with it
        sig = base64.b64decode(p["thought_signature"]) if p.get("thought_signature") else None
        if "function_call" in p:
            parts.append(types.Part(function_call=types.FunctionCall(**p["function_call"]), thought_signature=sig))
        else:
            parts.append(types.Part(text=p["text"], thought_signature=sig))
    return LlmResponse(content=types.Content(role="model", parts=parts), custom_metadata={"replayed": True})


def _resumable(data: dict) -> bool:
    """A recorded turn can stand in for a live one in a resumed run: a tool call must carry its signature, because
    the live model's next turn is sent the call back and refuses one without it (cassettes from before 15 October
    have none; those calls are made again)."""
    return all(p.get("thought_signature") for p in data.get("parts", []) if "function_call" in p)


def _turn_digest(payload: Any, results: list[dict]) -> str:
    """The fingerprint of the whole turn: the context pack and every tool result since. A replay of a live run gets
    other tool results (offline pages), so it matches on the pack alone; a resumed run must see the same results, or
    the recorded answer would be to a different conversation."""
    return _digest({"pack": payload, "tools": results})


def _setup_digest(req: LlmRequest, system: str) -> str:
    """The fingerprint of what the agent was told around the pack: its instructions (with any re-ask), its answer's
    shape and its tools. A resumed run made after a prompt changed must ask again, not play the old answer."""
    cfg = req.config
    schema: Any = (getattr(cfg, "response_schema", None) or getattr(cfg, "response_json_schema", None)) if cfg else None
    if isinstance(schema, type) and hasattr(schema, "model_json_schema"):
        schema = schema.model_json_schema()
    elif hasattr(schema, "model_dump"):
        schema = schema.model_dump(mode="json", exclude_none=True)
    tools = sorted(fd.name for t in ((cfg.tools if cfg else None) or [])
                   for fd in (getattr(t, "function_declarations", None) or []))
    return _digest({"system": system, "schema": schema, "tools": tools})


def _recorded(root: Path, agent: str, key: str, step: int, digest: str, turn: str,
              setup: str | None = None) -> dict | None:
    """The cassette recorded for exactly this request (same key, step and fingerprint, the same instructions, and
    after a tool call the same tool results), if there is one."""
    for path in (_cassette(root, agent, key, step, digest), _cassette(root, agent, key, step)):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        same_turn = step == 0 or data.get("turn_digest") == turn
        same_setup = setup is None or data.get("setup_digest") == setup
        if data.get("digest") == digest and same_turn and same_setup and _resumable(data):
            return data
    return None


class RecordingLlm(BaseLlm):
    """Wraps a live model and records each completed turn as a cassette file. With resume on, a request recorded
    before (the same key, step and fingerprint) is answered from its cassette, so a run that was cut off part way can
    be run again and only the calls it never finished go to the live model."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "recording"
    agent: str = ""
    inner: Any = None
    record_dir: Path | None = None
    resume: bool = False
    route: str = ""                  # the model and endpoint it calls ("gemini-3.8-flash@us"), kept in each cassette

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        payload, results, _, system = _request_parts(llm_request)
        key = _key_of(payload, json.dumps(payload, ensure_ascii=False, default=str))
        step = len(results)
        digest = _digest(payload)
        turn = _turn_digest(payload, results)
        setup = _setup_digest(llm_request, system)
        if self.resume and self.record_dir is not None:
            done = _recorded(self.record_dir, self.agent, key, step, digest, turn, setup)
            if done is not None:
                yield _response_of(done)
                return
        final: LlmResponse | None = None
        async for resp in self.inner.generate_content_async(llm_request, stream=stream):
            if not resp.partial:
                final = resp
            yield resp
        if final is not None and self.record_dir is not None:
            path = _cassette(self.record_dir, self.agent, key, step)
            if path.exists() and _stored_digest(path) not in (None, digest):
                # the same key for a different request: kept beside the first, and found again by its fingerprint
                path = _cassette(self.record_dir, self.agent, key, step, digest)
            path.parent.mkdir(parents=True, exist_ok=True)
            parts = []
            for p in (final.content.parts if final.content else []) or []:
                if getattr(p, "function_call", None):
                    item: dict[str, Any] = {"function_call": {"name": p.function_call.name, "args": p.function_call.args}}
                elif getattr(p, "text", None) and not getattr(p, "thought", False):
                    item = {"text": p.text}
                else:
                    continue
                if getattr(p, "thought_signature", None):
                    item["thought_signature"] = base64.b64encode(p.thought_signature).decode("ascii")
                parts.append(item)
            um = final.usage_metadata
            tmp = path.with_name(path.name + ".part")        # written whole or not at all, even if the run is cut off
            tmp.write_text(json.dumps({
                "agent": self.agent, "key": key, "step": step, "digest": digest, "turn_digest": turn,
                "setup_digest": setup,
                "model": getattr(self.inner, "model", ""),
                "route": self.route or getattr(self.inner, "model", ""),
                "recorded_at": datetime.now(UTC).isoformat(), "parts": parts,
                "grounding": final.grounding_metadata.model_dump(mode="json") if final.grounding_metadata else None,
                # what the call used when it was recorded (a replay itself uses nothing, and reports nothing)
                "usage": {"tokens_in": um.prompt_token_count, "tokens_out": um.candidates_token_count,
                          "tokens_thinking": um.thoughts_token_count} if um else None,
            }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            tmp.replace(path)


def _stored_digest(path: Path) -> str | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("digest")
    except (OSError, ValueError):
        return None


class ReplayLlm(BaseLlm):
    """Plays back recorded cassettes; falls back to the offline oracle when none matches."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = "replay"
    agent: str = ""
    replay_dir: Path | None = None
    fallback: Any = None
    on_fallback: Any = None          # told each time a call has no cassette (the case is then not a pure replay)
    strict: bool = False             # a missing cassette is an error, not a scripted answer (tests of a pure replay)

    @property
    def capabilities(self) -> LlmCapabilities:
        return LlmCapabilities(output_schema_and_tools=True)

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        payload, results, _, _ = _request_parts(llm_request)
        key = _key_of(payload, json.dumps(payload, ensure_ascii=False, default=str))
        root = self.replay_dir or Path(".")
        path = _cassette(root, self.agent, key, len(results), _digest(payload))
        if not path.exists():
            path = _cassette(root, self.agent, key, len(results))
        if path.exists():
            yield _response_of(json.loads(path.read_text(encoding="utf-8")))
            return
        if self.fallback is None or self.strict:
            raise FileNotFoundError(f"No cassette for {self.agent}/{key}" + ("" if self.fallback else " and no fallback"))
        log.warning("no cassette for %s/%s, using the offline oracle", self.agent, key)
        if self.on_fallback is not None:
            self.on_fallback(self.agent, key)
        async for r in self.fallback.generate_content_async(llm_request, stream):
            yield r
