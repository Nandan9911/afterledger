"""Plays a demo family's story through the app's own services.

Nothing is written into the case directly: papers are uploaded and read, leads are found by the
discovery agents and marked by the family, plans are built and approved, packs are filled and read
back, and claim events are recorded, each on its day in the story. While an event plays, 'now' is
that moment in the story (runtime.STORY_NOW), so every activity line, approval and clock carries the
date it would have had. The finished case is the one the app would hold if the family had used it.

Two modes:
  as_it_stands  plays the whole timeline at once (what the demo opens on);
  watch         plays it with a pause between events, so the activity feed can be watched live.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..domain.checks import normalise
from ..health import ServiceFailure
from ..runtime import STORY_NOW
from .families import Family, family_keys, load_family

log = logging.getLogger(__name__)

DEMO_PREFIX = "demo-"


def demo_case_id(key: str) -> str:
    return f"{DEMO_PREFIX}{key}"


def _relation(text: str | None) -> str | None:
    t = (text or "").strip().lower()
    return "spouse" if t in ("wife", "husband", "spouse") else t or None


class SeedError(RuntimeError):
    pass


@dataclass
class SeedReport:
    key: str
    case_id: str
    story_day: date
    events: int = 0
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0
    model_calls: int = 0

    def as_dict(self) -> dict:
        return {"key": self.key, "case_id": self.case_id, "story_day": self.story_day.isoformat(),
                "events": self.events, "warnings": self.warnings, "seconds": round(self.seconds, 1),
                "model_calls": self.model_calls}


@dataclass
class _Run:
    deps: Any
    fam: Family
    case_id: str
    report: SeedReport
    people: dict[str, str] = field(default_factory=dict)    # truth person id -> case person id
    claims: dict[str, str] = field(default_factory=dict)    # truth claim id -> case claim id
    leads: dict[str, str] = field(default_factory=dict)     # truth lead key -> case lead id
    papers: dict[str, str] = field(default_factory=dict)    # truth paper key -> case paper id

    def warn(self, text: str) -> None:
        log.warning("seed %s: %s", self.fam.key, text)
        self.report.warnings.append(text)

    # ---- mapping the truth file's ids onto the case's own ------------------------------------
    async def person_id(self, truth_id: str | None) -> str | None:
        if not truth_id:
            return None
        if truth_id in self.people:
            return self.people[truth_id]
        case = await self.deps.cases.get_case(self.case_id)
        if truth_id == self.fam.deceased["id"]:
            return case.deceased_id
        tp = self.fam.person(truth_id)
        if tp is None:
            return None
        names = {normalise(tp["display_name"])} | {normalise(n["text"]) for n in tp.get("names", [])}
        people = [p for p in await self.deps.cases.list(self.case_id, "people") if p.id != case.deceased_id]
        for p in people:
            if normalise(p.display_name) in names or any(normalise(n.text) in names for n in p.names):
                self.people[truth_id] = p.id
                return p.id
        # A live model may keep only what the family said ("अमित", for "my son Amit"): part of one of the person's
        # names, with the same relationship, when only one person in the case fits.
        from ..domain.names import tokens
        whole = [set(tokens(n)) for n in [tp["display_name"], *(n["text"] for n in tp.get("names", []))]]
        rel = _relation(tp.get("relationship"))
        fits = [p for p in people if rel and _relation(p.relationship) == rel and
                any(part and any(part <= w for w in whole)
                    for part in [set(tokens(n.text)) for n in p.names] + [set(tokens(p.display_name))])]
        if len(fits) == 1:
            self.people[truth_id] = fits[0].id
            return fits[0].id
        # ...or only in a script the story's file does not spell them in ("അർജുൻ" for Arjun Nair): the one person
        # with that relationship, when no name of theirs can be compared with the file's
        from ..domain.names import script_of
        scripts = {script_of(n) for n in [tp["display_name"], *(n["text"] for n in tp.get("names", []))]}
        same = [p for p in people if rel and _relation(p.relationship) == rel]
        if len(same) == 1 and not ({script_of(n.text) for n in same[0].names} | {script_of(same[0].display_name)}) \
                & scripts:
            self.people[truth_id] = same[0].id
            return same[0].id
        return None

    async def claim_id(self, truth_id: str) -> str | None:
        if truth_id in self.claims:
            return self.claims[truth_id]
        tc = self.fam.claim(truth_id)
        if tc is None:
            return None
        want = tc["plan"].split("@")[0]
        masks = {(self.fam.holding(h) or {}).get("account_mask") for h in tc.get("holdings", [])} - {None}
        kinds = {(self.fam.holding(h) or {}).get("kind") for h in tc.get("holdings", [])} - {None}
        cands = []
        at_institution = [c for c in await self.deps.cases.list(self.case_id, "claims")
                          if c.institution_id == tc["institution_id"]]
        for c in at_institution:
            key = c.plan_key
            if not key and c.plan_id:
                cp = await self.deps.cases.get(self.case_id, "plans", c.plan_id)
                key = cp.plan_key if cp else None
            key = key or self.deps.extras.get("claim_plan_key", {}).get(c.id)
            if key and key.split("@")[0] == want:
                cands.append(c)
        if not cands:
            # a live run may plan the claim under a plan it researched for that institution (IN/pension@in_up_treasury
            # where the truth names IN/family_pension): the one claim at the institution stands for it
            others = [c for c in at_institution if c.kind != "procedure"]
            cands = others if len(others) == 1 else []
        if len(cands) > 1 and (masks or kinds):
            # two claims at one bank (with and without a nominee): the truth's own holdings decide
            def score(c: Any) -> int:
                hs = [h for h in holdings if h.id in c.holding_ids]
                return sum(2 for h in hs if h.account_mask in masks) + sum(1 for h in hs if h.kind in kinds)
            holdings = await self.deps.cases.list(self.case_id, "holdings")
            cands.sort(key=score, reverse=True)
        if cands:
            self.claims[truth_id] = cands[0].id
            return cands[0].id
        return None

    async def lead_id(self, key: str) -> str | None:
        if key in self.leads:
            return self.leads[key]
        tl = self.fam.lead(key)
        if tl is None:
            return None
        leads = await self.deps.cases.list(self.case_id, "leads")
        # the lead resting on the payments the truth's lead names, first: a live model words a category its own way
        # (PMJJBY as government_cover, not life_cover), but the payments are the same
        on_group: list = []
        if tl.get("group"):
            from ..modules.discovery import narration_key
            from ..oracles.discovery import truth_groups
            tg = truth_groups(self.fam)
            mapped = {g.id: tg.get(narration_key(g.label)) for g in await self.deps.cases.list(self.case_id, "groups")}
            on_group = sorted((x for x in leads if tl["group"] in {mapped.get(g) for g in x.group_ids}),
                              key=lambda x: (x.kind != tl.get("kind"), x.category != tl["category"]))
        same = [x for x in leads if x.category == tl["category"]]
        exact = [x for x in same if x.institution_id == tl.get("institution_id")]
        pick = (on_group or exact or same or [None])[0]
        if pick is not None:
            self.leads[key] = pick.id
            return pick.id
        return None


Handler = Callable[[_Run, dict, date], Awaitable[None]]
_HANDLERS: dict[str, Handler] = {}


def _on(name: str):
    def deco(fn: Handler) -> Handler:
        _HANDLERS[name] = fn
        return fn
    return deco


# ---------------------------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------------------------

@_on("intake")
async def _intake(run: _Run, ev: dict, on: date) -> None:
    from ..modules import intake
    fam, deps = run.fam, run.deps
    talk = fam.get("intake") or {}
    turns = talk.get("turns", [])
    first = next((t for t in turns if t["role"] == "assistant"), None)
    lang = (first or {}).get("lang") or fam.get("languages", ["en"])[0]
    # the person telling the story types their name when they start, as in the app, so their turns are theirs even
    # when they never say it (a live model cannot know Daisuke's name from "my father died")
    teller = fam.person(talk["by"]) if talk.get("by") else None
    case, _ = await intake.start_case(deps, lang=lang, demo_key=fam.key, story_day=fam.story_day, case_id=run.case_id,
                                      mode=talk.get("mode", "family"),
                                      starter_name=(teller or {}).get("display_name"))
    if teller is not None and case.viewer_person_id:
        run.people[talk["by"]] = case.viewer_person_id
    n = 0
    for t in turns:
        if t["role"] != "family":
            continue
        n += 1
        STORY_NOW.set(STORY_NOW.get() + timedelta(seconds=(t.get("seconds") or 20) + 30))
        await intake.family_turn(deps, run.case_id, t["text"], person_id=case.viewer_person_id, lang=t.get("lang"),
                                 channel=t.get("channel", "text"), seconds=t.get("seconds"))
    speaker = await run.person_id(talk.get("by"))
    if speaker:
        for u in await deps.cases.list(run.case_id, "utterances"):
            if u.role == "family" and u.person_id is None:
                u.person_id = speaker
                await deps.cases.put(run.case_id, "utterances", u)
        p = await deps.cases.get(run.case_id, "people", speaker)
        if p is not None:
            p.access, p.joined_on = "owner", on
            await deps.cases.put(run.case_id, "people", p)
    if n == 0:
        run.warn("the intake has no family turns")


@_on("confirm_summary")
async def _confirm_summary(run: _Run, ev: dict, on: date) -> None:
    from ..modules import intake
    await intake.confirm_summary(run.deps, run.case_id, by=await run.person_id(ev.get("by")))


@_on("invite")
async def _invite(run: _Run, ev: dict, on: date) -> None:
    from ..modules import people
    pid = await run.person_id(ev["person"])
    if pid is None:
        run.warn(f"invite: nobody in the case matches {ev['person']}")
        return
    await people.invite_person(run.deps, run.case_id, pid, by=await run.person_id(ev.get("by")))


@_on("add_person")
async def _add_person(run: _Run, ev: dict, on: date) -> None:
    from ..modules import people
    tp = run.fam.person(ev["person"])
    if tp is None:
        raise SeedError(f"add_person: {ev['person']} is not in the family file")
    p = await people.add_person(run.deps, run.case_id, display_name=tp["display_name"],
                                relationship=tp.get("relationship"), roles=tp.get("roles", []),
                                languages=tp.get("languages", []), city=tp.get("city"), country=tp.get("country"),
                                notes=tp.get("notes", ""), by=await run.person_id(ev.get("by")))
    run.people[ev["person"]] = p.id
    await _apply_person_details(run, ev["person"], p.id)


async def _apply_person_details(run: _Run, truth_id: str, pid: str) -> None:
    """Details the family types on the People screen: age, initials, colour, languages."""
    from ..modules import people
    tp = run.fam.person(truth_id) or {}
    changes = {k: tp.get(k) for k in ("age", "initials", "color", "languages", "city", "country", "relationship",
                                      "address", "birth_date") if tp.get(k) is not None}
    if tp.get("display_name_en"):
        changes["name_en"] = tp["display_name_en"]
    for n in tp.get("names", []):          # readings (furigana) the family confirms on the People screen
        if n.get("reading"):
            current_p = await run.deps.cases.get(run.case_id, "people", pid)
            if current_p is not None:
                from ..domain.case import NameForm
                names = list(current_p.names)
                hit = next((x for x in names if x.text == n["text"]), None)
                if hit is not None and not hit.reading:
                    hit.reading = n["reading"]
                elif hit is None:
                    names.append(NameForm(text=n["text"], script=n.get("script", "Latn"), reading=n["reading"],
                                          seen_on="family"))
                changes["names"] = [x.model_dump() for x in names]
    if tp.get("channel"):
        changes["channel"] = tp["channel"]
    current = await run.deps.cases.get(run.case_id, "people", pid)
    if current is not None:
        changes["roles"] = sorted(set(current.roles) | set(tp.get("roles", [])))
    await people.update_person(run.deps, run.case_id, pid, changes)


@_on("upload")
async def _upload(run: _Run, ev: dict, on: date) -> None:
    await _upload_many(run, [ev], on)


async def _upload_many(run: _Run, evs: list[dict], on: date) -> None:
    from ..modules import papers, pipeline
    ids = []
    for ev in evs:
        by = await run.person_id(ev.get("by") or run.fam.get("viewer"))
        paper = await papers.add_demo_paper(run.deps, run.case_id, run.fam, ev["paper"], uploaded_by=by)
        run.papers[ev["paper"]] = paper.id
        ids.append(paper.id)
        STORY_NOW.set(STORY_NOW.get() + timedelta(minutes=2))
    await pipeline.after_papers(run.deps, run.case_id, ids)


@_on("mark_leads")
async def _mark_leads(run: _Run, ev: dict, on: date) -> None:
    from ..modules import discovery, pipeline
    by = await run.person_id(ev.get("by"))
    changed = False
    for tl in run.fam.get("leads", []):
        lid = await run.lead_id(tl["key"])
        if lid is None:
            if tl.get("status") != "closed":
                run.warn(f"mark_leads: discovery did not find the lead '{tl['key']}'")
            continue
        lead = await run.deps.cases.get(run.case_id, "leads", lid)
        if lead.status == "closed" or lead.family_mark:
            continue
        if tl.get("status") == "confirmed":
            await discovery.mark_lead(run.deps, run.case_id, lid, "know", by=by,
                                      value=tl.get("family_value") or tl.get("value"), note=tl.get("family_note"))
            changed = True
        elif tl.get("family_value"):
            await discovery.mark_lead(run.deps, run.case_id, lid, "check", by=by, value=tl["family_value"],
                                      note=tl.get("family_note"))
            changed = True
    if changed:
        await pipeline.refresh_claims(run.deps, run.case_id)


@_on("answer_values")
async def _answer_values(run: _Run, ev: dict, on: date) -> None:
    from ..modules import reading
    by = await run.person_id(ev.get("by"))
    corrections = run.fam.get("value_answers") or {}
    for cp in await run.deps.cases.list(run.case_id, "checkpoints"):
        if cp.code != "C2" or cp.status != "open":
            continue
        vid = (cp.payload or {}).get("value_id")
        label = (cp.payload or {}).get("label", "")
        fix = corrections.get(label)
        if fix:
            await reading.answer_value_check(run.deps, run.case_id, vid, "no", by=by, corrected=str(fix))
        else:
            await reading.answer_value_check(run.deps, run.case_id, vid, "yes", by=by)


@_on("confirm_people")
async def _confirm_people(run: _Run, ev: dict, on: date) -> None:
    from ..modules import people
    for tp in run.fam.people:
        if tp.get("unconfirmed"):
            continue                      # the family has not said who this is yet
        pid = await run.person_id(tp["id"])
        if pid:
            await _apply_person_details(run, tp["id"], pid)
    await people.confirm_people(run.deps, run.case_id, by=await run.person_id(ev.get("by")))


async def _claim(run: _Run, truth_id: str) -> str:
    from ..modules import pipeline
    cid = await run.claim_id(truth_id)
    if cid is None:
        await pipeline.refresh_claims(run.deps, run.case_id)
        cid = await run.claim_id(truth_id)
    if cid is None:
        raise SeedError(f"no claim in the case matches '{truth_id}'")
    return cid


@_on("approve_plan")
async def _approve_plan(run: _Run, ev: dict, on: date) -> None:
    from ..modules import planning
    deps = run.deps
    cid = await _claim(run, ev["claim"])
    tc = run.fam.claim(ev["claim"]) or {}
    by = await run.person_id(ev.get("by"))
    claim = await deps.cases.get(run.case_id, "claims", cid)
    if not claim.plan_id:
        await planning.plan_claim(deps, run.case_id, cid)
        claim = await deps.cases.get(run.case_id, "claims", cid)
    for _ in range(6):                    # each answer can open the next question
        open_q = [c for c in await deps.cases.list(run.case_id, "checkpoints")
                  if c.status == "open" and c.kind == "plan_question" and (c.payload or {}).get("claim_id") == cid]
        if not open_q:
            break
        for c in open_q:
            var = c.payload["variable"]
            value = (tc.get("facts") or {}).get(var, run.fam.answer(var))
            if value is None:
                run.warn(f"approve_plan {ev['claim']}: no answer for '{var}' in the family file")
                continue
            await planning.answer_question(deps, run.case_id, claim.plan_id, var, value, by=by)
    cp = await deps.cases.get(run.case_id, "plans", claim.plan_id)
    unanswered = [q for q in cp.questions if not any(
        cp.facts.get(f"__asked__{v.name}") for v in ((await _plan(run, cp.plan_key)).variables if cp.plan_key else [])
        if v.question == q)]
    if unanswered:
        run.warn(f"approve_plan {ev['claim']}: questions still open: {unanswered}")
    await planning.approve_plan(deps, run.case_id, cp.id, by=by)
    # procedures this claim needs (an affidavit, a certificate) get the family's answers and approval too
    for proc in await deps.cases.list(run.case_id, "claims"):
        if proc.kind == "procedure" and cid in proc.needed_by and proc.plan_id:
            for c in await deps.cases.list(run.case_id, "checkpoints"):
                if c.status == "open" and c.kind == "plan_question" and (c.payload or {}).get("claim_id") == proc.id:
                    var = c.payload["variable"]
                    value = run.fam.answer(var)
                    if value is not None:
                        await planning.answer_question(deps, run.case_id, proc.plan_id, var, value, by=by)
            pcp = await deps.cases.get(run.case_id, "plans", proc.plan_id)
            if pcp and not pcp.approved_by:
                await planning.approve_plan(deps, run.case_id, pcp.id, by=by)
    cp = await deps.cases.get(run.case_id, "plans", claim.plan_id)
    if tc.get("expected_route") is not None:
        from ..domain.plan_runtime import route as plan_route
        from ..modules.common import knowledge
        plan = await knowledge(deps).plan(cp.plan_key)
        got = plan_route(plan, {k: v for k, v in cp.facts.items() if not k.startswith("__")}) if plan else []
        if sorted(got) != sorted(tc["expected_route"]):
            run.warn(f"approve_plan {ev['claim']}: route {got} differs from the expected {tc['expected_route']}")


async def _plan(run: _Run, key: str):
    from ..modules.common import knowledge
    return await knowledge(run.deps).plan(key)


@_on("prepare_pack")
async def _prepare_pack(run: _Run, ev: dict, on: date) -> None:
    from ..modules import packs
    cid = await _claim(run, ev["claim"])
    pack = await packs.prepare_pack(run.deps, run.case_id, cid)
    if pack is None:
        raise SeedError(f"prepare_pack: no pack could be made for '{ev['claim']}'")


@_on("approve_pack")
async def _approve_pack(run: _Run, ev: dict, on: date) -> None:
    from ..modules import packs
    cid = await _claim(run, ev["claim"])
    pack = await packs.prepare_pack(run.deps, run.case_id, cid)
    if pack is None:
        raise SeedError(f"approve_pack: no pack could be made for '{ev['claim']}'")
    await packs.approve_pack(run.deps, run.case_id, pack.id, by=await run.person_id(ev.get("by")))


@_on("claim_event")
async def _claim_event(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup
    cid = await _claim(run, ev["claim"])
    happened = ev.get("on_event")             # an event recorded later than the day it happened
    when = date.fromisoformat(happened) if isinstance(happened, str) else (happened or on)
    await followup.record_claim_event(run.deps, run.case_id, cid, ev["event"], when, amount=ev.get("amount"),
                                      note=ev.get("note"), by=await run.person_id(ev.get("by")))


@_on("start_procedure")
async def _start_procedure(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup, planning
    by = await run.person_id(ev.get("by"))
    c = await planning.start_procedure(run.deps, run.case_id, ev["plan"], by=by)
    if c is None:
        raise SeedError(f"start_procedure: no plan {ev['plan']}")
    if ev.get("claim"):
        run.claims[ev["claim"]] = c.id
    cp = await run.deps.cases.get(run.case_id, "plans", c.plan_id) if c.plan_id else None
    if cp and not cp.approved_by:
        await planning.approve_plan(run.deps, run.case_id, cp.id, by=by)
    if ev.get("event"):
        await followup.record_claim_event(run.deps, run.case_id, c.id, ev["event"], on, note=ev.get("note"), by=by)


@_on("correspondence")
async def _correspondence(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup
    cid = await _claim(run, ev["claim"]) if ev.get("claim") else None
    exp = ev.get("expected_on")
    await followup.add_correspondence(run.deps, run.case_id, claim_id=cid, direction=ev.get("direction", "in"),
                                      channel=ev.get("channel", "letter"), title=ev["title"],
                                      summary=ev.get("summary", ""), on=on,
                                      expected_on=date.fromisoformat(exp) if isinstance(exp, str) else exp,
                                      expected_title=ev.get("expected_title"), expected_note=ev.get("expected_note"))


@_on("reply")
async def _reply(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup
    cid = await _claim(run, ev["claim"])
    await followup.record_reply(run.deps, run.case_id, cid, title=ev["title"], text=ev.get("text", ""), on=on,
                                channel=ev.get("channel", "letter"))


@_on("draft_letter")
async def _draft_letter(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup
    cid = await _claim(run, ev["claim"])
    await followup.draft_letter(run.deps, run.case_id, cid, ev.get("kind", "reminder"),
                                by=await run.person_id(ev.get("by")), request=ev.get("request"))


@_on("ask")
async def _ask(run: _Run, ev: dict, on: date) -> None:
    from ..modules import conversation
    await conversation.ask(run.deps, run.case_id, ev["text"], person_id=await run.person_id(ev.get("by")),
                           lang=ev.get("lang"), channel=ev.get("channel", "text"))


@_on("today")
async def _today(run: _Run, ev: dict, on: date) -> None:
    from ..modules import followup
    deps = run.deps
    case = await deps.cases.get_case(run.case_id)
    viewer = await run.person_id(ev.get("viewer") or run.fam.get("viewer"))
    if viewer:
        case.viewer_person_id = viewer
        await deps.cases.put_case(case)
    # every family member gets a greeting of their own; the default viewer's is planned last, so it is the case's
    from ..modules.common import snapshot
    for person in (await snapshot(deps, run.case_id, ["people"])).family():
        if person.id != viewer and person.languages:
            await followup.plan_today(deps, run.case_id, person.id)
    await followup.plan_today(deps, run.case_id, viewer)


# ---------------------------------------------------------------------------------------------
# playing a timeline
# ---------------------------------------------------------------------------------------------

def _when(ev: dict) -> date:
    on = ev["on"]
    return on if isinstance(on, date) else date.fromisoformat(str(on))


async def _mixed_replay(run: _Run, fallbacks: int) -> None:
    """A replay whose cassettes did not cover every call was partly answered by the offline oracles, which know
    the family's truth: the case is then labelled scripted, so the evaluation does not measure the agents on it."""
    if fallbacks <= 0:
        return
    deps, case_id = run.deps, run.case_id
    run.warn(f"{fallbacks} model call(s) had no recorded response and used scripted responses")
    case = await deps.cases.get_case(case_id)
    if case is None:
        return
    case.model_mode = "scripted"
    await deps.cases.put_case(case)
    await deps.activity(case_id, "seed", f"{fallbacks} of the agents' calls on this replay had no recorded response "
                        "and used scripted responses instead, so this case is labelled scripted and is not used to "
                        "measure the agents.", kind="warning")


async def seed_family(deps: Any, key: str, *, story_day: date | None = None, mode: str = "as_it_stands",
                      delay: float = 0.0, until: date | None = None,
                      on_event: Callable[[dict], Awaitable[None] | None] | None = None) -> SeedReport:
    """Builds (or rebuilds) the demo case for one family by playing its timeline."""
    fam = load_family(key, story_day)
    case_id = demo_case_id(key)
    report = SeedReport(key=key, case_id=case_id, story_day=fam.story_day)
    started = _time.time()
    calls_before = getattr(deps.models, "calls", 0)
    fallbacks_before = getattr(deps.models, "fallbacks", 0)
    await deps.cases.delete_case(case_id)
    drop = getattr(deps.blobs, "delete_prefix", None)     # Cloud Storage keeps files a store delete leaves
    if drop is not None:
        await drop(f"cases/{case_id}/")
    run = _Run(deps=deps, fam=fam, case_id=case_id, report=report)
    # Scripted responses must play every step exactly (a failure is a bug); a live model may not, and then the
    # replay goes on with a warning rather than stopping.
    strict = getattr(deps.models, "mode", "scripted") == "scripted"
    events = sorted(enumerate(fam.get("timeline", [])), key=lambda x: (_when(x[1]), x[0]))
    pause = delay if mode == "watch" else 0.0
    tz = ZoneInfo(fam.get("timezone") or "UTC")      # the story happens on the family's own clock
    token = STORY_NOW.set(None)
    try:
        i = 0
        per_day: dict[date, int] = {}
        while i < len(events):
            _, ev = events[i]
            on = _when(ev)
            if until and on > until:
                break
            slot = per_day.get(on, 0)
            per_day[on] = slot + 1
            STORY_NOW.set(datetime.combine(on, time(9, 0), tzinfo=tz) + timedelta(minutes=25 * slot))
            batch = [ev]
            if ev["do"] == "upload":      # papers uploaded together are read together
                while i + 1 < len(events) and events[i + 1][1]["do"] == "upload" and _when(events[i + 1][1]) == on:
                    i += 1
                    batch.append(events[i][1])
            handler = _HANDLERS.get(ev["do"])
            if handler is None:
                raise SeedError(f"unknown timeline event '{ev['do']}'")
            try:
                if ev["do"] == "upload":
                    await _upload_many(run, batch, on)
                else:
                    await handler(run, ev, on)
            except Exception as exc:
                if strict or isinstance(exc, ServiceFailure):     # a strict live run stops at a failing service
                    raise
                # A live model can take the story another way (another claim, another question): the replay
                # carries on from where the case is, and says what could not be done.
                run.warn(f"{ev['do']} on {on.isoformat()}: {exc}")
                if await deps.cases.get_case(case_id) is not None:
                    await deps.activity(case_id, "seed", f"The story's step “{ev['do'].replace('_', ' ')}” could not be "
                                        f"played on this run: {str(exc)[:200]}", kind="warning")
            report.events += len(batch)
            if mode == "watch":           # the pages show the day the replay has reached
                case = await deps.cases.get_case(case_id)
                if case is not None and case.replay_on != on:
                    case.replay_on = on
                    await deps.cases.put_case(case)
            if on_event:
                res = on_event({"key": key, "case_id": case_id, "on": on.isoformat(), "do": ev["do"],
                                "detail": {k: v for k, v in ev.items() if k not in ("on", "do")}})
                if asyncio.iscoroutine(res):
                    await res
            if pause:
                await asyncio.sleep(pause)
            i += 1
        await _mixed_replay(run, getattr(deps.models, "fallbacks", 0) - fallbacks_before)
        if not until or until >= fam.story_day:
            STORY_NOW.set(datetime.combine(fam.story_day, time(8, 30), tzinfo=tz))
            case = await deps.cases.get_case(case_id)
            if case is not None and case.replay_on is not None:
                case.replay_on = None
                await deps.cases.put_case(case)
            if case is not None and not any(e["do"] == "today" for _, e in events):
                await _today(run, {}, fam.story_day)
            if case is not None:
                await deps.activity(case_id, "seed", "Demo case ready: the story as it stands on "
                                    f"{fam.story_day.day} {fam.story_day.strftime('%B %Y')}. Every family, paper and "
                                    "institution in it is fictional; the rules applied are real.", kind="step")
    finally:
        STORY_NOW.reset(token)
    report.seconds = _time.time() - started
    report.model_calls = getattr(deps.models, "calls", 0) - calls_before
    return report


async def seed_all(deps: Any, keys: list[str] | None = None, *, story_day: date | None = None) -> list[SeedReport]:
    out = []
    for key in keys or family_keys():
        out.append(await seed_family(deps, key, story_day=story_day))
    return out
