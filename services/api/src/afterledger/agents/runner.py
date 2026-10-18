"""Running agents and module workflows on ADK.

Each module is an ADK Workflow (a graph of code nodes and agents, with parallel workers where a
step fans out over papers, payment groups or research questions). A run is one short
InMemoryRunner invocation: the case itself lives in our stores, not in the ADK session, so runs
can be resumed, retried and replayed independently.

Around every run (agents/plugins.py, adapters/llm/harness.py):
- every model call is metered, budgeted and traced, and falls back a tier when its model is out of quota;
- every tool call is checked before it runs and traced after;
- every agent node has a timeout and retries transient failures (ADK's retry_config);
- an answer that does not fit the agent's schema is sent back once with the error (AL_REASK_MALFORMED);
- an answer that fits but fails its check (a letter with a number not in the case, a form value that does not read
  back) is sent back with what failed, and checked again: repair_loop, up to AL_REPAIR_ROUNDS times.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
import warnings
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk import Workflow
from google.adk.agents.run_config import RunConfig
from google.adk.runners import InMemoryRunner
from google.adk.workflow import RetryConfig, node
from google.genai import types
from pydantic import BaseModel, ValidationError

from ..domain.ids import new_id
from .context import working_on
from .meter import BudgetExceeded
from .plugins import plugins_for
from .specs import AGENTS

log = logging.getLogger(__name__)

# ADK marks JSON-schema function declarations as experimental (on by default); it is the behaviour the agents' tools
# rely on, so the once-per-process notice is left out of the service's and the command line's output.
warnings.filterwarnings("ignore", message=r"\[EXPERIMENTAL\] feature FeatureName\.JSON_SCHEMA_FOR_FUNC_DECL",
                        category=UserWarning)

# Failures worth one more try of the same node: the network, a timeout, the service. Not a bad request.
# A dropped connection is retried too: on 15 October a token refresh lost its connection mid-run
# (google.auth's TransportError) and the step failed outright
TRANSIENT = ["TimeoutError", "NodeTimeoutError", "ServerError", "ConnectError", "ConnectTimeout", "ReadTimeout",
             "ReadError", "RemoteProtocolError", "ServiceUnavailable", "DeadlineExceeded", "TransportError",
             "ConnectionError"]
# They look at page images, or write out every row of a statement: each gets twice the time (agent and request).
# reader_a joined on 19 October 2026: its requests had 90 s, and a dense statement's rows take Gemini longer to write.
SLOW_READERS = {"reader_a", "reader_b", "reader_c", "visual_critic", "triage"}

REASK = ("Your previous answer could not be used: {problem} Answer again: one JSON object that matches your output "
         "schema exactly, every required field present, nothing outside the JSON.")


class AgentError(RuntimeError):
    pass


class Unusable(ValueError):
    """The run finished but its answer cannot be used (none, or not the schema)."""


def _to_model(name: str, value: Any) -> BaseModel:
    spec = AGENTS[name]
    if isinstance(value, spec.output):
        return value
    if isinstance(value, str):
        value = json.loads(value)
    return spec.output.model_validate(value)


def agent_node(deps: Any, agent: Any, *, tools: bool = False, **kwargs: Any) -> Any:
    """An agent as a workflow node with its timeout and transient retries (and any node options, such as
    parallel_worker)."""
    spec = AGENTS.get(agent.name)
    slow = tools or agent.name in SLOW_READERS or (spec is not None and (spec.tier == "pro" or bool(spec.tools)))
    timeout = float(deps.settings.agent_timeout_s) * (2.0 if slow else 1.0)
    return node(agent, retry_config=RetryConfig(max_attempts=2, initial_delay=2.0, exceptions=TRANSIENT),
                timeout=timeout, **kwargs)


async def run_workflow(deps: Any, wf: Workflow, start: dict | str | None = None, *, state: dict | None = None,
                       app_name: str = "afterledger", images: list[tuple[bytes, str]] | None = None,
                       case_id: str | None = None) -> dict[str, Any]:
    """Runs a workflow to completion. Returns {node path: output} for every node that produced one,
    plus '__last__' for the final output."""
    runner = InMemoryRunner(node=wf, app_name=app_name, plugins=plugins_for(deps))
    session = await runner.session_service.create_session(app_name=app_name, user_id="afterledger",
                                                          state=state or {})
    text = start if isinstance(start, str) else json.dumps(start or {}, ensure_ascii=False, default=str)
    parts = [types.Part(text=text)]
    for data, mime in images or []:
        parts.append(types.Part.from_bytes(data=data, mime_type=mime))
    outputs: dict[str, Any] = {}
    last = None
    # A node's failure, by its path, until a retry of that node answers. ADK reports the failed try as an event of its
    # own and then retries the node: on 15 October a statement's reader timed out once, its retry read the statement,
    # and the run was still failed on the first try's error, so the family's statement was never read.
    errors: dict[str, str] = {}
    # AL_MAX_LLM_CALLS: the most model calls one workflow run may make (a safety net if an agent loops on tools)
    run_config = RunConfig(max_llm_calls=deps.settings.max_llm_calls)
    with working_on(case_id):
        async for ev in runner.run_async(user_id="afterledger", session_id=session.id, run_config=run_config,
                                         new_message=types.Content(role="user", parts=parts)):
            path = ev.node_info.path if ev.node_info is not None else ""
            if getattr(ev, "error_message", None):
                errors[path] = f"{getattr(ev, 'author', '?')}: {ev.error_message}"
            if ev.output is not None and ev.node_info is not None:
                outputs[path] = ev.output
                last = ev.output
                errors.pop(path, None)
    if errors:
        raise AgentError(next(iter(errors.values())))
    outputs["__last__"] = last
    return outputs


def _problem(e: BaseException) -> str:
    if isinstance(e, ValidationError):
        errs = e.errors()[:6]
        return "; ".join(f"{'.'.join(str(p) for p in x.get('loc', ()))}: {x.get('msg')}" for x in errs) + "."
    return f"{e}"[:600]


async def call_agent(deps: Any, name: str, payload: dict, *, tools: list | None = None,
                     case_id: str | None = None, instruction_extra: str = "",
                     images: list[tuple[bytes, str]] | None = None) -> BaseModel:
    """One agent, one context pack, one structured answer; an unusable answer is sent back once."""
    tries = 1 + max(0, int(deps.settings.reask_malformed))
    problem: str | None = None
    for attempt in range(tries):
        extra = instruction_extra
        if problem:
            extra = (extra + "\n\n" if extra else "") + REASK.format(problem=problem)
        agent = deps.models.agent(name, tools=tools, instruction_extra=extra)
        wf = Workflow(name=f"call_{name}", edges=[("START", agent_node(deps, agent, tools=bool(tools)))])
        t0 = time.perf_counter()
        deps.models.calls += 1
        try:
            with working_on(case_id, "reask" if attempt else None):
                outputs = await run_workflow(deps, wf, payload, case_id=case_id,
                                             images=images if deps.models.mode != "scripted" else None)
            result = outputs.get("__last__")
            if result is None:
                raise Unusable("the agent gave no answer.")
            out = _to_model(name, result)
        except (ValidationError, json.JSONDecodeError, Unusable) as e:
            problem = _problem(e)
            log.warning("agent %s gave an unusable answer (try %d of %d): %s", name, attempt + 1, tries, problem)
            await deps.trace.record({"type": "unusable_answer", "agent": name, "key": payload.get("key"),
                                     "case_id": case_id, "try": attempt + 1, "problem": problem, "id": new_id("tr")})
            continue
        await deps.trace.record({"type": "agent", "agent": name, "key": payload.get("key"), "case_id": case_id,
                                 "ms": round((time.perf_counter() - t0) * 1000), "model": deps.models.label,
                                 "tries": attempt + 1, "id": new_id("tr")})
        return out
    raise AgentError(f"agent {name} gave no usable answer after {tries} tries: {problem}")


def as_model(name: str, value: Any) -> BaseModel:
    return _to_model(name, value)


Check = Callable[[BaseModel], "list[str] | Awaitable[list[str]]"]


async def _problems(check: Check, out: BaseModel) -> list[str]:
    found = check(out)
    if inspect.isawaitable(found):
        found = await found
    return [str(p) for p in found or []]


def _brief(problems: list[str]) -> str:
    text = "; ".join(problems[:3]) + (f"; and {len(problems) - 3} more" if len(problems) > 3 else "")
    return text if len(text) <= 300 else text[:297] + "..."


async def repair_loop(deps: Any, name: str, pack: dict, check: Check, *, case_id: str | None, subject: str,
                      what: str, module: str, tools: list | None = None,
                      images: list[tuple[bytes, str]] | None = None) -> tuple[BaseModel, list[str], int]:
    """Check, fix, check again. The agent answers; code checks the answer; an answer that fails goes back to the agent
    with what failed and its previous answer, and is checked again, up to AL_REPAIR_ROUNDS repairs (0 turns repairs
    off). Each round writes a ledger line and an Activity line; a repair counts against the case's budget, and a
    spent budget stops the repairs. Returns the last answer, what still fails (empty when it passes) and how many
    repairs it took. What still fails is for the caller to hold back for a person."""
    out = await call_agent(deps, name, pack, case_id=case_id, tools=tools, images=images)
    problems = await _problems(check, out)
    rounds = max(0, int(getattr(deps.settings, "repair_rounds", 0) or 0))
    n = 0
    while problems and n < rounds:
        n += 1
        if case_id:
            await deps.ledger(case_id, subject, "repair", "fail", method=f"{what}, checked by code (round {n})",
                              detail=_brief(problems))
            scripted = " (offline, the first draft's slip is scripted, to show the check at work)" \
                if deps.models.mode == "scripted" and n == 1 else ""
            await deps.activity(case_id, module, f"{what[0].upper() + what[1:]} did not pass its check{scripted}: "
                                f"{_brief(problems)}. Sent back to be fixed{' again' if n > 1 else ''}.",
                                agent=name, kind="check")
        repair = {**pack, "key": f"{pack.get('key', name)}/repair{n}", "previous_answer": out.model_dump(mode="json"),
                  "problems": problems,
                  "repair": "Your previous answer failed the checks listed in problems. Fix exactly those, keep "
                            "everything else, and answer again in the same schema."}
        try:
            with working_on(case_id, "repair"):
                out = await call_agent(deps, name, repair, case_id=case_id, tools=tools, images=images)
        except BudgetExceeded:
            if case_id:
                await deps.activity(case_id, module, f"The case reached its budget for model work, so {what} was not "
                                    "fixed again. A person checks it before it can be approved.", kind="warning")
            break
        problems = await _problems(check, out)
    if n and case_id:
        await deps.ledger(case_id, subject, "repair", "pass" if not problems else "fail",
                          method=f"{what}, checked again after {n} repair{'s' if n != 1 else ''}",
                          detail=_brief(problems) if problems else "passes its check")
        if not problems:
            await deps.activity(case_id, module, f"Fixed, and checked again: {what} now passes.", agent=name,
                                kind="check")
    return out, problems, n
