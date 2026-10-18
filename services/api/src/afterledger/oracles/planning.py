"""Offline answers for the explainer."""
from __future__ import annotations

from ..adapters.llm.models import OracleRequest
from ..domain.money import format_amount
from . import Ctx, oracle


@oracle("explainer")
def explainer(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    plan = p.get("plan") or {}
    claim = p.get("claim") or {}
    fam = ctx.family(req)
    if fam:
        for c in fam.get("claims", []):
            exp = c.get("explain")
            if exp and c.get("plan") == plan.get("key") and set(c.get("expected_route", [])) == set(plan.get("route", [])) \
                    and exp.get("match", "") in claim.get("title", ""):
                steps = [{"step_id": s.get("step_id", ""), "text": s["text"], "text_local": s.get("text_local", ""),
                          "who": s.get("who", ""), "cites": s.get("cites", [])} for s in exp.get("steps", [])] or \
                    [{"step_id": s["id"], "text": s["text"], "who": ""} for s in plan.get("steps", [])]
                return {"summary": exp["summary"], "summary_local": exp.get("summary_local", ""),
                        "lang": p.get("lang", "en"), "steps": steps}
    return {"summary": baseline_summary(p), "lang": "en",
            "steps": [{"step_id": s["id"], "text": s["text"], "who": ""} for s in plan.get("steps", [])]}


def baseline_summary(p: dict) -> str:
    """The amount, the facts that set the route, and the step the route turns on, in plain words."""
    plan, claim = p.get("plan") or {}, p.get("claim") or {}
    amount = claim.get("amount")
    parts = [format_amount(amount, claim.get("currency", "INR"), decimals=False)] if amount else []
    facts = {f["name"]: f.get("value") for f in p.get("facts", []) if isinstance(f, dict)}
    nominee = next((v for k, v in facts.items() if "nominee" in k or "nomination" in k), None)
    if nominee == "yes":
        who = (p.get("claimant") or "").split(" ")[0]
        parts.append(f"{who} is the nominee" if who else "There is a nominee")
    elif nominee == "no":
        parts.append("No nominee")
    if facts.get("has_will") == "no":
        parts.append("no will" if parts else "No will")
    steps = {s["id"]: s["text"] for s in plan.get("steps", [])}
    route = [steps[r] for r in plan.get("route", []) if r in steps]
    def lower(x: str) -> str:                      # "No nominee" -> "no nominee", but names keep their capital
        return x[0].lower() + x[1:] if x.split(" ", 1)[0] in ("No", "There", "The", "A", "An") else x
    head = ", ".join(parts[:1] + [lower(x) for x in parts[1:]]) if parts else ""
    if route:
        return (head + ". " if head else "") + route[0].split(". ")[0].rstrip(".") + "."
    if plan.get("questions"):
        return (head + ". " if head else "") + "The route depends on a few answers; the plan covers every answer until then."
    return (head + ". " if head else "") + (plan.get("title") or "")
