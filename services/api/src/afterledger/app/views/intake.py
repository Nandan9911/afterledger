"""Telling the story: the conversation so far and what has been understood from it."""
from __future__ import annotations

import re
from typing import Any

from ...domain.calendars import format_date
from ...domain.countries import country_name
from ...modules.common import Snapshot, snapshot
from ...modules.research import language_name
from ..schemas import ConversationView, KnownLine
from .common import chip, cite_views, tidy_utterance

ROLE_WORDS = {"claimant": "Will make the claims.", "helper": "Helps with the papers.", "executor": "The executor.",
              "heir": "A legal heir.", "nominee": "The nominee."}


def pronoun(snap: Snapshot, utterances: list[Any]) -> str:
    """The deceased's possessive pronoun, from how the family speak of them; 'their' when unsure."""
    rels = {(p.relationship or "").lower() for p in snap.family()}
    if "wife" in rels:
        return "his"
    if "husband" in rels:
        return "her"
    text = " ".join((u.translation or u.text) for u in utterances if u.role == "family").lower()
    first = re.search(r"\bmy (husband|father|son|brother|grandfather|uncle|dad|wife|mother|daughter|sister|"
                      r"grandmother|aunt|mum|mom)\b", text)      # the first person named is the one who died
    if first is None:
        return "their"
    return "his" if first.group(1) in ("husband", "father", "son", "brother", "grandfather", "uncle", "dad") else "her"


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


async def conversation(deps: Any, case_id: str) -> ConversationView:
    snap = await snapshot(deps, case_id, ["people", "checkpoints", "holdings"])
    utterances = sorted(await deps.cases.list(case_id, "utterances"), key=lambda u: (u.at or deps.now(), u.id))
    case, sit = snap.case, snap.case.situation
    his = pronoun(snap, utterances)
    known: list[KnownLine] = []
    if sit and sit.deceased_name:
        where = case.died_in or ""
        if case.died_in and sit.death_country and sit.death_country not in (case.died_in or ""):
            where = f"{case.died_in}, {country_name(sit.death_country)}"
        died = f"Died on {format_date(case.died_on, 'en')}" if case.died_on else "Date of death not known yet"
        text = died + (f" in {where}" if where else "") + "."
        if sit.occupation:
            text += f" {sit.occupation.rstrip('.')}."
        known.append(KnownLine(label="Who died", text=f"{sit.deceased_name}. {text}", person_id=case.deceased_id))
    for p in snap.family():
        bits = [f"{p.display_name}, {his} {p.relationship}" if p.relationship else p.display_name]
        if p.city:
            bits[0] += f", in {p.city}"
        line = bits[0] + "."
        if p.languages:
            line += f" Speaks {language_name(p.languages[0])}" + (" and prefers voice." if p.channel == "voice" else ".")
        roles = [ROLE_WORDS[r] for r in ("claimant", "helper", "executor", "heir") if r in p.roles]
        if roles:
            line += " " + " ".join(roles[:2])
        known.append(KnownLine(label="Family", text=line, person_id=p.id))
    if sit and sit.holdings_mentioned:
        text = _join(sit.holdings_mentioned)
        known.append(KnownLine(label="Money mentioned", text=text[:1].upper() + text[1:] + "."))
    jur = case.jurisdiction
    if jur and jur.summary:
        known.append(KnownLine(label="Whose rules apply", text=jur.summary))
    elif case.country_codes:
        names = _join([country_name(c) for c in case.country_codes])
        known.append(KnownLine(label="Whose rules apply",
                               text=f"{names}, for every holding so far. Our agents check this against the law; there "
                                    "is nothing for you to confirm."))
    if sit and sit.will != "unknown":
        known.append(KnownLine(label="Will", text="There is a will." if sit.will == "yes" else "No will was found."))
    summary = next((c for c in snap.checkpoints if c.code == "C1"), None)
    ids = {s for u in utterances for s in u.sources if not s.startswith(("fact:", "paper:", "case:", "holding:", "claim:"))}
    utterances = [await tidy_utterance(deps, u) for u in utterances]
    return ConversationView(utterances=utterances, people=[c for c in (chip(p) for p in snap.people) if c],
                            known=known, urgent=list(sit.urgent) if sit else [], summary=summary, stage=case.stage,
                            languages=case.languages, cites=await cite_views(deps, ids, deps.today_for(case)))
