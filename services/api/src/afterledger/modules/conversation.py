"""Module 9: asking AfterLedger anything, by voice or text, in any language.

The intent router decides what is being asked; the case answerer answers only from the case and
its sources, through lookup tools, and the answer is checked for grounding before anyone hears
it. A need the case does not cover becomes a research request rather than a guess.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from ..agents.runner import call_agent
from ..domain.case import Utterance
from ..domain.ids import new_id
from ..domain.money import format_amount
from .common import case_block, knowledge, pack_key, snapshot

MODULE = "conversation"


STATUS_WORDS = {"planning": "being planned", "ready": "papers ready to sign", "submitted": "handed in",
                "acknowledged": "every paper received", "settled": "paid", "returned": "returned by the institution",
                "escalated": "escalated", "handed_off": "with a professional", "closed": "nothing to claim"}


def _day(d: Any) -> str:
    return f"{d.day} {d.strftime('%B %Y')}" if d else ""


async def _case_facts(deps: Any, case_id: str) -> list[dict]:
    """The case as short, complete sentences, each with where it comes from and the rules behind it."""
    snap = await snapshot(deps, case_id)
    k = knowledge(deps)
    facts: list[dict] = []
    for h in snap.holdings:
        if h.status == "closed":
            continue
        inst = await k.institution(h.institution_id) if h.institution_id else None
        val = format_amount(h.value, h.currency) if h.value is not None else "value not known yet"
        word = {"debt": "owed", "lead": "a lead, not confirmed yet"}.get(h.direction if h.direction == "debt" else h.status,
                                                                      "confirmed")
        facts.append({"text": f"{(inst.name + ': ') if inst else ''}{h.label}, {val} ({word}).",
                      "source": f"holding:{h.id}", "cites": []})
    for c in snap.claims:
        inst = await k.institution(c.institution_id) if c.institution_id else None
        name = inst.name if inst else c.institution_id
        text = f"{c.title}: {STATUS_WORDS.get(c.status, c.status)}."
        cites: list[str] = []
        if c.status == "settled" and c.settled_on:
            text += f" Paid {format_amount(c.settled_amount or c.amount_due, c.currency)} on {_day(c.settled_on)}."
        for x in c.clocks:
            if x.status == "late" and x.owner == "institution":
                text += (f" {name} had every paper on {_day(x.started_on)}, so it had to act by {_day(x.due_on)}; it is "
                         f"{x.days_late} day{'s' if x.days_late != 1 else ''} late")
                if x.compensation is not None and x.per_day is not None:
                    text += (f" and owes {format_amount(x.compensation, c.currency)} so far for the delay "
                             f"({format_amount(x.per_day, c.currency)} a day)")
                text += "."
                cites += x.cites
            elif x.status == "running" and x.due_on:
                text += f" {x.label}: due by {_day(x.due_on)}."
                cites += x.cites
        if c.next_action and c.status not in ("settled", "closed"):
            text += f" Next: {c.next_action.rstrip('.')}."
        facts.append({"text": text, "source": f"claim:{c.id}", "cites": cites})
    for t in snap.tasks:
        facts.append({"text": f"Today: {t.title}. {t.body}", "source": f"task:{t.id}", "cites": t.cites})
    for cp in snap.plans:
        claim = snap.claim(cp.claim_id)
        if claim is not None and claim.status == "closed":
            continue
        facts.append({"text": f"Plan for {claim.title if claim else cp.title}: {cp.summary}", "source": f"plan:{cp.id}",
                      "cites": []})
    for lead in snap.leads:
        if lead.status != "closed" and not lead.category.startswith("__"):
            facts.append({"text": f"Lead: {lead.title}. {lead.sentence}", "source": f"lead:{lead.id}",
                          "cites": lead.cites})
    return facts


async def ask(deps: Any, case_id: str, question: str, *, person_id: str | None = None, lang: str | None = None,
              channel: str = "text") -> dict:
    case = await deps.cases.get_case(case_id)
    base = {"case": case_block(case)}
    u = Utterance(id=new_id("ut"), case_id=case_id, person_id=person_id, role="family", lang=lang or "en",
                  text=question, channel=channel, at=deps.now(), intent="question")  # type: ignore[arg-type]
    await deps.cases.put(case_id, "utterances", u)
    intent = await call_agent(deps, "intent_router", {"key": pack_key("intent_router", case, question[:60]), **base,
                                                      "question": question, "lang": lang}, case_id=case_id)
    u.lang = intent.lang
    if intent.lang != "en":
        u.translation = intent.question_en or (await deps.translator.translate([question], "en", intent.lang))[0]
        if u.translation == question:
            u.translation = None
    k = knowledge(deps)

    async def case_lookup(question: str) -> dict:
        """Looks up facts in this family's case that bear on the question."""
        q = question.lower()
        words = [w for w in re.findall(r"\w+", q) if len(w) > 3 and w not in ("when", "will", "what", "does",
                                                                                "have", "with", "there")]
        timing = bool(re.search(r"\b(when|pay|paid|due|late|deadline|how long|settle)", q))
        facts = await _case_facts(deps, case_id)

        def score(f: dict) -> float:
            s_ = sum(w in f["text"].lower() for w in words)
            if timing and f["source"].startswith("claim:") and s_:
                s_ += 1.5                 # a question about when is answered by the claim and its clock
            return s_

        scored = sorted(facts, key=lambda f: -score(f))
        return {"facts": [f for f in scored if score(f) > 0][:6]}

    async def plan_lookup(claim_id: str) -> dict:
        """Returns the plan for one claim: its steps, documents and clocks."""
        cp = next((p for p in await deps.cases.list(case_id, "plans") if p.claim_id == claim_id), None)
        return cp.model_dump(mode="json") if cp else {}

    async def source_lookup(assertion_id: str) -> dict:
        """Returns a rule's sentence, its verbatim quote and its source."""
        a = await k.assertion(assertion_id)
        return {"id": a.id, "text": a.text, "quote": a.quote, "source": a.source.title, "url": a.source.url} if a else {}

    asker = await deps.cases.get(case_id, "people", person_id) if person_id else None
    who = {"name": asker.display_name, "relationship": asker.relationship,
           "languages": asker.languages} if asker else None
    if intent.intent == "unknown_need":
        from .research import ResearchRun, _tools
        country = (case.country_codes or [None])[0]
        look = ResearchRun(id=new_id("rs"), need={"country": country, "question": question})
        web_search, fetch_page, _ = _tools(deps, look, "Answer")
        out = await call_agent(deps, "unknown_need_handler", {"key": pack_key("unknown_need_handler", case, question[:60]),
                                                              **base, "mode": "conversation", "question": question,
                                                              "lang": intent.lang, "asker": who,
                                                              "question_en": intent.question_en or None},
                               tools=[web_search, fetch_page], case_id=case_id)
        answer, answer_en, sources = out.answer, out.answer_en or out.answer, list(out.cites)
        ans_lang = out.lang
        if out.research_request:
            await deps.activity(case_id, MODULE, f"Not known yet, so it was passed to the research service: "
                                f"{out.research_request}", kind="research")
    elif intent.intent in ("smalltalk", "outside"):
        answer = answer_en = ("I'm here for anything about the case - papers, claims, deadlines or what to do next."
                              if intent.intent == "outside" else "I'm here whenever you need me.")
        sources, ans_lang = [], "en"
    else:
        claims = [{"id": c.id, "title": c.title, "status": c.status}
                  for c in await deps.cases.list(case_id, "claims")]
        out = await call_agent(deps, "case_answerer", {"key": pack_key("case_answerer", case, question[:60]), **base,
                                                       "question": question, "lang": intent.lang,
                                                       "question_en": intent.question_en or None, "asker": who,
                                                       "today": deps.today_for(case).isoformat(), "claims": claims},
                               tools=[case_lookup, plan_lookup, source_lookup], case_id=case_id)
        answer, answer_en, sources, ans_lang = out.answer, out.answer_en, out.sources, out.lang
        grounded_texts = []
        for sid in sources:
            a = await k.assertion(sid)
            if a:
                grounded_texts.append((sid, a.text + " " + (a.quote_translation or a.quote)))
        grounded_texts += [(f"case:{i}", t) for i, t in enumerate(out.grounded_on)]
        if grounded_texts:
            g = await deps.grounding.check(answer_en, grounded_texts, threshold=0.35)
            await deps.ledger(case_id, f"answer:{u.id}", "grounding", "pass" if g.supported else "fail",
                              method=g.method, detail=f"support {g.support_score}")
    at = deps.now()
    if u.at is not None and at <= u.at:      # the answer always follows its question
        at = u.at + timedelta(seconds=1)
    reply = Utterance(id=new_id("ut"), case_id=case_id, role="assistant", lang=ans_lang, text=answer,
                      translation=answer_en if ans_lang != "en" else None, at=at, sources=sources,
                      grounded=bool(sources), channel=channel)  # type: ignore[arg-type]
    await deps.cases.put_many(case_id, "utterances", [u, reply])
    deps.changed(case_id, "utterances")
    return {"question": u.model_dump(mode="json"), "answer": reply.model_dump(mode="json"), "intent": intent.intent}
