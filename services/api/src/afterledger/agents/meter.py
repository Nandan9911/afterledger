"""Usage and cost of every model call, per case and per agent, and the per-case budget.

Every model call goes through MeteredLlm (adapters/llm/harness.py), which calls Meter.record afterwards: one trace
line per call wherever the call ran (a single agent or a workflow's parallel worker), with tokens, milliseconds and
an estimated cost from data/knowledge/prices.yaml. Deps.activity attaches the usage since the case's previous
activity line, so the Activity screen shows what each step cost and the case's total survives a restart.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import yaml

from ..domain.ids import new_id
from .context import OPTIONAL_PURPOSES

log = logging.getLogger(__name__)


class BudgetExceeded(RuntimeError):
    def __init__(self, case_id: str, spent: float, budget: float):
        super().__init__(f"case {case_id} reached its budget: ${spent:.2f} of ${budget:.2f}")
        self.case_id, self.spent, self.budget = case_id, spent, budget


@dataclass
class Usage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    ms: int = 0

    def add(self, other: Usage) -> None:
        for k, v in asdict(other).items():
            setattr(self, k, getattr(self, k) + v)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cost_usd"] = round(self.cost_usd, 6)
        return d


class Prices:
    def __init__(self, table: dict[str, Any]):
        self.as_of = str(table.get("as_of") or "")
        self.models: dict[str, dict[str, float]] = table.get("models") or {}
        self.non_global_markup = float(table.get("non_global_markup") or 1.0)

    @classmethod
    def load(cls, knowledge_dir: Any) -> Prices:
        f = knowledge_dir / "prices.yaml"
        return cls(yaml.safe_load(f.read_text(encoding="utf-8")) or {} if f.exists() else {})

    def cost(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """A model on another endpoint than the global one ("gemini-3.8-flash@us") costs its price plus the markup."""
        name, _, location = model.partition("@")
        p = self.models.get(name)
        if p is None:
            return 0.0
        long = bool(p.get("long_from")) and tokens_in > p["long_from"]
        rate_in = p.get("long_input", p["input"]) if long else p["input"]
        rate_out = p.get("long_output", p["output"]) if long else p["output"]
        markup = self.non_global_markup if location and location != "global" else 1.0
        return (tokens_in * rate_in + tokens_out * rate_out) * markup / 1_000_000


def usage_from_activity(items: list[Any]) -> Usage:
    """A case's model use so far: the sum of the usage its activity lines carry."""
    total = Usage()
    for a in items:
        d = (getattr(a, "detail", None) or {}).get("usage")
        if isinstance(d, dict):
            total.add(Usage(**{k: d.get(k) or 0 for k in asdict(Usage())}))
    return total


def usage_of(response: Any) -> Usage:
    """Tokens from a model response's usage metadata (scripted and replayed answers report none)."""
    um = getattr(response, "usage_metadata", None)
    if um is None:
        return Usage(calls=1)
    n = lambda v: int(v or 0)  # noqa: E731
    return Usage(calls=1, tokens_in=n(um.prompt_token_count) + n(getattr(um, "tool_use_prompt_token_count", 0)),
                 tokens_out=n(um.candidates_token_count), tokens_thinking=n(getattr(um, "thoughts_token_count", 0)),
                 tokens_cached=n(getattr(um, "cached_content_token_count", 0)))


class Meter:
    def __init__(self, deps: Any):
        self.deps = deps
        self.prices = Prices.load(deps.settings.knowledge_dir)
        self.budget = deps.settings.case_budget_usd
        self.total = Usage()
        self.by_case: dict[str, Usage] = {}
        self.by_agent: dict[str, Usage] = {}
        self.by_model: dict[str, Usage] = {}
        self._since_activity: dict[str, Usage] = {}
        self._loaded: set[str] = set()
        self._warned: set[str] = set()
        self.since = datetime.now(UTC).isoformat(timespec="seconds")

    async def record(self, *, agent: str, model: str, response: Any, ms: float, status: str, case_id: str | None,
                     purpose: str, fallback_from: str | None = None) -> Usage:
        u = usage_of(response)
        u.ms = round(ms)
        u.cost_usd = self.prices.cost(model, u.tokens_in, u.tokens_out + u.tokens_thinking)
        self.total.add(u)
        self.by_agent.setdefault(agent, Usage()).add(u)
        self.by_model.setdefault(model, Usage()).add(u)
        if case_id:
            self.by_case.setdefault(case_id, Usage()).add(u)
            self._since_activity.setdefault(case_id, Usage()).add(u)
        try:
            await self.deps.trace.record({
                "type": "model_call", "id": new_id("mc"), "agent": agent, "case_id": case_id, "model": model,
                "fallback_from": fallback_from, "purpose": purpose, "status": status, "ms": u.ms,
                "tokens_in": u.tokens_in, "tokens_out": u.tokens_out, "tokens_thinking": u.tokens_thinking,
                "tokens_cached": u.tokens_cached, "cost_usd": round(u.cost_usd, 6)})
        except Exception:  # a trace that cannot be written never stops the case
            log.exception("could not write the model-call trace")
        if case_id and self.budget:
            await self._warn_near_budget(case_id)
        return u

    async def _warn_near_budget(self, case_id: str) -> None:
        """Once per case, Activity says when four fifths of the budget for model work is spent."""
        if case_id in self._warned:
            return
        await self._load(case_id)
        spent = self.by_case.get(case_id, Usage()).cost_usd
        if spent < 0.8 * float(self.budget or 0):
            return
        self._warned.add(case_id)
        try:
            await self.deps.activity(
                case_id, "system", f"This case has used about ${spent:.2f} of its ${float(self.budget or 0):.2f} budget "
                "for model work. At the limit, extra research rounds and repairs pause; reading papers and "
                "answering questions carry on.", kind="warning")
        except Exception:  # a notice that cannot be written never stops the case
            log.exception("could not write the budget notice")

    async def check(self, case_id: str | None, purpose: str) -> None:
        """Before a model call: extra rounds (research, repairs, re-asks) stop once the case reaches its budget."""
        if not self.budget or not case_id or purpose not in OPTIONAL_PURPOSES:
            return
        await self._load(case_id)
        spent = self.by_case.get(case_id, Usage()).cost_usd
        if spent >= self.budget:
            raise BudgetExceeded(case_id, spent, self.budget)

    def near_budget(self, case_id: str) -> bool:
        return bool(self.budget) and self.by_case.get(case_id, Usage()).cost_usd >= 0.8 * float(self.budget or 0)

    async def _load(self, case_id: str) -> None:
        """After a restart, the case's spend so far comes back from its activity lines."""
        if case_id in self._loaded:
            return
        self._loaded.add(case_id)
        try:
            items = await self.deps.cases.list(case_id, "activity")
        except Exception:
            return
        # what the activity lines carry, plus what this process used since the case's last activity line
        total = usage_from_activity(items)
        pending = self._since_activity.get(case_id)
        if pending is not None:
            total.add(pending)
        self.by_case[case_id] = total

    def take_since_activity(self, case_id: str) -> dict[str, Any] | None:
        u = self._since_activity.pop(case_id, None)
        return u.as_dict() if u and u.calls else None

    def snapshot(self) -> dict[str, Any]:
        return {"prices_as_of": self.prices.as_of, "budget_usd": self.budget, "since": self.since,
                "total": self.total.as_dict(),
                "by_agent": {k: v.as_dict() for k, v in sorted(self.by_agent.items())},
                "by_model": {k: v.as_dict() for k, v in sorted(self.by_model.items())},
                "by_case": {k: v.as_dict() for k, v in self.by_case.items()}}
