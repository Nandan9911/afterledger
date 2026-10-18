"""ADK plugins registered on every runner, so they apply to every agent in every workflow:

- GuardPlugin checks a tool call before it runs: fetch_page reads only public http(s) pages (netsafe.py), and an
  agent that has made AL_MAX_TOOL_CALLS calls in one turn is told to answer with what it has. A refusal goes back to
  the model as the tool's answer, and into the trace. Past the limit the model is also asked without its tools, so it
  can only answer: on 15 October Gemini 3.8 Flash called a refused search more than a hundred times in one turn.
- ToolTracePlugin writes one trace line per tool call: which agent, which tool, how long, and whether it failed.
- ADK's own ReflectAndRetryToolPlugin lets a model correct a tool call that raised an error, once.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin
from google.adk.plugins.reflect_retry_tool_plugin import ReflectAndRetryToolPlugin
from google.genai import types

from ..domain.ids import new_id
from ..netsafe import allowed
from .context import CASE_ID

log = logging.getLogger(__name__)

_URL_ARGS = {"fetch_page": "url"}


class GuardPlugin(BasePlugin):
    def __init__(self, deps: Any):
        super().__init__(name="afterledger_guard")
        self.deps = deps
        self.calls: dict[tuple[str, str, str], int] = {}

    async def before_model_callback(self, *, callback_context: Any, llm_request: Any) -> Any:
        limit = int(getattr(self.deps.settings, "max_tool_calls", 0) or 0)
        cfg = getattr(llm_request, "config", None)
        if limit and cfg is not None and cfg.tools and self.calls.get(_turn(callback_context), 0) >= limit:
            # its tools spent, the agent is asked once more without them: it can only answer now
            cfg.tool_config = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.NONE))
        return None

    async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any) -> dict | None:
        limit = int(getattr(self.deps.settings, "max_tool_calls", 0) or 0)
        turn = _turn(tool_context)
        self.calls[turn] = self.calls.get(turn, 0) + 1
        if limit and self.calls[turn] > limit:
            await _trace(self.deps, {"type": "tool_refused", "tool": tool.name, "agent": turn[2],
                                     "reason": f"more than {limit} tool calls in one turn"})
            return {"error": f"Tool limit reached ({limit} calls). Do not call any more tools: answer now with what "
                             "you have, and put what is still missing in gaps or notes."}
        arg = _URL_ARGS.get(tool.name)
        if arg and isinstance(tool_args.get(arg), str):
            ok, why = allowed(tool_args[arg])
            if not ok:
                await _trace(self.deps, {"type": "tool_refused", "tool": tool.name, "agent": _agent(tool_context),
                                         "reason": why, "url": tool_args[arg][:300]})
                return {"url": tool_args[arg], "error": f"Not read: {why}. Only public official pages can be read."}
        return None


class ToolTracePlugin(BasePlugin):
    def __init__(self, deps: Any):
        super().__init__(name="afterledger_tool_trace")
        self.deps = deps
        self._started: dict[str, float] = {}

    async def before_tool_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any) -> dict | None:
        self._started[_call_id(tool_context)] = time.perf_counter()
        return None

    async def after_tool_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any,
                                  result: dict[str, Any]) -> dict | None:
        t0 = self._started.pop(_call_id(tool_context), None)
        failed = isinstance(result, dict) and bool(result.get("error"))
        await _trace(self.deps, {"type": "tool_call", "tool": tool.name, "agent": _agent(tool_context),
                                 "ms": round((time.perf_counter() - t0) * 1000) if t0 else None,
                                 "status": "error" if failed else "ok", "args": _short(tool_args)})
        return None

    async def on_tool_error_callback(self, *, tool: Any, tool_args: dict[str, Any], tool_context: Any,
                                     error: Exception) -> dict | None:
        self._started.pop(_call_id(tool_context), None)
        await _trace(self.deps, {"type": "tool_call", "tool": tool.name, "agent": _agent(tool_context),
                                 "status": f"raised {type(error).__name__}", "args": _short(tool_args)})
        return None


def plugins_for(deps: Any) -> list[BasePlugin]:
    """Fresh plugins per run: ADK's retry plugin keeps per-run counts."""
    return [GuardPlugin(deps), ToolTracePlugin(deps),
            ReflectAndRetryToolPlugin(max_retries=1, throw_exception_if_retry_exceeded=False)]


def _agent(tool_context: Any) -> str:
    return str(getattr(tool_context, "agent_name", "") or "")


def _turn(ctx: Any) -> tuple[str, str, str]:
    """One agent's turn: its invocation, and its node in the workflow (each parallel worker has its own)."""
    return (str(getattr(ctx, "invocation_id", "") or ""), str(getattr(ctx, "_node_path", "") or ""), _agent(ctx))


def _call_id(tool_context: Any) -> str:
    return str(getattr(tool_context, "function_call_id", None) or id(tool_context))


def _short(args: dict[str, Any]) -> dict[str, str]:
    return {k: str(v)[:200] for k, v in (args or {}).items()}


async def _trace(deps: Any, event: dict[str, Any]) -> None:
    try:
        await deps.trace.record({**event, "id": new_id("tc"), "case_id": CASE_ID.get()})
    except Exception:
        log.exception("could not write a tool trace")
