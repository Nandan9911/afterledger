"""Offline answers for the conversation agents."""
from __future__ import annotations

import re

from ..adapters.llm.models import OracleRequest, ToolCalls
from ..domain.checks import normalise
from . import Ctx, oracle
from .intake import detect_lang


def _qa_match(fam, question: str) -> dict | None:
    q = normalise(question)
    for item in fam.get("qa", []) if fam else []:
        pats = [normalise(x) for x in item.get("match", [])]
        if any(x in q for x in pats) or normalise(item.get("question", "")) == q:
            return item
    return None


@oracle("intent_router")
def intent_router(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    q = p.get("question", "")
    lang = p.get("lang") or detect_lang(q)
    fam = ctx.family(req)
    hit = _qa_match(fam, q)
    if hit:
        return {"intent": "case_question", "topic": "", "lang": hit.get("lang", lang),
                "question_en": hit.get("question_en", "")}
    low = q.lower()
    if re.search(r"\b(hello|hi|thanks|thank you|namaste|hola|gracias)\b", low):
        return {"intent": "smalltalk", "topic": "", "lang": lang}
    if re.search(r"\b(weather|cricket|recipe|movie)\b", low):
        return {"intent": "outside", "topic": "", "lang": lang}
    return {"intent": "case_question", "topic": "", "lang": lang}


_RULE_Q = re.compile(r"\b(which|what) (rule|law|regulation|direction|section|paragraph)|\brule says\b|\bsays so\b",
                     re.I)
_STEPS_Q = re.compile(r"\b(which|what) (papers|documents|steps)\b|\bwhat (is|comes) next\b|\bnext step", re.I)


def _claim_for(claims: list[dict], question: str) -> dict | None:
    """The claim a question is about: the one whose title shares the most words with it."""
    words = {w for w in re.findall(r"\w{4,}", question.lower())}
    best = max(claims, key=lambda c: len(words & set(re.findall(r"\w{4,}", c.get("title", "").lower()))),
               default=None)
    return best if best and words & set(re.findall(r"\w{4,}", best.get("title", "").lower())) else None


def _from_plan(plan: dict) -> dict:
    """An answer from plan_lookup: the next steps, and the papers the claim needs, citing the steps' rules."""
    steps = [s for s in plan.get("steps", []) if s.get("status") in ("todo", "doing", "blocked")][:2]
    docs = [d.get("name") for d in plan.get("documents", []) if d.get("name")][:6]
    if not steps and not docs:
        return {"answer": "That plan has no steps left to do.", "answer_en": "That plan has no steps left to do.",
                "lang": "en", "sources": [], "follow_ups": [], "grounded_on": []}
    text = (" ".join(s["text"] for s in steps) + (" The papers it needs: " + ", ".join(docs) + "." if docs else "")).strip()
    cites = list(dict.fromkeys(c for s in steps for c in s.get("cites", [])))
    return {"answer": text, "answer_en": text, "lang": "en", "sources": cites, "follow_ups": [],
            "grounded_on": [s["text"] for s in steps]}


@oracle("case_answerer")
def case_answerer(ctx: Ctx, req: OracleRequest) -> dict | ToolCalls:
    p = ctx.payload(req)
    q = p.get("question", "")
    lang = p.get("lang") or detect_lang(q)
    fam = ctx.family(req)
    hit = _qa_match(fam, q)
    if hit:
        return {"answer": hit["answer"], "answer_en": hit.get("answer_en", hit["answer"]), "lang": hit.get("lang", lang),
                "sources": hit.get("sources", []), "follow_ups": hit.get("follow_ups", []),
                "grounded_on": hit.get("grounded_on", [])}
    results = req.tool_results
    if not results:
        claim = _claim_for(p.get("claims", []), q) if _STEPS_Q.search(q) else None
        if claim:                             # steps and papers: the claim's plan
            return ToolCalls([("plan_lookup", {"claim_id": claim["id"]})])
        return ToolCalls([("case_lookup", {"question": q})])
    last = results[-1]
    if last["name"] == "plan_lookup":
        return _from_plan(last["response"] or {})
    if last["name"] == "source_lookup":       # "which rule says so": the rule, in its own words
        a = last["response"] or {}
        if a.get("id"):
            text = f"{a['text']} In the rule's own words: “{a['quote']}” ({a['source']})."
            return {"answer": text, "answer_en": text, "lang": "en", "sources": [a["id"]], "follow_ups": [],
                    "grounded_on": [a["text"]]}
    if last["name"] == "case_lookup" and _RULE_Q.search(q):
        cites = [c for f in (last["response"] or {}).get("facts", []) for c in f.get("cites", [])]
        if cites:
            return ToolCalls([("source_lookup", {"assertion_id": cites[0]})])
    found = (next((r for r in results if r["name"] == "case_lookup"), results[0])["response"] or {})
    facts = found.get("facts", [])
    if not facts:
        return {"answer": "I can't find that in the case yet. Add a paper or ask me in another way, and I'll look again.",
                "answer_en": "I can't find that in the case yet.", "lang": "en", "sources": [], "follow_ups": [],
                "grounded_on": []}
    best = facts[:2] if len(facts) > 1 and facts[1].get("source", "").split(":")[0] == facts[0].get(
        "source", "").split(":")[0] else facts[:1]
    text = " ".join(f["text"] for f in best)
    cites = [c for f in best for c in f.get("cites", [])]
    return {"answer": text, "answer_en": text, "lang": "en", "sources": list(dict.fromkeys(cites)),
            "follow_ups": [], "grounded_on": [f["text"] for f in best]}


@oracle("unknown_need_handler")
def unknown_need_handler(ctx: Ctx, req: OracleRequest) -> dict | ToolCalls:
    p = ctx.payload(req)
    q = p.get("question", "")
    if p.get("mode") == "first_answer":
        # a quick check of one official page before the full research starts
        from .research import fixture_for
        need = p.get("need") or {}
        fx = fixture_for(need) or {}
        fa = fx.get("first_answer")
        ra = fx.get("researcher_a") or {}
        if not req.tool_results:
            query = (ra.get("queries") or [{"query": q}])[0]
            return ToolCalls([("web_search", {"query": query["query"], "lang": query.get("lang")})])
        if len(req.tool_results) == 1 and (ra.get("fetch") or []):
            return ToolCalls([("fetch_page", {"url": ra["fetch"][0]})])
        if fa:
            official = fa.get("found_official", bool(fa.get("cites")))
            return {"can_answer": official, "answer": fa["text"], "answer_en": fa.get("translation", fa["text"]),
                    "lang": fa.get("lang", need.get("lang", "en")), "research_request": q,
                    "sources": [ra["fetch"][0]] if official and ra.get("fetch") else [], "cites": fa.get("cites", []),
                    "found_official": official, "general": fa.get("general", []), "general_en": fa.get("general_en", [])}
        return {"can_answer": False, "answer": "No official page answers this offline. The full research runs live.",
                "answer_en": "No official page answers this offline. The full research runs live.",
                "lang": "en", "research_request": q, "found_official": False}
    text = ("That is not in the case yet, and this copy runs offline, so it cannot look it up now. A live run "
            "searches official sources for it and answers with the source.")
    return {"can_answer": False, "answer": text, "answer_en": text, "research_request": q, "lang": "en"}
