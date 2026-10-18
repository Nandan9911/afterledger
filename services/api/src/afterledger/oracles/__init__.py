"""Offline oracles: what each agent answers when no live model is used.

For the six demo families an oracle answers from the family's truth file (what is really printed,
who is really related to whom), with the deliberate noise a demo needs (reader B misreads one
photographed balance). For any other input an oracle uses a simple baseline (keyword rules,
regular expressions, the knowledge base), and says so in its output where that matters. Live
runs replace every oracle with Gemini; nothing else in the pipeline changes.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..adapters.llm.models import OracleRequest

REGISTRY: dict[str, Callable[[Ctx, OracleRequest], Any]] = {}


def oracle(name: str) -> Callable:
    def wrap(fn: Callable) -> Callable:
        REGISTRY[name] = fn
        return fn
    return wrap


@dataclass
class Ctx:
    deps: Any

    def family(self, req: OracleRequest):
        """The demo family a request is about, at the case's story day, or None."""
        p = req.payload if isinstance(req.payload, dict) else {}
        case = p.get("case") or {}
        key = case.get("demo")
        if not key:
            return None
        from ..seed.families import load_family
        day = case.get("story_day")
        try:
            return load_family(key, date.fromisoformat(day) if day else None)
        except FileNotFoundError:
            return None

    def payload(self, req: OracleRequest) -> dict:
        return req.payload if isinstance(req.payload, dict) else {"text": req.payload}


def oracle_for(name: str, deps: Any) -> Callable[[OracleRequest], Any]:
    _load_all()
    fn = REGISTRY.get(name)
    if fn is None:
        raise KeyError(f"no offline oracle for agent '{name}'")
    ctx = Ctx(deps)
    return lambda req: fn(ctx, req)


_loaded = False


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    from . import (  # noqa: F401
        conversation,
        discovery,
        followup,
        forms,
        intake,
        jurisdiction,
        people,
        planning,
        reading,
        research,
    )
    _loaded = True
