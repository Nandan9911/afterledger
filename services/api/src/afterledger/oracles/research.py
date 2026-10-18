"""Offline answers for the research agents.

For prepared situations (data/eval/research_fixtures/<country>.yaml) the researchers really call
their search and page-fetch tools against the page snapshots, then return the quotes a careful
reader copies from the pages they fetched - including one deliberately misquoted line, which the
citation checker must drop. Anything not prepared gets an honest gap: nothing can be verified
offline, so the plan starts by asking the institution.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ..adapters.llm.models import OracleRequest, ToolCalls
from ..domain.countries import country_name
from . import Ctx, oracle


@lru_cache(maxsize=1)
def _fixtures() -> dict[str, dict]:
    from ..config import get_settings
    folder = get_settings().data_dir / "eval" / "research_fixtures"
    out = {}
    for f in sorted(Path(folder).glob("*.yaml")) if Path(folder).is_dir() else []:
        fx = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        out[fx.get("need_key", f.stem)] = fx
    return out


def fixture_for(need: dict) -> dict | None:
    key = f"{need.get('country')}/{need.get('holding_kind', 'any')}"
    fx = _fixtures().get(key)
    if fx is None:
        for k, v in _fixtures().items():
            if k.split("/")[0] == need.get("country") and v.get("plan_key", "").split("/")[0] == need.get("country"):
                fx = v
                break
    return fx


_KIND_QUESTIONS = {
    "savings": ["Which documents does the bank need to pay a deceased customer's balance to the heirs?",
                "Is there a limit below which the bank pays without a court document?",
                "How long does the bank take once it has every document?"],
    "life_policy": ["How does a beneficiary claim a death benefit, and which documents are needed?",
                    "How long does the insurer have to pay?"],
}


@oracle("question_planner")
def question_planner(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    need = p.get("need") or {}
    fx = fixture_for(need)
    rnd = int(p.get("round") or 1)
    if rnd > 1:                       # a later round: the fixture's questions for what the last round left open
        return {"questions": (((fx or {}).get("rounds") or {}).get(rnd) or {}).get("questions") or []}
    if fx:
        return {"questions": fx["questions"]}
    kind = need.get("holding_kind") or "savings"
    qs = _KIND_QUESTIONS.get(kind, _KIND_QUESTIONS["savings"])
    country = country_name(need.get("country", ""))
    return {"questions": [{"id": f"q{i + 1}", "question": q, "variable": None, "language": "en",
                           "queries": [f"{country}: {q}"]} for i, q in enumerate(qs)]}


def _plan_for(fx: dict | None, who: str, rnd: int, single_pass: bool) -> dict:
    """What this researcher does in this round, from the fixture. A later round has its own plan (fixtures.rounds);
    a single-pass run (AL_RESEARCH_MODE=fixed) finds in its one pass what the rounds would have found."""
    if rnd > 1:
        return ((((fx or {}).get("rounds") or {}).get(rnd) or {}).get(who)) or \
            {"queries": [], "fetch": [], "assertions": [], "gaps": []}
    plan = dict((fx or {}).get(who) or {})
    if single_pass:
        for later in ((fx or {}).get("rounds") or {}).values():
            extra = (later or {}).get(who) or {}
            plan["assertions"] = [*plan.get("assertions", []), *extra.get("assertions", [])]
            plan["queries"] = [*plan.get("queries", []), *[{k: v for k, v in q.items() if k != "question"}
                                                           for q in extra.get("queries", [])]]
        plan["gaps"] = []
    return plan


def _researcher(ctx: Ctx, req: OracleRequest, who: str) -> dict | ToolCalls:
    p = ctx.payload(req)
    need = p.get("need") or {}
    fx = fixture_for(need)
    rnd = int(p.get("round") or 1)
    single = bool(p.get("single_pass"))
    plan = _plan_for(fx, who, rnd, single)
    qids = {q.get("id") for q in p.get("questions", [])}

    def mine(question: str | None) -> bool:
        """Fixture items name the question they answer; a researcher given some questions finds only theirs."""
        return rnd > 1 or single or not question or question in qids
    results = req.tool_results
    searched = [r for r in results if r["name"] == "web_search"]
    fetched = [r for r in results if r["name"] == "fetch_page"]
    if not searched:
        queries = [q for q in plan.get("queries") or [] if mine(q.get("question"))] or \
            [{"query": q, "lang": qq.get("language") or "en"} for qq in p.get("questions", [])
             for q in qq.get("queries", [])[:1]]
        return ToolCalls([("web_search", {"query": q["query"], "lang": q.get("lang")}) for q in queries[:4]])
    if not fetched:
        found = []
        for r in searched:
            for hit in (r["response"] or {}).get("results", []):
                if hit["url"] not in found:
                    found.append(hit["url"])
        wanted = [u for u in plan.get("fetch", []) if u in found] or (found[:2] if not fx else [])
        if not wanted:
            return {"assertions": [], "pages_read": [], "dropped": [],
                    "gaps": plan.get("gaps") or ["No official or institution page was found for these questions."]}
        return ToolCalls([("fetch_page", {"url": u}) for u in wanted])
    pages_read = [(r["response"] or {}).get("url") for r in fetched if not (r["response"] or {}).get("error")]
    if not fx:
        return {"assertions": [], "pages_read": pages_read, "dropped": [],
                "gaps": ["Offline, pages that were not prepared cannot be read for quotes; a live run reads them."]}
    by_id = {a["id"]: a for a in fx.get("assertions", [])}
    out = []
    for aid in plan.get("assertions", []):
        a = by_id.get(aid)
        if a:
            a = {**a, **(plan.get("variants") or {}).get(aid, {})}
        if a and a["source_url"] in pages_read and mine(a.get("question")):
            out.append({"id": a["id"], "text": a["text"], "quote": a["quote"],
                        "quote_translation": a.get("quote_translation"), "source_url": a["source_url"],
                        "jurisdiction": a["jurisdiction"], "topic": a.get("topic", ""), "question_id": None})
    for trap in plan.get("traps", []):
        if trap["source_url"] in pages_read and mine(trap.get("question")):
            out.append({k: v for k, v in trap.items() if k != "question"} | {"question_id": None})
    aside = [u for u in plan.get("set_aside", []) if u in pages_read]
    gaps = [g["text"] if isinstance(g, dict) else g for g in plan.get("gaps") or []
            if not isinstance(g, dict) or mine(g.get("question"))]
    return {"assertions": out, "pages_read": pages_read, "dropped": aside, "gaps": gaps}


@oracle("researcher_primary")
def researcher_primary(ctx: Ctx, req: OracleRequest):
    return _researcher(ctx, req, "researcher_a")


@oracle("researcher_second")
def researcher_second(ctx: Ctx, req: OracleRequest):
    return _researcher(ctx, req, "researcher_b")


@oracle("research_adjudicator")
def research_adjudicator(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fx = fixture_for(p.get("need") or {})
    a_ids = {x["id"] for x in p.get("a", [])}
    b_ids = {x["id"] for x in p.get("b", [])}
    if fx and fx.get("adjudication"):
        decisions = [d for d in fx["adjudication"].get("decisions", []) if d["id"] in a_ids | b_ids]
        return {"decisions": decisions, "summary": fx["adjudication"].get("summary", ""), "re_research": []}
    decisions = [{"id": i, "verdict": "accept", "reason": "Both researchers found it." if i in a_ids & b_ids else
                  "One researcher found it; not contradicted."} for i in sorted(a_ids | b_ids)]
    return {"decisions": decisions, "summary": f"{len(a_ids & b_ids)} points agreed." if decisions else
            "No official source was found, so nothing can be relied on yet.", "re_research": []}


@oracle("procedure_compiler")
def procedure_compiler(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    need = p.get("need") or {}
    fx = fixture_for(need)
    if fx and fx.get("compiled_plan"):
        return {"plan": fx["compiled_plan"], "notes": "Scripted compiler response."}
    country = need.get("country", "XX")
    kind = need.get("holding_kind") or "savings"
    inst = need.get("institution_id")
    key = f"{country}/{kind}" + (f"@{inst}" if inst else "")
    return {"plan": {
        "key": key, "title": "Not verified yet: ask the institution", "jurisdiction": country,
        "institution_kind": "bank", "holding_kinds": [kind], "level": "draft", "unverified": True,
        "variables": [], "documents": [{"id": "death_cert", "name": "Death certificate"}], "clocks": [],
        "steps": [{"id": "ask_institution", "kind": "follow_up", "actor": "family",
                   "text": "Write to the institution asking, in writing, for the documents and steps it needs."},
                  {"id": "submit", "kind": "submit", "actor": "family",
                   "text": "Send the papers the institution asks for.", "documents": ["death_cert"],
                   "after": ["ask_institution"]}],
        "notes": "Unverified: no tier 1 or 2 source was found."}, "notes": "Baseline compiler: nothing verified."}


