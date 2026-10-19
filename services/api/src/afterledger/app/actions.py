"""What the family can do, as use cases: each runs the module that owns the change and then the
follow-on steps that keep the case consistent (a lead marked changes the claims, an approved plan
gets its papers prepared).

An action holds the case only for its own short work, and the follow-on steps that wait on a model run in the
background (concurrency.py): an answer, an approval or a recorded payment returns at once, and the plans, papers and
Today follow, with the case's header saying what is being worked out. Actions that are themselves model work (a
question asked, a letter drafted, a reply read) run without holding the case, and their writes are merged like a
background job's."""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..concurrency import CaseLock, job, working
from ..domain.case import Checkpoint
from ..domain.ids import new_id
from ..domain.money import format_amount
from ..modules import (
    conversation,
    discovery,
    followup,
    intake,
    packs,
    papers,
    people,
    pipeline,
    planning,
    reading,
)
from ..modules.common import case_lock as common_case_lock
from ..modules.common import knowledge

log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class BadInput(Exception):
    """Something the family typed that cannot be used as it is: said back in plain words (422)."""


def amount_typed(text: str | None) -> str | None:
    """An amount as a person types it ("1,20,000", "₹ 85,000.50", "EUR 1.200") to a plain decimal, or None when
    nothing was typed. Separators are read the way the text uses them: a single comma or point followed by exactly
    three digits groups thousands; otherwise the last one marks the decimals."""
    import re
    from decimal import Decimal, InvalidOperation
    if text is None or not text.strip():
        return None
    raw = re.sub(r"[^\d.,]", "", text)
    if not raw or not re.search(r"\d", raw):
        raise BadInput("Write the amount as a number, for example 120000 or 1,20,000.")
    seps = [m.start() for m in re.finditer(r"[.,]", raw)]
    if seps:
        last = seps[-1]
        tail = raw[last + 1:]
        grouping = len(tail) == 3 and (len(seps) == 1 or raw[seps[0]] == raw[last])
        if grouping:
            raw = re.sub(r"[.,]", "", raw)
        else:
            raw = re.sub(r"[.,]", "", raw[:last]) + "." + tail
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise BadInput("Write the amount as a number, for example 120000 or 1,20,000.") from None
    if value < 0:
        raise BadInput("The amount cannot be negative.")
    return str(value)


# Uploads taken in at the same time on one server: each holds its files in memory while its pages are drawn and
# stored, so a crowd of large uploads waits its turn instead of running the server out of memory.
UPLOADS_AT_ONCE = 3


def _uploads(deps: Any) -> asyncio.Semaphore:
    return deps.extras.setdefault("uploads", asyncio.Semaphore(UPLOADS_AT_ONCE))


async def _queue(deps: Any, case_id: str, kind: str, payload: dict, step: str) -> None:
    """A background job queued with the case's header saying at once what is being worked out, so a screen
    refetched straight after the action does not look finished while the job waits to start."""
    key = new_id("work")
    working(deps, case_id, key, step, queued=True)
    try:
        await deps.tasks.enqueue(kind, {**payload, "work": key})
    except Exception:
        working(deps, case_id, key, None)
        raise


def case_lock(deps: Any, case_id: str) -> CaseLock:
    return common_case_lock(deps, case_id)


async def refresh_clocks_if_idle(deps: Any, case_id: str) -> None:
    """Clocks move on with the calendar; they are brought up to date when a screen opens, unless the case
    is busy (then the job that holds it refreshes them when it finishes)."""
    lock = case_lock(deps, case_id)
    if lock.locked():
        return
    async with lock:
        await followup.refresh_clocks(deps, case_id)
        # research a restart cut short starts again (the claim still says it is being researched)
        from ..modules.rules_research import resume_interrupted
        await resume_interrupted(deps, case_id)


async def require_case(deps: Any, case_id: str) -> Any:
    case = await deps.cases.get_case(case_id)
    if case is None:
        raise NotFound(f"No case {case_id}")
    return case


# ---------------------------------------------------------------------------------------------
# background work
# ---------------------------------------------------------------------------------------------

def register_handlers(deps: Any) -> None:
    async def after_papers(payload: dict) -> None:
        # reading first, beside any other job (it takes minutes for a long statement, and writes only each paper's
        # own records); then what the papers lead to, one job at a time on the case. Neither holds the case while it
        # waits on a model: the family can talk, answer and approve meanwhile. The case's screens are told which
        # step is under way, so they never look finished while it runs.
        case_id, work = payload["case_id"], payload.get("work") or new_id("work")
        try:
            if payload["paper_ids"]:
                working(deps, case_id, work, "reading")
                async with job(deps, case_id, exclusive=False):
                    await pipeline.read_papers(deps, case_id, payload["paper_ids"])
            working(deps, case_id, work, "finding")
            async with job(deps, case_id):
                await pipeline.after_reading(deps, case_id)
                await followup.plan_today(deps, case_id)
        finally:
            working(deps, case_id, work, None)

    async def prepare_pack(payload: dict) -> None:
        case_id, work = payload["case_id"], payload.get("work") or new_id("work")
        try:
            working(deps, case_id, work, "preparing")
            async with job(deps, case_id):
                await packs.prepare_pack(deps, case_id, payload["claim_id"])
        finally:
            working(deps, case_id, work, None)
        await today_later(deps, case_id)

    async def plan_today(payload: dict) -> None:
        case_id = payload["case_id"]
        async with job(deps, case_id):
            # changes made while this waited its turn are in what it reads now: they need no plan of their own
            deps.extras.setdefault("today_pending", set()).discard(case_id)
            await followup.plan_today(deps, case_id, payload.get("viewer_id"))

    async def rework(payload: dict) -> None:
        """An answer, a lead, a person or an event worked into the claims and plans: the model work an action
        left to the background, so the action itself returned at once."""
        case_id, work = payload["case_id"], payload.get("work") or new_id("work")
        try:
            working(deps, case_id, work, "planning")
            async with job(deps, case_id):
                if payload.get("all"):
                    deps.extras.setdefault("rework_pending", set()).discard(case_id)
                answer = payload.get("answer")
                if answer:
                    await planning.apply_answer(deps, case_id, answer["plan_id"], answer["variable"], answer["value"])
                if payload.get("holdings"):
                    await discovery.holdings_from_papers(deps, case_id)
                if payload.get("all"):
                    await pipeline.refresh_claims(deps, case_id)
                for claim_id in payload.get("claims") or []:
                    claim = await deps.cases.get(case_id, "claims", claim_id)
                    if claim is None:
                        continue
                    await planning.plan_claim(deps, case_id, claim_id)
                    claim = await deps.cases.get(case_id, "claims", claim_id)
                    if claim is not None:
                        await followup.refresh_claim(deps, case_id, claim)
                deps.changed(case_id, "claims", "plans", "checkpoints")
        finally:
            working(deps, case_id, work, None)
        await today_later(deps, case_id)

    deps.tasks.register("after_papers", after_papers)
    deps.tasks.register("prepare_pack", prepare_pack)
    deps.tasks.register("plan_today", plan_today)
    deps.tasks.register("rework", rework)

    async def failed(kind: str, payload: dict, error: BaseException) -> None:
        """A background step that gave up says so on the case, instead of only in the server's log."""
        case_id = payload.get("case_id")
        if case_id and await deps.cases.get_case(case_id) is not None:
            await deps.activity(case_id, "system", f"A background step ({kind.replace('_', ' ')}) failed and was not "
                                f"finished: {type(error).__name__}. It can be run again.", kind="warning")
            deps.changed(case_id, "activity")

    if hasattr(deps.tasks, "on_failure"):
        deps.tasks.on_failure = failed


async def today_later(deps: Any, case_id: str) -> None:
    """Today is planned again after a change, in the background, a few seconds after the change (AL_TODAY_DEBOUNCE_S),
    once however many changes come in meanwhile."""
    pending = deps.extras.setdefault("today_pending", set())
    if case_id in pending:
        return
    pending.add(case_id)
    try:
        await deps.tasks.enqueue("plan_today", {"case_id": case_id},
                                 delay_seconds=max(0, int(getattr(deps.settings, "today_debounce_s", 0) or 0)))
    except Exception:
        pending.discard(case_id)         # never leave the case marked as waiting for a plan that will not come
        raise


async def rework_later(deps: Any, case_id: str, *, everything: bool = False, claims: list[str] | None = None,
                       answer: dict | None = None, holdings: bool = False) -> None:
    """The model work an action leads to, queued: an answer worked into its plan, the claims built and planned again
    (once for however many changes ask for it before it starts), or some claims planned again."""
    if everything and not (answer or holdings or claims):
        pending = deps.extras.setdefault("rework_pending", set())
        if case_id in pending:
            return
        pending.add(case_id)
    payload: dict = {"case_id": case_id}
    if everything:
        payload["all"] = True
    if claims:
        payload["claims"] = claims
    if answer:
        payload["answer"] = answer
    if holdings:
        payload["holdings"] = True
    try:
        await _queue(deps, case_id, "rework", payload, "planning")
    except Exception:
        deps.extras.setdefault("rework_pending", set()).discard(case_id)
        raise


# ---------------------------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------------------------

async def start_case(deps: Any, *, lang: str = "en", starter_name: str | None = None,
                     owner_id: str | None = None) -> tuple[Any, Any]:
    case, reply = await intake.start_case(deps, lang=lang, starter_name=starter_name)
    case.synthetic = False
    case.owner_id = owner_id
    await deps.cases.put_case(case)
    return case, reply


def _talking(deps: Any, case_id: str) -> asyncio.Lock:
    """One conversation turn at a time on a case: each one answers what was said before it."""
    return deps.extras.setdefault("turn_locks", {}).setdefault(case_id, asyncio.Lock())


async def turn(deps: Any, case_id: str, *, text: str, person_id: str | None, lang: str | None, channel: str,
               seconds: int | None) -> dict:
    """What the family says in the conversation: heard, worked into what is known, and the next question asked. The
    model work does not hold the case (until 19 October 2026 it did, for five model calls a turn): the family's
    other actions and the background work go on meanwhile, and the turn's writes are merged with theirs. Plans that
    what was said changes are planned again in the background."""
    await require_case(deps, case_id)
    async with _talking(deps, case_id), job(deps, case_id, exclusive=False):
        out = await intake.family_turn(deps, case_id, text, person_id=person_id, lang=lang, channel=channel,
                                       seconds=seconds, replan=False)
    if out.pop("replan", False):
        await rework_later(deps, case_id, everything=True)
    return out


# ---------------------------------------------------------------------------------------------
# papers
# ---------------------------------------------------------------------------------------------

async def upload(deps: Any, case_id: str, files: list[tuple[str, bytes, str]], *, by: str | None,
                 title: str | None = None, together: bool = False) -> list[Any]:
    """Each file becomes a paper (or, with together, the images become pages of one paper); reading
    runs in the background and the screens hear about every step."""
    await require_case(deps, case_id)
    made = []
    # a new paper is records of its own: it is added at once, even while the case is busy with an earlier one
    async with _uploads(deps):
        groups = [files] if together else [[f] for f in files]
        # several files are taken in side by side (until 19 October 2026 one after another, and each page's OCR
        # too: two papers of three pages kept the family waiting 7.4 s)
        made = list(await asyncio.gather(*(papers.add_upload(deps, case_id, group, uploaded_by=by, title=title)
                                           for group in groups)))
    case = await deps.cases.get_case(case_id)
    if case.stage == "intake":                   # the first paper: nothing else is working on the case yet
        async with case_lock(deps, case_id):
            case = await deps.cases.get_case(case_id)
            if case.stage == "intake":
                case.stage = "papers"
                await deps.cases.put_case(case)
    for p in made:
        await deps.activity(case_id, "reading", f"Added {p.title}; reading it now.", kind="step")
    deps.changed(case_id, "papers")
    await _queue(deps, case_id, "after_papers", {"case_id": case_id, "paper_ids": [p.id for p in made]}, "reading")
    return made


async def delete_paper(deps: Any, case_id: str, paper_id: str) -> dict:
    """Removes a paper the family added: the paper and its file, everything read from it, and anything found only
    in it that nobody has acted on yet (an open lead, a holding without a claim). What other papers or the family
    said stays. The case is then worked through again, as after a new paper, so the money map, the leads and Today
    follow."""
    await require_case(deps, case_id)
    paper = await deps.cases.get(case_id, "papers", paper_id)
    if paper is None:
        raise NotFound(f"No paper {paper_id}")
    if paper.fixture_key:
        raise Conflict("A sample case's papers stay; reset the sample instead.")
    if paper.status in ("queued", "reading"):
        raise Conflict("This paper is still being read. It can be removed as soon as reading finishes.")
    # a paper is not removed while a job works out what the papers point to (it could put back what was removed)
    try:
        async with job(deps, case_id, wait=5), case_lock(deps, case_id):
            removed = await _remove_paper(deps, case_id, paper)
    except TimeoutError:
        raise Conflict("The case is busy working out what its papers point to. Try again in a minute.") from None
    drop = getattr(deps.blobs, "delete_prefix", None)
    if drop is not None:                     # the original file and its page images
        await drop(f"cases/{case_id}/papers/{paper_id}/")
    found = removed["leads"] + removed["holdings"]
    await deps.activity(case_id, "reading", f"Removed “{paper.title}” and what was read from it"
                        + (f", with {found} thing{'s' if found != 1 else ''} found only in it" if found else "")
                        + ". The case is being worked through again without it.", kind="step", paper_id=paper_id)
    deps.changed(case_id, "papers", "values", "transactions", "leads", "holdings", "checkpoints")
    await _queue(deps, case_id, "after_papers", {"case_id": case_id, "paper_ids": []}, "finding")
    return {"deleted": paper_id, **removed}


async def _remove_paper(deps: Any, case_id: str, paper: Any) -> dict:
    pid = paper.id
    store = deps.cases
    txns = [t for t in await store.list(case_id, "transactions") if t.paper_id == pid]
    tx_ids = {t.id for t in txns}
    values = [v for v in await store.list(case_id, "values") if v.paper_id == pid]
    groups = [g for g in await store.list(case_id, "groups") if g.transaction_ids and set(g.transaction_ids) <= tx_ids]
    group_ids = {g.id for g in groups}
    leads = [ld for ld in await store.list(case_id, "leads") if ld.status == "open" and (
        (ld.paper_ids and set(ld.paper_ids) <= {pid}) or (not ld.paper_ids and ld.group_ids and set(ld.group_ids) <= group_ids))]
    lead_ids = {ld.id for ld in leads}
    claimed = {h for c in await store.list(case_id, "claims") for h in c.holding_ids}
    holdings = [h for h in await store.list(case_id, "holdings") if h.id not in claimed and (
        (h.source == "lead" and h.lead_id in lead_ids)
        or (h.source == "paper" and h.evidence and all(e.paper_id == pid for e in h.evidence)))]
    facts = [f for f in await store.list(case_id, "facts") if all(e.paper_id == pid for e in f.evidence)]
    refs = {f"read_value:{v.id}" for v in values} | {f"lead:{i}" for i in lead_ids}
    checkpoints = [c for c in await store.list(case_id, "checkpoints")
                   if c.subject_ref in refs or (c.payload or {}).get("paper_id") == pid]
    lines = [ln.id for ln in await store.list(case_id, "lines") if ln.paper_id == pid]
    for collection, ids in (("checkpoints", [c.id for c in checkpoints]), ("facts", [f.id for f in facts]),
                            ("holdings", [h.id for h in holdings]), ("leads", list(lead_ids)),
                            ("groups", list(group_ids)), ("values", [v.id for v in values]),
                            ("transactions", list(tx_ids)), ("lines", lines)):
        await store.delete_many(case_id, collection, ids)
    await store.delete(case_id, "papers", pid)
    return {"rows": len(tx_ids), "values": len(values), "leads": len(leads), "holdings": len(holdings)}


# ---------------------------------------------------------------------------------------------
# checkpoints: one entry point for every question the family answers
# ---------------------------------------------------------------------------------------------

async def answer_checkpoint(deps: Any, case_id: str, checkpoint_id: str, *, answer: str, value: str | None = None,
                            note: str | None = None, by: str | None = None) -> Checkpoint:
    await require_case(deps, case_id)
    heard: str | None = None              # a correction to the summary, heard as the family's next words
    async with case_lock(deps, case_id):
        # read under the case's lock, so two answers at once (two screens, or a click twice) take one
        cp: Checkpoint | None = await deps.cases.get(case_id, "checkpoints", checkpoint_id)
        if cp is None:
            raise NotFound(f"No checkpoint {checkpoint_id}")
        if cp.status != "open":
            raise Conflict("This question has already been answered.")
        valid = {o.value for o in cp.options}
        if valid and answer not in valid:
            raise Conflict(f"'{answer}' is not one of the answers offered: {', '.join(sorted(valid))}.")
        if cp.code == "C1" and answer == "change" and (note or "").strip():
            heard = (note or "").strip()
            await intake.change_summary(deps, case_id, heard, by=by)
        elif cp.code == "C1":
            await intake.confirm_summary(deps, case_id, by=by, answer=answer)
            if answer == "yes":
                # what the family named (an account, a policy, a loan) goes on the money map and is planned, in
                # the background, as after a paper
                await _queue(deps, case_id, "after_papers", {"case_id": case_id, "paper_ids": []}, "finding")
        elif cp.code == "C2":
            vid = (cp.payload or {}).get("value_id")
            await reading.answer_value_check(deps, case_id, vid, "yes" if answer == "yes" else "no", by=by,
                                             corrected=value)
            if answer != "yes" and value:          # a corrected value can change the holdings and their claims
                await rework_later(deps, case_id, holdings=True, everything=True)
        elif cp.code == "C3":
            if await people.answer_identity(deps, case_id, checkpoint_id, answer, by=by, relationship=value or note,
                                            rework=False):
                await rework_later(deps, case_id, everything=True)     # an heir more or less changes who signs
        elif cp.code == "C4":
            lead_id = (cp.subject_ref or "").split(":", 1)[1]
            await discovery.mark_lead(deps, case_id, lead_id, answer, by=by, value=value, note=note)
            await rework_later(deps, case_id, everything=True)
        elif cp.code == "C5" and cp.kind == "plan_question":
            plan_id = (cp.subject_ref or "").split(":")[1]
            variable = (cp.payload or {}).get("variable")
            await planning.answer_question(deps, case_id, plan_id, variable, answer, by=by, rework=False)
            await rework_later(deps, case_id, answer={"plan_id": plan_id, "variable": variable, "value": answer})
        elif cp.code == "C5" and cp.kind == "plan_approval":
            plan_id = (cp.subject_ref or "").split(":")[1]
            if answer == "approve":
                await _approve_plan(deps, case_id, plan_id, by)
            else:
                cp.answer_note = note
                cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", answer, by, deps.now()
                await deps.cases.put(case_id, "checkpoints", cp)
        elif cp.code == "C7" and cp.kind == "claim_event":
            # what an institution said: recorded on the claim only now, with the family's yes
            claim = await followup.confirm_claim_event(deps, case_id, cp, answer, by=by)
            if claim is None:
                raise Conflict("This claim is finished already, so nothing was recorded.")
            if answer == "yes" and claim.plan_id:
                await rework_later(deps, case_id, claims=[claim.id])
        elif cp.code == "C6":
            pack_id = (cp.subject_ref or "").split(":", 1)[1]
            if answer == "approve":
                await packs.approve_pack(deps, case_id, pack_id, by=by)
            else:
                cp.answer_note = note
                cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", answer, by, deps.now()
                await deps.cases.put(case_id, "checkpoints", cp)
                await deps.activity(case_id, "packs", f"Asked for a change to the papers: {note or 'no details'}.",
                                    kind="question")
        else:
            cp.status, cp.answer, cp.answer_note, cp.answered_by, cp.answered_at = "answered", answer, note, by, deps.now()
            await deps.cases.put(case_id, "checkpoints", cp)
        await today_later(deps, case_id)
    if heard:
        await turn(deps, case_id, text=heard, person_id=by, lang=None, channel="text", seconds=None)
    return await deps.cases.get(case_id, "checkpoints", checkpoint_id)


async def _approve_plan(deps: Any, case_id: str, plan_id: str, by: str | None) -> Any:
    cp = await planning.approve_plan(deps, case_id, plan_id, by=by)
    claim = await deps.cases.get(case_id, "claims", cp.claim_id) if cp.claim_id else None
    if claim is not None and claim.kind != "procedure":
        # papers for the plan just approved, unless they are made already (a plan that replaced the interim one
        # gets its own papers; the interim letter stays as it was sent)
        have = [pk for pk in await deps.cases.list(case_id, "packs") if pk.claim_id == claim.id]
        if not any(pk.plan_key in (None, cp.plan_key) for pk in have):
            await _queue(deps, case_id, "prepare_pack", {"case_id": case_id, "claim_id": claim.id}, "preparing")
    return cp


async def mark_lead(deps: Any, case_id: str, lead_id: str, *, mark: str, value: str | None, note: str | None,
                    by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "leads", lead_id) is None:
            raise NotFound(f"No lead {lead_id}")
        lead = await discovery.mark_lead(deps, case_id, lead_id, mark, by=by, value=value, note=note)
        await rework_later(deps, case_id, everything=True)
        await today_later(deps, case_id)
    return lead


async def answer_coverage(deps: Any, case_id: str, item_id: str, *, answer: str, by: str | None) -> None:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        case = await deps.cases.get_case(case_id)
        if not any(c.get("id") == item_id for c in case.coverage):
            raise NotFound(f"No coverage item {item_id}")
        await discovery.answer_coverage(deps, case_id, item_id, answer, by=by)
        await today_later(deps, case_id)


# ---------------------------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------------------------

async def add_person(deps: Any, case_id: str, body: Any) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        p = await people.add_person(deps, case_id, display_name=body.display_name, relationship=body.relationship,
                                    roles=body.roles, languages=body.languages, city=body.city, country=body.country,
                                    by=body.by)
        changes = {k: v for k, v in {"address": body.address, "age": body.age}.items() if v is not None}
        if changes:
            p = await people.update_person(deps, case_id, p.id, changes, by=body.by)
        await rework_later(deps, case_id, everything=True)
    return p


async def update_person(deps: Any, case_id: str, person_id: str, body: Any) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "people", person_id) is None:
            raise NotFound(f"No person {person_id}")
        changes = body.model_dump(exclude_none=True, exclude={"by"})
        p = await people.update_person(deps, case_id, person_id, changes, by=body.by)
        if {"relationship", "display_name"} & set(changes):
            await rework_later(deps, case_id, everything=True)
    return p


async def invite_person(deps: Any, case_id: str, person_id: str, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        return await people.invite_person(deps, case_id, person_id, by=by)


# ---------------------------------------------------------------------------------------------
# the papers of a pack, and the pack as one download
# ---------------------------------------------------------------------------------------------

async def item_file(deps: Any, case_id: str, pack_id: str, item_id: str, *, mirror: bool = False) -> tuple[bytes, str]:
    from ..forms.letters import letter_pdf
    pack = await deps.cases.get(case_id, "packs", pack_id)
    if pack is None:
        raise NotFound(f"No pack {pack_id}")
    item = next((i for i in pack.items if i.id == item_id), None)
    if item is None:
        raise NotFound(f"No item {item_id}")
    case = await deps.cases.get_case(case_id)
    ref = item.mirror_file if mirror else item.file
    if ref and await deps.blobs.exists(ref):
        return await deps.blobs.get(ref), deps.blobs.content_type(ref)
    text = item.mirror_text if mirror else item.text
    if text:
        lang = (item.mirror_language if mirror else item.language) or "en"
        footer = "Prepared with AfterLedger." + (" Sample case: not a real letter." if case.synthetic else "")
        return letter_pdf(text, lang=lang, footer=footer), "application/pdf"
    raise NotFound("This item has no file yet.")


_PAGE_CACHE: dict[tuple, bytes] = {}


async def item_page(deps: Any, case_id: str, pack_id: str, item_id: str, n: int, *, mirror: bool = False) -> bytes:
    """One page of a filled form or letter, as a PNG, for the pack preview."""
    import pymupdf
    pack = await deps.cases.get(case_id, "packs", pack_id)
    item = next((i for i in (pack.items if pack else []) if i.id == item_id), None)
    if item is None:
        raise NotFound(f"No item {item_id}")
    key = (case_id, pack_id, item_id, n, mirror, item.file, len(item.text or ""))
    if key not in _PAGE_CACHE:
        data, mime = await item_file(deps, case_id, pack_id, item_id, mirror=mirror)
        if mime != "application/pdf":
            raise NotFound("Not a PDF")
        doc = pymupdf.open(stream=data, filetype="pdf")
        if n < 1 or n > doc.page_count:
            raise NotFound(f"No page {n}")
        _PAGE_CACHE[key] = doc[n - 1].get_pixmap(dpi=110, alpha=False).tobytes("png")
        if len(_PAGE_CACHE) > 256:
            _PAGE_CACHE.pop(next(iter(_PAGE_CACHE)))
    return _PAGE_CACHE[key]


async def item_pages(deps: Any, case_id: str, pack_id: str, item_id: str) -> int:
    import pymupdf
    try:
        data, mime = await item_file(deps, case_id, pack_id, item_id)
    except NotFound:
        return 0
    if mime != "application/pdf":
        return 0
    return pymupdf.open(stream=data, filetype="pdf").page_count


async def pack_zip(deps: Any, case_id: str, pack_id: str) -> tuple[bytes, str]:
    pack = await deps.cases.get(case_id, "packs", pack_id)
    if pack is None:
        raise NotFound(f"No pack {pack_id}")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, item in enumerate(pack.items, start=1):
            for mirror in (False, True):
                try:
                    data, _ = await item_file(deps, case_id, pack_id, item.id, mirror=mirror)
                except NotFound:
                    continue
                name = _file_name(f"{n:02d} {item.title}" + (f" ({item.mirror_language})" if mirror else "")) + ".pdf"
                z.writestr(name, data)
        lines = [f"{pack.title}", "", pack.summary, "", "Papers to attach:"]
        lines += [f"- {e.name}: {e.status.replace('_', ' ')}" + (f". {e.note}" if e.note else "") for e in pack.enclosures]
        lines += ["", "Checks:"] + [f"- {c.name}: {c.result}. {c.detail}" for c in pack.checks]
        z.writestr("00 Read me.txt", "\n".join(lines))
    return buf.getvalue(), _file_name(pack.title) + ".zip"


def _file_name(text: str) -> str:
    import re
    return re.sub(r"[^\w .,()&-]+", "", text).strip()[:120] or "pack"


# ---------------------------------------------------------------------------------------------
# follow-up
# ---------------------------------------------------------------------------------------------

async def claim_event(deps: Any, case_id: str, claim_id: str, *, event: str, on: date | None, amount: str | None,
                      note: str | None, by: str | None) -> Any:
    case = await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "claims", claim_id) is None:
            raise NotFound(f"No claim {claim_id}")
        claim = await followup.record_claim_event(deps, case_id, claim_id, event, on or deps.today_for(case),
                                                  amount=amount_typed(amount), note=note, by=by, rework=False)
        if claim.plan_id:                 # an event can settle a fact the plan depends on: planned again, after
            await rework_later(deps, case_id, claims=[claim_id])
        await today_later(deps, case_id)
    return claim


async def draft_letter(deps: Any, case_id: str, claim_id: str, *, kind: str, request: str | None, by: str | None) -> Any:
    """The letter is written by a model: without holding the case, so the family's other actions go on meanwhile."""
    await require_case(deps, case_id)
    if await deps.cases.get(case_id, "claims", claim_id) is None:
        raise NotFound(f"No claim {claim_id}")
    async with job(deps, case_id, exclusive=False):
        return await followup.draft_letter(deps, case_id, claim_id, kind, by=by, request=request)


async def approve_letter(deps: Any, case_id: str, corr_id: str, *, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "correspondence", corr_id) is None:
            raise NotFound(f"No letter {corr_id}")
        return await followup.approve_letter(deps, case_id, corr_id, by=by)


async def letter_sent(deps: Any, case_id: str, corr_id: str, *, on: date | None, channel: str, by: str | None) -> Any:
    case = await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "correspondence", corr_id) is None:
            raise NotFound(f"No letter {corr_id}")
        corr = await followup.mark_letter_sent(deps, case_id, corr_id, on=on or deps.today_for(case), channel=channel,
                                               by=by)
        await today_later(deps, case_id)
    return corr


async def letter_file(deps: Any, case_id: str, corr_id: str, *, mirror: bool = False) -> bytes:
    from ..forms.letters import letter_pdf
    corr = await deps.cases.get(case_id, "correspondence", corr_id)
    if corr is None or not (corr.letter_mirror if mirror else corr.letter_text):
        raise NotFound(f"No letter {corr_id}")
    case = await deps.cases.get_case(case_id)
    footer = "Prepared with AfterLedger." + (" Sample case: not a real letter." if case.synthetic else "")
    return letter_pdf(corr.letter_mirror if mirror else corr.letter_text,
                      lang=(corr.letter_lang if mirror else "en") or "en", title=corr.title, footer=footer)


async def record_reply(deps: Any, case_id: str, claim_id: str, *, title: str, text: str, on: date | None,
                       channel: str) -> Any:
    case = await require_case(deps, case_id)
    if await deps.cases.get(case_id, "claims", claim_id) is None:
        raise NotFound(f"No claim {claim_id}")
    # the reply is read by a model: without holding the case
    async with job(deps, case_id, exclusive=False):
        corr = await followup.record_reply(deps, case_id, claim_id, title=title, text=text,
                                           on=on or deps.today_for(case), channel=channel)
    await today_later(deps, case_id)
    return corr


async def refresh_today(deps: Any, case_id: str, viewer_id: str | None) -> None:
    """Who is viewing, saved at once; their Today (a greeting of their own) is planned in the background, and the
    screen hears when it is ready (until 19 October 2026 the family waited for the model, 8 to 28 s)."""
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if viewer_id:
            case = await deps.cases.get_case(case_id)
            if await deps.cases.get(case_id, "people", viewer_id) is None:
                raise NotFound(f"No person {viewer_id}")
            case.viewer_person_id = viewer_id
            await deps.cases.put_case(case)
    await today_later(deps, case_id)


async def ask(deps: Any, case_id: str, *, question: str, person_id: str | None, lang: str | None,
              channel: str, keep: bool = True) -> dict:
    await require_case(deps, case_id)
    if not keep:                              # nothing is saved, so nothing needs the case's lock
        return await conversation.ask(deps, case_id, question, person_id=person_id, lang=lang, channel=channel,
                                      keep=False)
    # the answer is model work: the case is not held meanwhile, and what the question adds to it is merged
    async with job(deps, case_id, exclusive=False):
        return await conversation.ask(deps, case_id, question, person_id=person_id, lang=lang, channel=channel)


# ---------------------------------------------------------------------------------------------
# sharing a claim with the institution's desk
# ---------------------------------------------------------------------------------------------

async def share_desk(deps: Any, case_id: str, claim_id: str, *, days: int, by: str | None) -> dict:
    case = await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        claim = await deps.cases.get(case_id, "claims", claim_id)
        if claim is None:
            raise NotFound(f"No claim {claim_id}")
        until = deps.today_for(case) + timedelta(days=days)
        existing = next((s for s in case.shared_with if s.get("claim_id") == claim_id), None)
        token = existing["token"] if existing else new_id("desk").replace("desk_", "")
        share = {"token": token, "claim_id": claim_id, "institution_id": claim.institution_id,
                 "until": until.isoformat(), "by": by}
        case.shared_with = [s for s in case.shared_with if s.get("claim_id") != claim_id] + [share]
        await deps.cases.put_case(case)
        await knowledge(deps).store.put("desk_shares", token, {"id": token, "case_id": case_id, **share})
        inst = await knowledge(deps).institution(claim.institution_id)
        await deps.activity(case_id, "followup", f"Shared {claim.title} with {inst.name if inst else 'the institution'}'s "
                            f"desk until {until.day} {until.strftime('%B %Y')}.", kind="decision")
    return {"token": token, "until": until}


async def desk_settle(deps: Any, share: dict, *, amount: str, on: date | None, note: str | None) -> None:
    """The institution records on its desk that it has paid. It reaches the family as a message from the institution
    and a question: the claim is settled only when the family confirms the money arrived."""
    case_id, claim_id = share["case_id"], share["claim_id"]
    case = await require_case(deps, case_id)
    paid = amount_typed(amount)
    if not paid or not Decimal(paid):
        raise BadInput("Write the amount paid as a number, for example 120000 or 1,20,000.")
    async with case_lock(deps, case_id):
        claim = await deps.cases.get(case_id, "claims", claim_id)
        if claim is None:
            raise NotFound(f"No claim {claim_id}")
        if claim.status in ("settled", "closed"):
            raise Conflict("This claim is settled already." if claim.status == "settled" else "This claim is closed.")
        when = on or deps.today_for(case)
        inst = await knowledge(deps).institution(claim.institution_id)
        name = (inst.short_name or inst.name) if inst else "The institution"
        paid_text = format_amount(Decimal(paid), claim.currency, decimals=Decimal(paid) % 1 != 0)
        # a payment recorded here that still waits for the family: the same one again changes nothing, and a
        # corrected amount replaces it (its entry among the replies says so)
        for waiting in await deps.cases.list(case_id, "checkpoints"):
            wp = waiting.payload or {}
            if not (waiting.status == "open" and waiting.code == "C7" and waiting.kind == "claim_event"
                    and wp.get("claim_id") == claim_id and wp.get("event") == "settled" and wp.get("source") == "desk"):
                continue
            if Decimal(wp.get("amount") or "0") == Decimal(paid) and wp.get("on") == when.isoformat():
                return
            before = await deps.cases.get(case_id, "correspondence", wp["correspondence_id"]) \
                if wp.get("correspondence_id") else None
            if before is not None:
                before.summary = f"{before.summary} It corrected this to {paid_text}."
                await deps.cases.put(case_id, "correspondence", before)
        corr = await followup.add_correspondence(
            deps, case_id, claim_id=claim_id, direction="in", channel="online", on=deps.today_for(case),
            title=f"{name} recorded a payment on its desk",
            summary=f"{name} recorded on the AfterLedger desk that it paid {paid_text} on {followup.fmt_full(when)}."
                    + (f" {note}" if note else ""))
        await followup.propose_claim_event(deps, case_id, claim, "settled", when, amount=paid, source="desk",
                                           correspondence_id=corr.id)
        await today_later(deps, case_id)


async def desk_lookup(deps: Any, token: str) -> dict:
    share = await knowledge(deps).store.get("desk_shares", token)
    if share is None:
        raise NotFound("This link is not valid.")
    case = await deps.cases.get_case(share["case_id"])
    if case is None:
        raise NotFound("This link is not valid.")
    if date.fromisoformat(share["until"]) < deps.today_for(case):
        raise NotFound("This link has expired.")
    return share


