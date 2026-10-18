"""What the family can do, as use cases: each runs the module that owns the change and then the
follow-on steps that keep the case consistent (a lead marked changes the claims, an approved plan
gets its papers prepared). Changes to one case are serialised by a per-case lock, so two screens or a
background job can never interleave writes to the same case."""
from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from datetime import date, timedelta
from typing import Any

from ..domain.case import Checkpoint
from ..domain.ids import new_id
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
from ..modules.common import knowledge, snapshot

log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


# Uploads taken in at the same time on one server: each holds its files in memory while its pages are drawn and
# stored, so a crowd of large uploads waits its turn instead of running the server out of memory.
UPLOADS_AT_ONCE = 3


def _uploads(deps: Any) -> asyncio.Semaphore:
    return deps.extras.setdefault("uploads", asyncio.Semaphore(UPLOADS_AT_ONCE))


def case_lock(deps: Any, case_id: str) -> asyncio.Lock:
    locks = deps.extras.setdefault("case_locks", {})
    lock = locks.get(case_id)
    if lock is None:
        lock = locks[case_id] = asyncio.Lock()
    return lock


async def refresh_clocks_if_idle(deps: Any, case_id: str) -> None:
    """Clocks move on with the calendar; they are brought up to date when a screen opens, unless the case
    is busy (then the job that holds it refreshes them when it finishes)."""
    lock = case_lock(deps, case_id)
    if lock.locked():
        return
    async with lock:
        await followup.refresh_clocks(deps, case_id)


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
        # reading first, without holding the case (it takes minutes for a long statement, and writes only each
        # paper's own records); then what the papers lead to, which changes the case, under its lock
        await pipeline.read_papers(deps, payload["case_id"], payload["paper_ids"])
        async with case_lock(deps, payload["case_id"]):
            await pipeline.after_reading(deps, payload["case_id"])
            await followup.plan_today(deps, payload["case_id"])

    async def prepare_pack(payload: dict) -> None:
        async with case_lock(deps, payload["case_id"]):
            await packs.prepare_pack(deps, payload["case_id"], payload["claim_id"])
            await followup.plan_today(deps, payload["case_id"])

    async def plan_today(payload: dict) -> None:
        deps.extras.setdefault("today_pending", set()).discard(payload["case_id"])
        async with case_lock(deps, payload["case_id"]):
            await followup.plan_today(deps, payload["case_id"])

    deps.tasks.register("after_papers", after_papers)
    deps.tasks.register("prepare_pack", prepare_pack)
    deps.tasks.register("plan_today", plan_today)

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
    """Today is planned again after a change, in the background, once however many changes come in."""
    pending = deps.extras.setdefault("today_pending", set())
    if case_id in pending:
        return
    pending.add(case_id)
    try:
        await deps.tasks.enqueue("plan_today", {"case_id": case_id})
    except Exception:
        pending.discard(case_id)         # never leave the case marked as waiting for a plan that will not come
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


async def turn(deps: Any, case_id: str, *, text: str, person_id: str | None, lang: str | None, channel: str,
               seconds: int | None) -> dict:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        return await intake.family_turn(deps, case_id, text, person_id=person_id, lang=lang, channel=channel,
                                        seconds=seconds)


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
        for group in groups:
            made.append(await papers.add_upload(deps, case_id, group, uploaded_by=by, title=title))
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
    await deps.tasks.enqueue("after_papers", {"case_id": case_id, "paper_ids": [p.id for p in made]})
    return made


# ---------------------------------------------------------------------------------------------
# checkpoints: one entry point for every question the family answers
# ---------------------------------------------------------------------------------------------

async def answer_checkpoint(deps: Any, case_id: str, checkpoint_id: str, *, answer: str, value: str | None = None,
                            note: str | None = None, by: str | None = None) -> Checkpoint:
    await require_case(deps, case_id)
    cp: Checkpoint | None = await deps.cases.get(case_id, "checkpoints", checkpoint_id)
    if cp is None:
        raise NotFound(f"No checkpoint {checkpoint_id}")
    if cp.status != "open":
        raise Conflict("This question has already been answered.")
    valid = {o.value for o in cp.options}
    if valid and answer not in valid:
        raise Conflict(f"'{answer}' is not one of the answers offered: {', '.join(sorted(valid))}.")
    async with case_lock(deps, case_id):
        if cp.code == "C1" and answer == "change" and (note or "").strip():
            await intake.change_summary(deps, case_id, (note or "").strip(), by=by)
        elif cp.code == "C1":
            await intake.confirm_summary(deps, case_id, by=by, answer=answer)
        elif cp.code == "C2":
            vid = (cp.payload or {}).get("value_id")
            await reading.answer_value_check(deps, case_id, vid, "yes" if answer == "yes" else "no", by=by,
                                             corrected=value)
            if answer != "yes" and value:
                await discovery.holdings_from_papers(deps, case_id)
                await pipeline.refresh_claims(deps, case_id)
        elif cp.code == "C3":
            await people.answer_identity(deps, case_id, checkpoint_id, answer, by=by, relationship=value or note)
        elif cp.code == "C4":
            lead_id = (cp.subject_ref or "").split(":", 1)[1]
            await discovery.mark_lead(deps, case_id, lead_id, answer, by=by, value=value, note=note)
            await pipeline.refresh_claims(deps, case_id)
        elif cp.code == "C5" and cp.kind == "plan_question":
            plan_id = (cp.subject_ref or "").split(":")[1]
            await planning.answer_question(deps, case_id, plan_id, (cp.payload or {}).get("variable"), answer, by=by)
        elif cp.code == "C5" and cp.kind == "plan_approval":
            plan_id = (cp.subject_ref or "").split(":")[1]
            if answer == "approve":
                await _approve_plan(deps, case_id, plan_id, by)
            else:
                cp.answer_note = note
                cp.status, cp.answer, cp.answered_by, cp.answered_at = "answered", answer, by, deps.now()
                await deps.cases.put(case_id, "checkpoints", cp)
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
    return await deps.cases.get(case_id, "checkpoints", checkpoint_id)


async def _approve_plan(deps: Any, case_id: str, plan_id: str, by: str | None) -> Any:
    cp = await planning.approve_plan(deps, case_id, plan_id, by=by)
    claim = await deps.cases.get(case_id, "claims", cp.claim_id) if cp.claim_id else None
    if claim is not None and claim.kind != "procedure" and not claim.pack_id:
        await deps.tasks.enqueue("prepare_pack", {"case_id": case_id, "claim_id": claim.id})
    return cp


async def mark_lead(deps: Any, case_id: str, lead_id: str, *, mark: str, value: str | None, note: str | None,
                    by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "leads", lead_id) is None:
            raise NotFound(f"No lead {lead_id}")
        lead = await discovery.mark_lead(deps, case_id, lead_id, mark, by=by, value=value, note=note)
        await pipeline.refresh_claims(deps, case_id)
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
        await pipeline.refresh_claims(deps, case_id)
    return p


async def update_person(deps: Any, case_id: str, person_id: str, body: Any) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "people", person_id) is None:
            raise NotFound(f"No person {person_id}")
        changes = body.model_dump(exclude_none=True, exclude={"by"})
        p = await people.update_person(deps, case_id, person_id, changes, by=body.by)
        if {"relationship", "display_name"} & set(changes):
            await pipeline.refresh_claims(deps, case_id)
    return p


async def invite_person(deps: Any, case_id: str, person_id: str, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        return await people.invite_person(deps, case_id, person_id, by=by)


# ---------------------------------------------------------------------------------------------
# plans, procedures and packs
# ---------------------------------------------------------------------------------------------

async def answer_plan(deps: Any, case_id: str, plan_id: str, *, variable: str, value: str, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        cp = await planning.answer_question(deps, case_id, plan_id, variable, value, by=by)
        if cp is None:
            raise NotFound(f"No plan {plan_id}")
        await today_later(deps, case_id)
    return cp


async def approve_plan(deps: Any, case_id: str, plan_id: str, *, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "plans", plan_id) is None:
            raise NotFound(f"No plan {plan_id}")
        cp = await _approve_plan(deps, case_id, plan_id, by)
        await today_later(deps, case_id)
    return cp


async def start_procedure(deps: Any, case_id: str, plan_key: str, *, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        claim = await planning.start_procedure(deps, case_id, plan_key, by=by)
        if claim is None:
            raise NotFound(f"No plan {plan_key}")
        await today_later(deps, case_id)
    return claim


async def prepare_pack(deps: Any, case_id: str, claim_id: str) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "claims", claim_id) is None:
            raise NotFound(f"No claim {claim_id}")
        pack = await packs.prepare_pack(deps, case_id, claim_id)
        await today_later(deps, case_id)
    return pack


async def approve_pack(deps: Any, case_id: str, pack_id: str, *, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "packs", pack_id) is None:
            raise NotFound(f"No pack {pack_id}")
        pack = await packs.approve_pack(deps, case_id, pack_id, by=by)
        await today_later(deps, case_id)
    return pack


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
                                                  amount=amount, note=note, by=by)
        await today_later(deps, case_id)
    return claim


async def draft_letter(deps: Any, case_id: str, claim_id: str, *, kind: str, request: str | None, by: str | None) -> Any:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "claims", claim_id) is None:
            raise NotFound(f"No claim {claim_id}")
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
    async with case_lock(deps, case_id):
        if await deps.cases.get(case_id, "claims", claim_id) is None:
            raise NotFound(f"No claim {claim_id}")
        corr = await followup.record_reply(deps, case_id, claim_id, title=title, text=text,
                                           on=on or deps.today_for(case), channel=channel)
        await today_later(deps, case_id)
    return corr


async def refresh_today(deps: Any, case_id: str, viewer_id: str | None) -> None:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
        if viewer_id:
            case = await deps.cases.get_case(case_id)
            if await deps.cases.get(case_id, "people", viewer_id) is None:
                raise NotFound(f"No person {viewer_id}")
            case.viewer_person_id = viewer_id
            await deps.cases.put_case(case)
        await followup.plan_today(deps, case_id, viewer_id)


async def ask(deps: Any, case_id: str, *, question: str, person_id: str | None, lang: str | None,
              channel: str) -> dict:
    await require_case(deps, case_id)
    async with case_lock(deps, case_id):
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
        await deps.activity(case_id, "followup", f"Shared {claim.title} with {inst.name if inst else 'the institution'}, "
                            f"read only, until {until.day} {until.strftime('%B %Y')}.", kind="decision")
    return {"token": token, "until": until}


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


async def case_counts(deps: Any, case_id: str) -> dict:
    snap = await snapshot(deps, case_id, ["papers", "claims"])
    return {"papers": len(snap.papers), "claims": len(snap.claims)}
