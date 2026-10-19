"""What an agent call is for, carried through the call (and every model call inside it) without passing it down
every function: the case it works on, and its purpose, which decides what a budget may stop."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

CASE_ID: ContextVar[str | None] = ContextVar("afterledger_case_id", default=None)
# core: the case's own work, and reask (a second ask for an answer a required step needs). research and repair are
# extra rounds, which a case's budget can stop.
PURPOSE: ContextVar[str] = ContextVar("afterledger_purpose", default="core")
OPTIONAL_PURPOSES = frozenset({"research", "repair"})
# The research run a model call belongs to, so one run's cost can be capped (AL_RESEARCH_BUDGET_USD) whichever case,
# or none, it works for.
RESEARCH_RUN: ContextVar[str | None] = ContextVar("afterledger_research_run", default=None)


@contextmanager
def working_on(case_id: str | None = None, purpose: str | None = None) -> Iterator[None]:
    tokens = []
    if case_id is not None:
        tokens.append((CASE_ID, CASE_ID.set(case_id)))
    if purpose is not None:
        tokens.append((PURPOSE, PURPOSE.set(purpose)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


@contextmanager
def researching(run_id: str) -> Iterator[None]:
    """Every model call inside counts towards this research run's cost."""
    tok = RESEARCH_RUN.set(run_id)
    try:
        yield
    finally:
        RESEARCH_RUN.reset(tok)
