"""Evaluation: the checks that must always pass, and agents against their baselines."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ...agents.meter import usage_from_activity
from ...runtime import Deps
from ..schemas import CaseUsage, UsageReport, UsageView
from ..views.localize import localize, page_language
from .common import get_deps

router = APIRouter(prefix="/v1/eval", tags=["evaluation"])
log = logging.getLogger(__name__)


def _report_path(deps: Deps):
    return deps.settings.state_dir / "eval" / "latest.json"


def _running(deps: Deps) -> bool:
    task = deps.extras.get("eval_task")
    return task is not None and not task.done()


@router.get("")
async def latest(request: Request, deps: Deps = Depends(get_deps)) -> dict[str, Any]:
    """The latest evaluation report, or an empty one that says how to make it, in the page's language."""
    if deps.settings.product:              # the public site has no evaluation pages
        raise HTTPException(404, "Not found")
    from ...seed.snapshot import bundle_report
    p = _report_path(deps)
    if p.exists():
        report = json.loads(p.read_text(encoding="utf-8"))
    elif (built := bundle_report(deps)) is not None:
        report = built                  # the offline evaluation made with this deployment's demo cases
    else:
        report = {"ran_at": None, "mode": deps.models.label, "checks": [], "agents": [], "coverage": [],
                  "note": "No evaluation has run yet. Start one here, or run `python tools/dev.py eval`."}
    report["running"] = _running(deps)
    error = deps.extras.get("eval_error")
    if error:
        report["error"] = error
    targets = [(report, "note")]
    targets += [(c, f) for c in report.get("checks", []) for f in ("name", "result", "shows", "detail")]
    targets += [(a, f) for a in report.get("agents", []) for f in ("module", "test_set", "measure", "baseline",
                                                                     "baseline_how", "agent")]
    targets += [(c, "rules_detail") for c in report.get("coverage", [])]
    await localize(deps, page_language(request), targets)
    return report


@router.post("/run")
async def run(deps: Deps = Depends(get_deps)) -> dict[str, Any]:
    """Runs the evaluation in a separate process, so the service keeps answering while it works."""
    if deps.settings.product:
        raise HTTPException(404, "Not found")
    if _running(deps):
        return {"started": False, "note": "An evaluation is already running."}
    deps.extras.pop("eval_error", None)
    env = {**os.environ, "AL_STATE_DIR": str(deps.settings.state_dir)}
    try:
        proc = await asyncio.create_subprocess_exec(sys.executable, "-m", "afterledger.cli", "eval", "--no-seed",
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                                                    env=env)
    except NotImplementedError:     # an event loop without subprocess support (some Windows set-ups)
        return await run_inline(deps)

    async def wait() -> None:
        out, _ = await proc.communicate()
        if proc.returncode not in (0, 1):            # 1 means a check failed, which the report shows
            deps.extras["eval_error"] = (out or b"").decode("utf-8", "replace")[-300:]

    deps.extras["eval_task"] = asyncio.get_running_loop().create_task(wait())
    return {"started": True}


@router.post("/run-inline", include_in_schema=False)
async def run_inline(deps: Deps = Depends(get_deps)) -> dict[str, Any]:
    """The same, in this process (tests use it)."""
    from ...eval.runner import run_all, write_report
    if _running(deps):
        return {"started": False, "note": "An evaluation is already running."}

    async def go() -> None:
        deps.extras.pop("eval_error", None)
        try:
            write_report(await run_all(deps), _report_path(deps))
        except Exception as exc:  # the screen must learn that the run ended
            log.exception("evaluation failed")
            deps.extras["eval_error"] = str(exc)[:300]

    deps.extras["eval_task"] = asyncio.get_running_loop().create_task(go())
    return {"started": True}


@router.get("/usage", response_model=UsageReport)
async def usage(deps: Deps = Depends(get_deps)) -> UsageReport:
    """Model use: each case's total from its activity (it survives a restart), and each agent's and model's since
    this copy of the service started. Costs are estimates from data/knowledge/prices.yaml. Not on the public site:
    it names every case."""
    if deps.settings.product:
        raise HTTPException(404, "Not found")
    snap = deps.meter.snapshot()
    cases = []
    for c in await deps.cases.list_cases():
        used = usage_from_activity(await deps.cases.list(c.id, "activity"))
        if used.calls:
            cases.append(CaseUsage(case_id=c.id, title=c.title, demo_key=c.demo_key, usage=UsageView(**used.as_dict())))
    cases.sort(key=lambda x: (-x.usage.cost_usd, -x.usage.calls, x.title))
    return UsageReport(prices_as_of=snap["prices_as_of"], budget_usd=snap["budget_usd"], since=snap["since"],
                       total=UsageView(**snap["total"]),
                       by_agent={k: UsageView(**v) for k, v in snap["by_agent"].items()},
                       by_model={k: UsageView(**v) for k, v in snap["by_model"].items()}, cases=cases)
