"""The two wrappers every model call passes through, outermost first:

- MeteredLlm checks the case's budget before the call and records tokens, time and estimated cost after it
  (agents/meter.py), wherever the call runs: a single agent, or a parallel worker inside a workflow.
- FallbackLlm tries a call's routes in order. A route is a model on an endpoint: the main one ("gemini-3.8-flash"),
  or a backup multi-region one ("gemini-3.8-flash@us"). When an endpoint is slow (past the request's deadline) or
  overloaded, the same model is tried on the next endpoint, and the slow one is passed over for a while (longer each
  time it is still slow). When every endpoint of a model is overloaded, the call moves down a tier (pro to flash,
  flash to lite), which is a different model, so it is reported to the service-health record as a failure (a strict
  live run stops there). A model may have peers that answer as well (AL_MODEL_FLASH_PEERS: gemini-3.6-flash for
  gemini-3.8-flash), tried like its endpoints before any smaller model. When every route of a model is slow, the
  call is tried again later (ModelTimeout) rather than handed to a smaller model. A call that has had no answer after hedge_after_s also goes to the model's next
  endpoint, and the first answer wins (the other request is cancelled): a slow endpoint costs seconds, not the whole
  deadline. Every move is said in the response's metadata and kept in the health record.

ADK sets each request's model name from the agent's model object, so each wrapper carries the real model id of the
model it wraps first.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.models._capabilities import LlmCapabilities
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ConfigDict

from ...agents.context import CASE_ID, PURPOSE

log = logging.getLogger(__name__)


class ModelTimeout(TimeoutError):
    """A live request ran past its deadline (AL_LLM_HTTP_TIMEOUT_S): slow, not failed, so the call is tried once more
    (a TimeoutError, which the agent node retries), and no service is marked as failing."""


def past_deadline(error: BaseException) -> bool:
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    text = str(error)
    return code in (408, 499) or "DEADLINE_EXCEEDED" in text or ("CANCELLED" in text and code in (None, 499))


def overloaded(error: BaseException) -> bool:
    """Quota exhausted or the service overloaded: worth trying another model, unlike a bad request."""
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    if code in (429, 500, 502, 503, 504):
        return True
    text = str(error)
    return any(s in text for s in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "overloaded", "Quota exceeded"))


def not_found(error: BaseException) -> bool:
    """The model is not served on that endpoint (a preview model that only runs on the global one)."""
    code = getattr(error, "code", None) or getattr(error, "status_code", None)
    return code == 404 or "NOT_FOUND" in str(error)


def model_of(route: str) -> str:
    return route.partition("@")[0]


def _kind(error: BaseException) -> str:
    if not_found(error):
        return "missing"
    if past_deadline(error):
        return "slow"
    if overloaded(error):
        return "overloaded"
    return "error"


class FallbackLlm(BaseLlm):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = ""
    agent: str = ""
    chain: list[Any] = []            # [(route, BaseLlm)]: the agent's own model on its main endpoint first
    # Routes that answer equally well, tried in turn (and hedged) before a smaller model: a model's endpoints, and
    # any model set as its peer (AL_MODEL_FLASH_PEERS). Empty: each model's endpoints are one group.
    groups: list[list[str]] = []
    health: Any = None
    cooldown_s: float = 300.0        # how long a slow endpoint is passed over at first
    hedge_after_s: float | None = None   # no answer by then: the same request also goes to the next route
    # Overloaded everywhere: wait this long and try once more before a smaller model (a 500 or 503 spell is often
    # over in seconds); 0 moves down at once.
    overload_pause_s: float = 0.0

    @property
    def capabilities(self) -> LlmCapabilities:
        return self.chain[0][1].capabilities

    def _groups(self) -> list[list[tuple[str, Any]]]:
        llms = dict(self.chain)
        if self.groups:
            return [[(r, llms[r]) for r in g if r in llms] for g in self.groups]
        by_model: dict[str, list[tuple[str, Any]]] = {}
        for r, llm in self.chain:
            by_model.setdefault(model_of(r), []).append((r, llm))
        return list(by_model.values())

    def _ordered(self, routes: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
        """A group's routes in the order to try them: those not being passed over first."""
        if self.health is None:
            return routes
        routes = [x for x in routes if not self.health.route_missing(x[0])] or routes[:1]
        return sorted(routes, key=lambda x: self.health.route_avoided(x[0]))     # stable: keeps the chain's order

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        first = self.chain[0][0]
        groups = self._groups()
        for g, group in enumerate(groups):
            label = model_of(group[0][0])
            route, answers, errors = await self._ask(self._ordered(group), llm_request, stream, first)
            if answers is None and self.overload_pause_s and g < len(groups) - 1 and \
                    any(k == "overloaded" for k, _ in errors):
                log.warning("%s: %s is overloaded, trying it once more in %gs", self.agent, label, self.overload_pause_s)
                await asyncio.sleep(self.overload_pause_s)
                route, answers, more = await self._ask(self._ordered(group), llm_request, stream, first)
                errors += more
            if answers is not None:
                for r in answers:
                    if route != first:
                        r.custom_metadata = {**(r.custom_metadata or {}), "model": route, "fallback_from": first}
                    yield r
                return
            kinds = [k for k, _ in errors]
            if not errors or set(kinds) == {"missing"}:
                raise RuntimeError(f"{label} is not served on any endpoint for {self.agent}")
            error = next(e for k, e in reversed(errors) if k != "missing")
            if "overloaded" not in kinds:                   # tried again later rather than given to a smaller model
                raise ModelTimeout(f"{label} did not answer {self.agent} within its deadline: {error}") from error
            error = next(e for k, e in reversed(errors) if k == "overloaded")
            if g == len(groups) - 1:
                raise error
            nxt = model_of(groups[g + 1][0][0])
            log.warning("%s: %s is out of quota or overloaded, trying %s (%s)", self.agent, label, nxt, error)
            if self.health is not None:
                self.health.failed(f"gemini:{label}", error)    # a smaller model next: a strict live run stops here

    async def _ask(self, routes: list[tuple[str, Any]], request: LlmRequest, stream: bool, first: str
                   ) -> tuple[str, list[LlmResponse] | None, list[tuple[str, BaseException]]]:
        """One group of routes that answer equally well (a model's endpoints, then its peers): the first route,
        then the next one after an error another route may not have (slow, overloaded, not served there), or
        alongside it once hedge_after_s has passed with no answer. Returns the route that answered and its
        responses, or the errors."""
        queue = list(routes)
        pending: dict[asyncio.Task, str] = {}
        errors: list[tuple[str, BaseException]] = []
        started: dict[str, float] = {}

        def start() -> None:
            route, llm = queue.pop(0)
            started[route] = time.monotonic()
            model = model_of(route)                 # a peer model answers under its own name
            req = request if request.model == model else request.model_copy(update={"model": model})
            pending[asyncio.ensure_future(self._answers(llm, req, stream))] = route

        start()
        try:
            while pending:
                hedge = self.hedge_after_s if (queue and self.hedge_after_s) else None
                done, _ = await asyncio.wait(pending, timeout=hedge, return_when=asyncio.FIRST_COMPLETED)
                if not done:                                 # no answer yet: the same request on the next endpoint
                    log.warning("%s: no answer from %s after %gs, also asking %s", self.agent,
                                ", ".join(pending.values()), hedge, queue[0][0])
                    start()
                    continue
                for task in sorted(done, key=lambda t: t.exception() is not None):     # an answer before an error
                    route = pending.pop(task)
                    error = task.exception()
                    if error is None:
                        if self.health is not None:
                            self.health.route_ok(route)
                            for other in pending.values():  # silent for at least the hedge delay: slow
                                waited = time.monotonic() - started[other]
                                if waited >= (self.hedge_after_s or 0):
                                    self.health.route_slow(other, f"no answer after {waited:.0f}s; {route} answered "
                                                           "first", self.cooldown_s)
                        return route, task.result(), errors
                    kind = _kind(error)
                    if kind == "missing" and route != first and self.health is not None:
                        self.health.route_gone(route)        # never served there: not slow, not failing
                    elif kind in ("slow", "overloaded"):
                        if self.health is not None:
                            self.health.route_slow(route, error, self.cooldown_s)
                    else:
                        raise error
                    errors.append((kind, error))
                    if queue and not pending:
                        log.warning("%s: %s is %s, trying %s", self.agent, route, kind, queue[0][0])
                        start()
            return "", None, errors
        finally:
            for task in pending:                             # the slower request is not needed any more
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=2)

    @staticmethod
    async def _answers(llm: Any, request: LlmRequest, stream: bool) -> list[LlmResponse]:
        return [r async for r in llm.generate_content_async(request, stream)]


class MeteredLlm(BaseLlm):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    model: str = ""
    agent: str = ""
    inner: Any = None
    meter: Any = None
    http_timeout_s: float | None = None    # a live request that hangs is given up after this, and tried again

    @property
    def capabilities(self) -> LlmCapabilities:
        return self.inner.capabilities

    async def generate_content_async(self, llm_request: LlmRequest,
                                     stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        case_id, purpose = CASE_ID.get(), PURPOSE.get()
        await self.meter.check(case_id, purpose)
        if self.http_timeout_s and llm_request.config is not None:
            opts = llm_request.config.http_options or types.HttpOptions()
            if opts.timeout is None:
                opts.timeout = int(self.http_timeout_s * 1000)        # milliseconds
            llm_request.config.http_options = opts
        t0 = time.perf_counter()
        final: LlmResponse | None = None
        status = "ok"
        try:
            async for r in self.inner.generate_content_async(llm_request, stream):
                if not r.partial:
                    final = r
                yield r
        except BaseException as e:
            status = type(e).__name__
            raise
        finally:
            meta = (final.custom_metadata or {}) if final is not None else {}
            if status == "ok" and meta.get("replayed"):
                status = "replayed"              # answered from a cassette recorded earlier: no tokens were used
            await self.meter.record(agent=self.agent, model=meta.get("model") or llm_request.model or self.model,
                                    response=final, ms=(time.perf_counter() - t0) * 1000, status=status,
                                    case_id=case_id, purpose=purpose, fallback_from=meta.get("fallback_from"))
