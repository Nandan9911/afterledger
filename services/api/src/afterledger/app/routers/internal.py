"""Endpoints that Google Cloud and the operator call, not visitors.

Cloud Tasks delivers each piece of background work (reading a batch of papers, preparing a pack, planning the
day) as POST /internal/tasks/{kind}, carrying the internal token in X-AfterLedger-Token. A handler that fails
returns 500 so Cloud Tasks retries it (the queue caps the attempts); work that can never succeed, because its
case is gone, is acknowledged and dropped so it is not retried. POST /internal/clean, with the same token, clears
the copy before a demonstration (infra/scripts/clean-site.sh). None of this is in the public API document.
"""
from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from ...runtime import Deps
from ..actions import NotFound
from .common import get_deps

log = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", include_in_schema=False)


def _allowed(deps: Deps, token: str | None) -> None:
    expected = deps.settings.internal_token
    if not expected or not token or not hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(403, "Not allowed")


@router.post("/clean")
async def clean(samples: str = "keep", deps: Deps = Depends(get_deps),
                x_afterledger_token: str | None = Header(None)) -> dict:
    """Before a demonstration: removes every case that is not a sample, with its papers, page images and filled
    forms, and forgets the country questions people asked. The sample families stay as they are (a live run may
    have prepared them); with samples=reset each is put back as the image built it. What research learned about
    institutions' rules is kept, with its sources: it holds nothing about a family."""
    _allowed(deps, x_afterledger_token)
    from ...modules import challenge
    from ..demo import family_keys, reset
    removed: list[str] = []
    drop = getattr(deps.blobs, "delete_prefix", None)
    for case in await deps.cases.list_cases():
        if case.demo_key:
            continue
        await deps.cases.delete_case(case.id)
        if drop is not None:
            await drop(f"cases/{case.id}/")
        removed.append(case.id)
    questions = await challenge.clear_challenges(deps)
    done: dict[str, str] = {}
    for key in deps.settings.shown_families(family_keys()) if samples == "reset" else []:
        try:
            out = await reset(deps, key)
            done[key] = str(out.get("state", "ready")) if isinstance(out, dict) else "ready"
        except Exception as exc:          # one family that cannot be rebuilt does not stop the rest
            log.exception("could not reset the %s family", key)
            done[key] = f"error: {exc}"[:200]
    log.info("cleaned: %d cases removed, %d questions forgotten, samples reset %s", len(removed), questions, done)
    return {"cases_removed": len(removed), "questions_removed": questions, "samples_reset": done}


@router.post("/tasks/{kind}")
async def run_task(kind: str, request: Request, deps: Deps = Depends(get_deps),
                   x_afterledger_token: str | None = Header(None)) -> dict:
    _allowed(deps, x_afterledger_token)
    run = getattr(deps.tasks, "run", None)
    if run is None:                       # background work runs in process here; nothing is delivered
        raise HTTPException(404, "This service does not take delivered tasks")
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "The task body is not JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(400, "The task body must be a JSON object")
    attempt = request.headers.get("X-CloudTasks-TaskRetryCount", "0")
    try:
        await run(kind, payload)
    except NotFound as exc:
        log.warning("task %s dropped: %s", kind, exc)
        return {"ok": False, "dropped": str(exc) or "not found"}
    except KeyError as exc:
        if exc.args and str(exc.args[0]).startswith("no handler"):
            raise HTTPException(404, str(exc.args[0])) from exc
        log.warning("task %s dropped, something it needs is gone: %s", kind, exc)
        return {"ok": False, "dropped": f"not found: {exc}"}
    except Exception:
        log.exception("task %s failed (attempt %s); Cloud Tasks will retry it", kind, attempt)
        raise HTTPException(500, "The task failed and will be retried") from None
    return {"ok": True}
