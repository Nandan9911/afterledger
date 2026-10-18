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
