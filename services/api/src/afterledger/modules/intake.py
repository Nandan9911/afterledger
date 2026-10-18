"""Module 1: telling the story.

A family member talks or types in their own language. Each turn is heard (listener), the
situation model is updated (situation extractor), and the single next question worth asking is
chosen (gap planner), checked for tone and put into the family's language (tone critic). When
nothing more is needed, the family confirms a one-page summary (checkpoint C1).
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ..agents.runner import call_agent
from ..domain.case import CaseFile, NameForm, Person, SituationModel, Utterance
from ..domain.checks import normalise
from ..domain.ids import new_id, stable_id
from ..domain.names import script_of
from .common import case_block, open_checkpoint, pack_key

MODULE = "intake"
HEIR_WORDS = {"wife", "husband", "son", "daughter", "child", "father", "mother", "parent"}
COLORS = ["#F3D9C4", "#CFE3F5", "#E6DCF2", "#D8EFD9", "#F7E3B5", "#E2E8E4", "#F5D0D6"]


async def start_case(deps: Any, *, lang: str = "en", demo_key: str | None = None, story_day: date | None = None,
                     case_id: str | None = None, starter_name: str | None = None,
                     mode: str = "family") -> tuple[CaseFile, Utterance]:
    case = CaseFile(id=case_id or new_id("case"), title="New case", demo_key=demo_key, story_day=story_day,
                    created_at=deps.now(), updated_at=deps.now(), languages=[lang], stage="intake",
                    situation=SituationModel(mode=mode), model_mode=deps.models.label)
    if starter_name and starter_name.strip():
        name = starter_name.strip()
        starter = Person(id=stable_id("p", case.id, normalise(name)), case_id=case.id, display_name=name,
                         names=[NameForm(text=name, script=script_of(name), seen_on="family")], languages=[lang],
                         initials=_initials(name), color=COLORS[0], access="owner", joined_on=deps.today_for(case))
        await deps.cases.put(case.id, "people", starter)
        case.viewer_person_id = starter.id
    await deps.cases.put_case(case)
    await deps.activity(case.id, MODULE, "Started a new case.", kind="step")
    reply = await _next_question(deps, case, [], lang)
    return case, reply


async def _utterances(deps: Any, case_id: str) -> list[Utterance]:
    items = await deps.cases.list(case_id, "utterances")
    return sorted(items, key=lambda u: (u.at or deps.now(), u.id))


async def _next_question(deps: Any, case: CaseFile, history: list[Utterance], lang: str) -> Utterance | None:
    base = {"case": case_block(case)}
    heard = [_said(u) for u in history]
    n_family = sum(1 for u in history if u.role == "family")
    gap = await call_agent(deps, "gap_planner", {"key": pack_key("gap_planner", case, str(n_family)), **base,
                                                 "situation": case.situation.model_dump(mode="json") if case.situation else {},
                                                 "utterances": heard}, case_id=case.id)
    if gap.done:
        await _offer_summary(deps, case)
        return None
    tone = await call_agent(deps, "tone_critic", {"key": pack_key("tone_critic", case, str(n_family)), **base,
                                                  "question": gap.question, "lang": lang}, case_id=case.id)
    at = deps.now()
    last = max((x.at for x in history if x.at), default=None)
    if last is not None and at <= last:       # a reply always follows what it answers
        at = last + timedelta(seconds=1)
    u = Utterance(id=new_id("ut"), case_id=case.id, role="assistant", lang=tone.lang, text=tone.question_local,
                  translation=tone.question if tone.lang != "en" else None, at=at, sources=gap.cites,
                  intent=gap.topic or None, why=gap.why or "")
    await deps.cases.put(case.id, "utterances", u)
    if gap.why and gap.cites:
        await deps.activity(case.id, MODULE, f"Asked: “{gap.question}” {gap.why}", agent="gap_planner",
                            kind="question", cites=gap.cites)
    deps.changed(case.id, "utterances")
    return u


def _said(u: Utterance) -> dict:
    """One turn of the conversation for a context pack; the assistant's turns carry the topic they asked about."""
    out = {"id": u.id, "role": u.role, "text": u.text, "translation": u.translation}
    if u.role == "assistant" and u.intent:
        out["topic"] = u.intent
    return out


async def family_turn(deps: Any, case_id: str, text: str, *, person_id: str | None = None, lang: str | None = None,
                      channel: str = "text", seconds: int | None = None, translation: str | None = None) -> dict:
    """translation: an English translation that came with the words (a ready-made question), checked by the
    listener rather than trusted."""
    case = await deps.cases.get_case(case_id)
    history = await _utterances(deps, case_id)
    base = {"case": case_block(case)}
    n_family = sum(1 for u in history if u.role == "family")
    pack = {"key": pack_key("listener", case, str(n_family + 1)), **base, "text": text, "lang_hint": lang,
            "channel": channel}
    if translation:
        pack["translation_hint"] = translation
    heard = await call_agent(deps, "listener", pack, case_id=case_id)
    u = Utterance(id=new_id("ut"), case_id=case_id, person_id=person_id, role="family", lang=heard.lang,
                  text=heard.text, translation=heard.translation if heard.lang != "en" else None, channel=channel,  # type: ignore[arg-type]
                  at=deps.now(), intent=heard.intent, seconds=seconds if channel == "voice" else None)
    await deps.cases.put(case_id, "utterances", u)
    history.append(u)
    if n_family == 0:
        loc = await call_agent(deps, "locale_detector", {"key": pack_key("locale_detector", case), **base,
                                                         "utterances": [{"text": x.text, "lang": x.lang} for x in history]},
                               case_id=case_id)
        case.languages = loc.languages or case.languages
    sit = await call_agent(deps, "situation_extractor", {
        "key": pack_key("situation_extractor", case, str(n_family + 1)), **base,
        "situation": case.situation.model_dump(mode="json") if case.situation else {},
        "utterances": [_said(x) for x in history]},
        case_id=case_id)
    await apply_situation(deps, case, sit, speaker_id=person_id, speaker_lang=heard.lang, channel=channel,
                          utterance_id=u.id)
    if await deps.cases.list(case_id, "claims"):   # a date or a will heard now can change a plan already made
        from .pipeline import refresh_claims
        await refresh_claims(deps, case_id)
    reply = await _next_question(deps, case, history, (case.languages or ["en"])[0])
    deps.changed(case_id, "utterances", "people")
    return {"heard": u, "reply": reply, "situation": case.situation}


async def apply_situation(deps: Any, case: CaseFile, sit: Any, *, speaker_id: str | None = None,
                          speaker_lang: str | None = None, channel: str = "text", utterance_id: str | None = None) -> None:
    """Updates the case and its people from the situation model."""
    from ..domain.calendars import parse_date
    model = SituationModel(**{k: v for k, v in sit.model_dump().items() if k in SituationModel.model_fields
                              and k not in ("family", "died_on", "deceased_name_en", "family_label",
                                            "speaker_relationship")},
                           family=[m.model_dump() for m in sit.family],
                           died_on=parse_date(sit.died_on) if sit.died_on else None)
    await _only_what_was_said(deps, case.id, model)
    case.situation = model
    if sit.deceased_name:
        case.title = sit.deceased_name
        case.title_en = sit.deceased_name_en or case.title_en
        latin = sit.deceased_name_en or (sit.deceased_name if script_of(sit.deceased_name) == "Latn" else None)
        case.family_label = sit.family_label or case.family_label or (
            f"The {latin.split()[-1]}s" if latin else f"{sit.deceased_name.split()[0]}家")
    if model.died_on:
        case.died_on = model.died_on
    case.died_in = sit.died_in or case.died_in
    case.country_codes = sit.country_codes or case.country_codes
    case.updated_at = deps.now()
    people = {p.id: p for p in await deps.cases.list(case.id, "people")}
    if sit.deceased_name:
        did = case.deceased_id or stable_id("p", case.id, "deceased")
        dp = people.get(did) or Person(id=did, case_id=case.id, display_name=sit.deceased_name, roles=["deceased"],
                                       initials=_initials(sit.deceased_name), color="#D9D2C5")
        dp.display_name = sit.deceased_name
        dp.name_en = sit.deceased_name_en or dp.name_en
        dp.death_date, dp.death_place = case.died_on, sit.died_in
        if not any(normalise(n.text) == normalise(sit.deceased_name) for n in dp.names):
            dp.names.append(NameForm(text=sit.deceased_name, script=script_of(sit.deceased_name), seen_on="family"))
        people[did] = dp
        case.deceased_id = did
    for m in sit.family:
        pid = _find(people, m.name, m.relationship) or stable_id("p", case.id, normalise(m.name))
        p = people.get(pid) or Person(id=pid, case_id=case.id, display_name=m.name, initials=_initials(m.name),
                                      color=COLORS[len(people) % len(COLORS)])
        p.relationship = m.relationship or p.relationship
        p.name_en = m.name_en or p.name_en
        p.city, p.country = m.city or p.city, m.country or p.country
        p.languages = m.languages or p.languages
        p.roles = sorted(set(p.roles) | set(m.roles))
        if not any(normalise(n.text) == normalise(m.name) for n in p.names):
            p.names.append(NameForm(text=m.name, script=script_of(m.name), seen_on="family"))
        people[pid] = p
    speaker = people.get(speaker_id) if speaker_id else None
    if speaker is not None and getattr(sit, "speaker_relationship", None) and not speaker.relationship:
        speaker.relationship = sit.speaker_relationship
        if sit.speaker_relationship in HEIR_WORDS:
            speaker.roles = sorted(set(speaker.roles) | {"heir"})  # type: ignore[arg-type]
        if speaker_lang and speaker_lang not in speaker.languages:
            speaker.languages = [speaker_lang, *speaker.languages]
        speaker.channel = channel  # type: ignore[assignment]
    if speaker_id is None and case.viewer_person_id is None:
        speaker = next((p for p in people.values() if "claimant" in p.roles or p.relationship in ("wife", "husband")),
                       None)
        if speaker is not None:
            case.viewer_person_id = speaker.id
            speaker.channel = channel  # type: ignore[assignment]
            if speaker_lang and speaker_lang not in speaker.languages:
                speaker.languages = [speaker_lang, *speaker.languages]
    await deps.cases.put_many(case.id, "people", list(people.values()))
    await deps.cases.put_case(case)


# Words a family uses for a will, or for a quarrel over the estate, in the app's languages (and in the listener's
# English translation of every turn)
_WILL_WORDS = re.compile(r"\bwills?\b|testament|wasiyat|वसीयत|遺言|testamento|وصي|ഒസ്യത്ത്|വിൽപത്ര", re.I)
_DISPUTE_WORDS = re.compile(r"disput|disagree|quarrel|argu|contest|court|lawsuit|विवाद|झगड़|争い|揉め|"
                            r"disputa|pelea|نزاع|خلاف|തർക്ക|Streit", re.I)


async def _only_what_was_said(deps: Any, case_id: str, model: SituationModel) -> None:
    """A will or a dispute changes a family's route (to probate, to a court), so the situation keeps "yes" only when
    the family spoke of one: a live model set will = yes for the Vermas, who never mentioned a will. Without a word
    about it the answer is unknown, and the plan asks."""
    if model.will != "yes" and model.dispute != "yes":
        return
    said = " ".join(f"{u.text} {u.translation or ''}" for u in await deps.cases.list(case_id, "utterances")
                    if u.role == "family")
    if model.will == "yes" and not _WILL_WORDS.search(said):
        model.will = "unknown"
    if model.dispute == "yes" and not _DISPUTE_WORDS.search(said):
        model.dispute = "unknown"


def _find(people: dict[str, Person], name: str, relationship: str | None) -> str | None:
    for p in people.values():
        if normalise(p.display_name) == normalise(name):
            return p.id
    for p in people.values():
        if relationship and p.relationship == relationship and relationship not in ("son", "daughter", "brother", "sister"):
            return p.id
    return None


def _initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").split() if p]
    if not parts:
        return "?"
    if script_of(name) != "Latn":
        return name[:1]
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


async def _offer_summary(deps: Any, case: CaseFile) -> None:
    sit = case.situation or SituationModel()
    people = await deps.cases.list(case.id, "people")
    family = [p for p in people if p.id != case.deceased_id]
    lines = []
    if sit.deceased_name:
        when = f" on {case.died_on.day} {case.died_on.strftime('%B %Y')}" if case.died_on else ""
        lines.append(f"{sit.deceased_name} died{when}{' in ' + sit.died_in if sit.died_in else ''}.")
    for p in family:
        lines.append(f"{p.display_name}, {p.relationship or 'family'}{', in ' + p.city if p.city else ''}.")
    if sit.holdings_mentioned:
        lines.append("Money mentioned: " + ", ".join(sit.holdings_mentioned) + ".")
    viewer = case.viewer_person_id
    for cp in await deps.cases.list(case.id, "checkpoints"):
        if cp.code == "C1" and cp.status == "answered" and cp.answer == "change":   # offered again after a correction
            cp.status, cp.answer, cp.answered_by, cp.answered_at = "open", None, None, None
            await deps.cases.put(case.id, "checkpoints", cp)
    await open_checkpoint(deps, case, code="C1", kind="summary", title="Is this right?",
                          question="Here is what we understood. Is it right?", options=[("yes", "Yes, this is right"),
                                                                                        ("change", "Change something")],
                          for_person_id=viewer, subject_ref=f"case:{case.id}:summary",
                          why="So that everything that follows starts from the right facts.",
                          payload={"lines": lines, "urgent": sit.urgent})
    await deps.activity(case.id, MODULE, "The summary is ready for the family to confirm.", kind="question")


async def change_summary(deps: Any, case_id: str, note: str, *, by: str | None) -> dict:
    """The family said what is wrong with the summary. Their words are heard like anything else they say, so the
    situation is corrected from them; the summary is set aside meanwhile, and offered again, rebuilt, once nothing
    more needs asking."""
    for cp in await deps.cases.list(case_id, "checkpoints"):
        if cp.code == "C1" and cp.status == "open":
            cp.status, cp.answer, cp.answer_note, cp.answered_by, cp.answered_at = "answered", "change", note, by, deps.now()
            await deps.cases.put(case_id, "checkpoints", cp)
    await deps.activity(case_id, MODULE, f"The family asked for a change to the summary: “{note}”", kind="question")
    return await family_turn(deps, case_id, note, person_id=by)


async def confirm_summary(deps: Any, case_id: str, *, by: str | None, answer: str = "yes") -> CaseFile:
    case = await deps.cases.get_case(case_id)
    for cp in await deps.cases.list(case_id, "checkpoints"):
        if cp.code == "C1" and cp.status == "open":
            cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", answer, by, deps.now()
            await deps.cases.put(case_id, "checkpoints", cp)
    if answer == "yes":
        case.stage = "papers"
        await deps.cases.put_case(case)
        await deps.activity(case_id, MODULE, "The family confirmed the summary.", kind="decision")
    deps.changed(case_id, "checkpoints")
    return case
