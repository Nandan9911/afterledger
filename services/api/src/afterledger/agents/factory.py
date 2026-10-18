"""Builds each agent's model for the configured mode.

- scripted: ScriptedLlm answering from the offline oracle for that agent (the default; no network).
- gemini:   the live Gemini model for the agent's tier, with the next tiers to fall back to (FallbackLlm),
            optionally recording cassettes.
- replay:   cassettes recorded from a live run, falling back to the oracle where none exists.

Whatever the mode, the outermost model is MeteredLlm, so every call is budgeted, timed and traced the same way.
"""
from __future__ import annotations

import logging
from typing import Any

from google.adk import Agent
from google.genai import types

from ..adapters.llm.harness import FallbackLlm, MeteredLlm
from ..adapters.llm.models import RecordingLlm, ReplayLlm, ScriptedLlm
from ..health import health_of
from .specs import AGENTS, AgentSpec

log = logging.getLogger(__name__)

_THINKING = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}


# Modules whose answers a person waits for as they type or speak: their slow calls are hedged sooner
INTERACTIVE = {"intake", "conversation"}


def _slow(spec: AgentSpec) -> bool:
    """Agents whose calls take longest: pro, tool-using and image-reading ones (given twice the time)."""
    from .runner import SLOW_READERS
    return spec.tier == "pro" or bool(spec.tools) or spec.name in SLOW_READERS


class ModelProvider:
    def __init__(self, deps: Any):
        self.deps = deps
        self.settings = deps.settings
        self.calls = 0
        self.fallbacks = 0          # replay calls that had no cassette and were answered by the offline oracle

    @property
    def mode(self) -> str:
        return self.settings.llm

    @property
    def label(self) -> str:
        """Shown honestly in the app: which kind of model produced what the family sees."""
        return {"scripted": "scripted", "gemini": "gemini", "replay": "replay"}[self.mode]

    def _oracle(self, name: str):
        from ..oracles import oracle_for
        return oracle_for(name, self.deps)

    def _live(self, model_id: str, spec: AgentSpec, location: str | None = None):
        """The live model for one agent's tier, on the main endpoint or another one (the spec is for tests that stand
        a scripted model in its place)."""
        from google.adk.models import Gemini
        return Gemini(model=model_id, retry_options=types.HttpRetryOptions(attempts=3, initial_delay=1.0),
                      client_kwargs={"location": location} if location else None)

    def _base(self, spec: AgentSpec):
        if self.mode == "scripted":
            return ScriptedLlm(agent=spec.name, oracle=self._oracle(spec.name))
        if self.mode == "replay":
            fallback = ScriptedLlm(agent=spec.name, oracle=self._oracle(spec.name))
            return ReplayLlm(model=self.settings.model_for(spec.tier), agent=spec.name,
                             replay_dir=self.settings.llm_replay_dir, fallback=fallback, on_fallback=self._fell_back,
                             strict=self.settings.llm_replay_strict)
        chain = []
        groups = self.settings.route_groups(spec.tier)
        for route in [r for g in groups for r in g]:
            model_id, _, location = route.partition("@")
            live = self._live(model_id, spec, location=location or None)
            if self.settings.llm_record_dir:
                live = RecordingLlm(model=model_id, agent=spec.name, inner=live, route=route,
                                    record_dir=self.settings.llm_record_dir, resume=self.settings.llm_record_resume)
            chain.append((route, live))
        # one route or several, the same wrapper: a slow or overloaded endpoint gives way to the same model on the
        # next one, a passed deadline everywhere is a retry, and an overload everywhere moves down a tier
        hedge = float(self.settings.llm_hedge_after_s or 0) * (2 if _slow(spec) else 1)
        quick = float(self.settings.llm_hedge_interactive_s or 0)
        if hedge and quick and spec.module in INTERACTIVE:
            hedge = min(hedge, quick)
        return FallbackLlm(model=chain[0][0], agent=spec.name, chain=chain, groups=groups, health=health_of(self.deps),
                           cooldown_s=self.settings.llm_slow_cooldown_s, hedge_after_s=hedge or None,
                           overload_pause_s=float(self.settings.llm_overload_pause_s or 0))

    def model_for(self, spec: AgentSpec):
        base = self._base(spec)
        timeout = None
        if self.mode == "gemini":             # a live request is never left hanging: one stuck connection is retried
            timeout = float(self.settings.llm_http_timeout_s) * (2 if _slow(spec) else 1)
        return MeteredLlm(model=base.model, agent=spec.name, inner=base, meter=self.deps.meter, http_timeout_s=timeout)

    def _fell_back(self, agent: str, key: str) -> None:
        self.fallbacks += 1

    def config_for(self, spec: AgentSpec) -> types.GenerateContentConfig | None:
        """Gemini 3 models are tuned for their default temperature (lowering it can make them loop), so only
        the thinking level is set: low for quick structured work, high where an agent has to reason."""
        if self.mode == "scripted" or not spec.thinking:
            return None
        return types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=_THINKING[spec.thinking]))

    def agent(self, name: str, *, tools: list | None = None, instruction_extra: str = "") -> Agent:
        spec = AGENTS[name]
        kwargs: dict[str, Any] = dict(
            name=spec.name, model=self.model_for(spec), description=spec.description,
            instruction=spec.instruction + (("\n\n" + instruction_extra) if instruction_extra else ""),
            output_schema=spec.output, include_contents="none")
        cfg = self.config_for(spec)
        if cfg is not None:
            kwargs["generate_content_config"] = cfg
        if tools:
            kwargs["tools"] = tools
        return Agent(**kwargs)
