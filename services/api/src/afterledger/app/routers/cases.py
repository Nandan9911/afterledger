"""A family's case: every screen's read model, and every action the family takes."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse

from ...accounts import User
from ...domain.case import CasePlan, Checkpoint, Claim, Correspondence, Lead, Pack, Paper, Person, Utterance
from ...runtime import Deps
from .. import actions
from ..schemas import (
    ActivityView,
    AnswerIn,
    AskIn,
    AskOut,
    ByIn,
    CaseHeader,
    CaseStarted,
    CaseSummary,
    ClaimEventIn,
    ConversationView,
    CoverageAnswerIn,
    LeadsView,
    LetterIn,
    MarkLeadIn,
    MoneyView,
    PacksView,
    PackView,
    PapersView,
    PaperView,
    PeopleView,
    PersonIn,
    PersonPatch,
    PlanAnswerIn,
    PlansView,
    PlanView,
    ProcedureIn,
    ReplyIn,
    SentIn,
    ShareIn,
    ShareOut,
    SourcesView,
    StartCase,
    TodayIn,
    TodayView,
    TrackerView,
    TurnIn,
    TurnOut,
    UsageView,
)
from ..views import case as case_view
from ..views import intake as intake_view
from ..views import leads as leads_view
from ..views import money as money_view
from ..views import packs as packs_view
from ..views import papers as papers_view
from ..views import people as people_view
from ..views import plans as plans_view
from ..views import sources as sources_view
from ..views import today as today_view
from ..views import tracker as tracker_view
from ..views.common import cite_views
from ..views.localize import page_language
from .auth import case_access, optional_user
from .common import get_deps, queue_events

# Where sign-in is required, every /{case_id} route is open only to the case's own account (and the admin)
router = APIRouter(prefix="/v1/cases", tags=["cases"], dependencies=[Depends(case_access)])
MAX_UPLOAD = 25 * 1024 * 1024
ALLOWED = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


# ---- cases ----------------------------------------------------------------------------------

@router.get("", response_model=list[CaseSummary])
async def list_cases(ids: str | None = None, user: User | None = Depends(optional_user),
                     deps: Deps = Depends(get_deps)) -> list[CaseSummary]:
    """Where sign-in is required (AL_AUTH): the sample cases and the signed-in account's own. Otherwise, every case
    on a development copy; on a public site without accounts, the sample cases and the ones named in `ids`
    (comma-separated: the cases this browser started or opened). A case's id is random: its private address."""
    cases = await deps.cases.list_cases()
    if deps.settings.product:              # the public site lists only the sample families it shows
        from ..demo import family_keys
        shown = set(deps.settings.shown_families(family_keys()))
        cases = [c for c in cases if not c.demo_key or c.demo_key in shown]
    if deps.settings.auth_required:
        cases = [c for c in cases if c.demo_key or (user is not None and c.owner_id == user.id)]
    elif deps.settings.product:
        mine = {i.strip() for i in (ids or "").split(",") if i.strip()}
        cases = [c for c in cases if c.demo_key or c.id in mine]
    return [case_view.summary(c) for c in sorted(cases, key=lambda c: (c.demo_key is None, c.title))]


@router.post("", response_model=CaseStarted)
async def start_case(body: StartCase, user: User | None = Depends(optional_user),
                     deps: Deps = Depends(get_deps)) -> CaseStarted:
    if deps.settings.auth_required and user is None:
        raise HTTPException(401, "Please sign in first.")
    case, reply = await actions.start_case(deps, lang=body.lang, starter_name=body.starter_name,
                                           owner_id=user.id if user else None)
    return CaseStarted(case=case, reply=reply)


@router.get("/{case_id}", response_model=CaseHeader)
async def get_case(case_id: str, viewer: str | None = None, deps: Deps = Depends(get_deps)) -> CaseHeader:
    await actions.require_case(deps, case_id)
    return await case_view.header(deps, case_id, viewer)


@router.delete("/{case_id}")
async def delete_case(case_id: str, deps: Deps = Depends(get_deps)) -> dict:
    case = await actions.require_case(deps, case_id)
    if case.demo_key:
        raise HTTPException(409, "A demo case is reset, not deleted.")
    await deps.cases.delete_case(case_id)
    drop = getattr(deps.blobs, "delete_prefix", None)
    if drop is not None:                     # its papers, page images and filled forms go with it
        await drop(f"cases/{case_id}/")
    return {"deleted": case_id}


# ---- telling the story ------------------------------------------------------------------------

@router.get("/{case_id}/conversation", response_model=ConversationView)
async def conversation(case_id: str, deps: Deps = Depends(get_deps)) -> ConversationView:
    await actions.require_case(deps, case_id)
    return await intake_view.conversation(deps, case_id)


@router.post("/{case_id}/turns", response_model=TurnOut)
async def turn(case_id: str, body: TurnIn, deps: Deps = Depends(get_deps)) -> TurnOut:
    out = await actions.turn(deps, case_id, text=body.text, person_id=body.person_id, lang=body.lang,
                             channel=body.channel, seconds=body.seconds)
    return TurnOut(heard=out["heard"], reply=out["reply"], conversation=await intake_view.conversation(deps, case_id))


# ---- today ----------------------------------------------------------------------------------

@router.get("/{case_id}/today", response_model=TodayView)
async def today(case_id: str, request: Request, viewer: str | None = None, deps: Deps = Depends(get_deps)) -> TodayView:
    await actions.require_case(deps, case_id)
    await actions.refresh_clocks_if_idle(deps, case_id)
    return await today_view.today(deps, case_id, viewer, lang=page_language(request))


@router.post("/{case_id}/today", response_model=TodayView)
async def refresh_today(case_id: str, body: TodayIn, request: Request, deps: Deps = Depends(get_deps)) -> TodayView:
    await actions.refresh_today(deps, case_id, body.viewer_id)
    return await today_view.today(deps, case_id, body.viewer_id, lang=page_language(request))


# ---- papers ---------------------------------------------------------------------------------

@router.get("/{case_id}/papers", response_model=PapersView)
async def papers(case_id: str, deps: Deps = Depends(get_deps)) -> PapersView:
    await actions.require_case(deps, case_id)
    return await papers_view.papers(deps, case_id)


@router.post("/{case_id}/papers", response_model=list[Paper])
async def upload(case_id: str, files: list[UploadFile] = File(...), by: str | None = Form(None),
                 title: str | None = Form(None), together: bool = Form(False),
                 deps: Deps = Depends(get_deps)) -> list[Paper]:
    got = []
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, f"{f.filename} is larger than 25 MB.")
        mime = (f.content_type or "").lower()
        if (f.filename or "").lower().endswith((".heic", ".heif")) and mime not in ALLOWED:
            mime = "image/heic"           # a browser on Windows often sends a phone's HEIC photo with no type
        if mime not in ALLOWED and not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(415, f"{f.filename}: only PDFs and photos (JPEG, PNG, WebP, HEIC) can be read.")
        got.append((f.filename or "upload", data, mime or "application/pdf"))
    if not got:
        raise HTTPException(400, "No files were sent.")
    return await actions.upload(deps, case_id, got, by=by, title=title, together=together)


@router.get("/{case_id}/papers/{paper_id}", response_model=PaperView)
async def paper(case_id: str, paper_id: str, deps: Deps = Depends(get_deps)) -> PaperView:
    await actions.require_case(deps, case_id)
    view = await papers_view.paper(deps, case_id, paper_id)
    if view is None:
        raise HTTPException(404, f"No paper {paper_id}")
    return view


@router.get("/{case_id}/papers/{paper_id}/pages/{page_id}/image")
async def page_image(case_id: str, paper_id: str, page_id: str, deps: Deps = Depends(get_deps)) -> Response:
    p: Paper | None = await deps.cases.get(case_id, "papers", paper_id)
    page = next((pg for pg in (p.pages if p else []) if pg.id == page_id), None)
    if page is None or not await deps.blobs.exists(page.image):
        raise HTTPException(404, "No such page")
    return Response(await deps.blobs.get(page.image), media_type=deps.blobs.content_type(page.image),
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/{case_id}/papers/{paper_id}/file")
async def paper_file(case_id: str, paper_id: str, deps: Deps = Depends(get_deps)) -> Response:
    p: Paper | None = await deps.cases.get(case_id, "papers", paper_id)
    if p is None or not p.file or not await deps.blobs.exists(p.file):
        raise HTTPException(404, "No file for this paper")
    return Response(await deps.blobs.get(p.file), media_type=deps.blobs.content_type(p.file))


# ---- money and leads ------------------------------------------------------------------------------

@router.get("/{case_id}/money", response_model=MoneyView)
async def money(case_id: str, request: Request, deps: Deps = Depends(get_deps)) -> MoneyView:
    await actions.require_case(deps, case_id)
    return await money_view.money_map(deps, case_id, lang=page_language(request))


@router.get("/{case_id}/leads", response_model=LeadsView)
async def leads(case_id: str, deps: Deps = Depends(get_deps)) -> LeadsView:
    await actions.require_case(deps, case_id)
    return await leads_view.leads(deps, case_id)


@router.post("/{case_id}/leads/{lead_id}/mark", response_model=Lead)
async def mark_lead(case_id: str, lead_id: str, body: MarkLeadIn, deps: Deps = Depends(get_deps)) -> Lead:
    return await actions.mark_lead(deps, case_id, lead_id, mark=body.mark, value=body.value, note=body.note,
                                   by=body.by)


@router.post("/{case_id}/coverage/{item_id}/answer", response_model=LeadsView)
async def answer_coverage(case_id: str, item_id: str, body: CoverageAnswerIn, deps: Deps = Depends(get_deps)) -> LeadsView:
    """The family answers "did they have…?" for something families in this country usually hold."""
    await actions.answer_coverage(deps, case_id, item_id, answer=body.answer, by=body.by)
    return await leads_view.leads(deps, case_id)


# ---- people -----------------------------------------------------------------------------------

@router.get("/{case_id}/people", response_model=PeopleView)
async def people(case_id: str, deps: Deps = Depends(get_deps)) -> PeopleView:
    await actions.require_case(deps, case_id)
    return await people_view.people(deps, case_id)


@router.post("/{case_id}/people", response_model=Person)
async def add_person(case_id: str, body: PersonIn, deps: Deps = Depends(get_deps)) -> Person:
    return await actions.add_person(deps, case_id, body)


@router.patch("/{case_id}/people/{person_id}", response_model=Person)
async def update_person(case_id: str, person_id: str, body: PersonPatch, deps: Deps = Depends(get_deps)) -> Person:
    return await actions.update_person(deps, case_id, person_id, body)


@router.post("/{case_id}/people/{person_id}/invite", response_model=Person)
async def invite_person(case_id: str, person_id: str, body: ByIn, deps: Deps = Depends(get_deps)) -> Person:
    return await actions.invite_person(deps, case_id, person_id, body.by)


# ---- plans and procedures ------------------------------------------------------------------------

@router.get("/{case_id}/plans", response_model=PlansView)
async def plans(case_id: str, deps: Deps = Depends(get_deps)) -> PlansView:
    await actions.require_case(deps, case_id)
    return await plans_view.plans(deps, case_id)


@router.get("/{case_id}/plans/{plan_id}", response_model=PlanView)
async def plan(case_id: str, plan_id: str, request: Request, deps: Deps = Depends(get_deps)) -> PlanView:
    await actions.require_case(deps, case_id)
    view = await plans_view.plan(deps, case_id, plan_id, lang=page_language(request))
    if view is None:
        raise HTTPException(404, f"No plan {plan_id}")
    return view


@router.post("/{case_id}/plans/{plan_id}/answers", response_model=CasePlan)
async def answer_plan(case_id: str, plan_id: str, body: PlanAnswerIn, deps: Deps = Depends(get_deps)) -> CasePlan:
    return await actions.answer_plan(deps, case_id, plan_id, variable=body.variable, value=body.value, by=body.by)


@router.post("/{case_id}/plans/{plan_id}/approve", response_model=CasePlan)
async def approve_plan(case_id: str, plan_id: str, body: ByIn, deps: Deps = Depends(get_deps)) -> CasePlan:
    return await actions.approve_plan(deps, case_id, plan_id, by=body.by)


@router.post("/{case_id}/procedures", response_model=Claim)
async def start_procedure(case_id: str, body: ProcedureIn, deps: Deps = Depends(get_deps)) -> Claim:
    return await actions.start_procedure(deps, case_id, body.plan_key, by=body.by)


# ---- packs ----------------------------------------------------------------------------------

@router.get("/{case_id}/packs", response_model=PacksView)
async def packs(case_id: str, request: Request, deps: Deps = Depends(get_deps)) -> PacksView:
    await actions.require_case(deps, case_id)
    return await packs_view.packs(deps, case_id, lang=page_language(request))


@router.get("/{case_id}/packs/{pack_id}", response_model=PackView)
async def pack(case_id: str, pack_id: str, request: Request, deps: Deps = Depends(get_deps)) -> PackView:
    await actions.require_case(deps, case_id)
    view = await packs_view.pack(deps, case_id, pack_id, lang=page_language(request))
    if view is None:
        raise HTTPException(404, f"No pack {pack_id}")
    return view


@router.post("/{case_id}/claims/{claim_id}/pack", response_model=Pack | None)
async def prepare_pack(case_id: str, claim_id: str, deps: Deps = Depends(get_deps)) -> Pack | None:
    return await actions.prepare_pack(deps, case_id, claim_id)


@router.post("/{case_id}/packs/{pack_id}/approve", response_model=Pack)
async def approve_pack(case_id: str, pack_id: str, body: ByIn, deps: Deps = Depends(get_deps)) -> Pack:
    return await actions.approve_pack(deps, case_id, pack_id, by=body.by)


@router.get("/{case_id}/packs/{pack_id}/items/{item_id}/file")
async def pack_item_file(case_id: str, pack_id: str, item_id: str, mirror: bool = False,
                         deps: Deps = Depends(get_deps)) -> Response:
    data, mime = await actions.item_file(deps, case_id, pack_id, item_id, mirror=mirror)
    return Response(data, media_type=mime, headers={"Content-Disposition": "inline"})


@router.get("/{case_id}/packs/{pack_id}/items/{item_id}/pages/{n}")
async def pack_item_page(case_id: str, pack_id: str, item_id: str, n: int, mirror: bool = False,
                         deps: Deps = Depends(get_deps)) -> Response:
    png = await actions.item_page(deps, case_id, pack_id, item_id, n, mirror=mirror)
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=600"})


@router.get("/{case_id}/packs/{pack_id}/download")
async def pack_download(case_id: str, pack_id: str, deps: Deps = Depends(get_deps)) -> Response:
    data, name = await actions.pack_zip(deps, case_id, pack_id)
    from urllib.parse import quote
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})


# ---- tracker, letters and replies ------------------------------------------------------------------

@router.get("/{case_id}/tracker", response_model=TrackerView)
async def tracker(case_id: str, request: Request, deps: Deps = Depends(get_deps)) -> TrackerView:
    await actions.require_case(deps, case_id)
    await actions.refresh_clocks_if_idle(deps, case_id)
    return await tracker_view.tracker(deps, case_id, lang=page_language(request))


@router.post("/{case_id}/claims/{claim_id}/events", response_model=Claim)
async def claim_event(case_id: str, claim_id: str, body: ClaimEventIn, deps: Deps = Depends(get_deps)) -> Claim:
    return await actions.claim_event(deps, case_id, claim_id, event=body.event, on=body.on, amount=body.amount,
                                     note=body.note, by=body.by)


@router.post("/{case_id}/claims/{claim_id}/letters", response_model=Correspondence)
async def draft_letter(case_id: str, claim_id: str, body: LetterIn, deps: Deps = Depends(get_deps)) -> Correspondence:
    return await actions.draft_letter(deps, case_id, claim_id, kind=body.kind, request=body.request, by=body.by)


@router.post("/{case_id}/claims/{claim_id}/replies", response_model=Correspondence)
async def record_reply(case_id: str, claim_id: str, body: ReplyIn, deps: Deps = Depends(get_deps)) -> Correspondence:
    return await actions.record_reply(deps, case_id, claim_id, title=body.title, text=body.text, on=body.on,
                                      channel=body.channel)


@router.post("/{case_id}/letters/{corr_id}/approve", response_model=Correspondence)
async def approve_letter(case_id: str, corr_id: str, body: ByIn, deps: Deps = Depends(get_deps)) -> Correspondence:
    return await actions.approve_letter(deps, case_id, corr_id, by=body.by)


@router.post("/{case_id}/letters/{corr_id}/sent", response_model=Correspondence)
async def letter_sent(case_id: str, corr_id: str, body: SentIn, deps: Deps = Depends(get_deps)) -> Correspondence:
    return await actions.letter_sent(deps, case_id, corr_id, on=body.on, channel=body.channel, by=body.by)


@router.get("/{case_id}/letters/{corr_id}/file")
async def letter_file(case_id: str, corr_id: str, mirror: bool = False, deps: Deps = Depends(get_deps)) -> Response:
    data = await actions.letter_file(deps, case_id, corr_id, mirror=mirror)
    return Response(data, media_type="application/pdf", headers={"Content-Disposition": "inline"})


@router.post("/{case_id}/claims/{claim_id}/share", response_model=ShareOut)
async def share(case_id: str, claim_id: str, body: ShareIn, deps: Deps = Depends(get_deps)) -> ShareOut:
    out = await actions.share_desk(deps, case_id, claim_id, days=body.days, by=body.by)
    return ShareOut(token=out["token"], url=f"{deps.settings.public_web_url.rstrip('/')}/desk/{out['token']}",
                    until=out["until"])


# ---- sources, activity, checkpoints, asking ----------------------------------------------------------

@router.get("/{case_id}/sources", response_model=SourcesView)
async def sources(case_id: str, deps: Deps = Depends(get_deps)) -> SourcesView:
    await actions.require_case(deps, case_id)
    return await sources_view.sources(deps, case_id)


@router.get("/{case_id}/activity", response_model=ActivityView)
async def activity(case_id: str, limit: int = Query(200, ge=1, le=2000), deps: Deps = Depends(get_deps)) -> ActivityView:
    await actions.require_case(deps, case_id)
    from ...agents.meter import usage_from_activity
    items = sorted(await deps.cases.list(case_id, "activity"), key=lambda a: (a.at, a.id), reverse=True)
    used = usage_from_activity(items)
    return ActivityView(items=items[:limit], usage=UsageView(**used.as_dict()) if used.calls else None)


@router.get("/{case_id}/events")
async def events(case_id: str, request: Request, deps: Deps = Depends(get_deps)) -> EventSourceResponse:
    """Activity lines and 'something changed' notices for open screens, as server-sent events."""
    await actions.require_case(deps, case_id)
    queue = deps.hub.listen(case_id)

    async def stream():
        try:
            async for item in queue_events(request, queue):
                yield item
        finally:
            deps.hub.forget(case_id, queue)

    return EventSourceResponse(stream())


@router.get("/{case_id}/checkpoints", response_model=list[Checkpoint])
async def checkpoints(case_id: str, status: Literal["open", "answered", "expired", "all"] = "open",
                      deps: Deps = Depends(get_deps)) -> list[Checkpoint]:
    await actions.require_case(deps, case_id)
    items = await deps.cases.list(case_id, "checkpoints")
    return [c for c in items if status == "all" or c.status == status]


@router.post("/{case_id}/checkpoints/{checkpoint_id}/answer", response_model=Checkpoint)
async def answer(case_id: str, checkpoint_id: str, body: AnswerIn, deps: Deps = Depends(get_deps)) -> Checkpoint:
    return await actions.answer_checkpoint(deps, case_id, checkpoint_id, answer=body.answer, value=body.value,
                                           note=body.note, by=body.by)


@router.post("/{case_id}/ask", response_model=AskOut)
async def ask(case_id: str, body: AskIn, deps: Deps = Depends(get_deps)) -> AskOut:
    out = await actions.ask(deps, case_id, question=body.question, person_id=body.person_id, lang=body.lang,
                            channel=body.channel)
    answer = Utterance.model_validate(out["answer"])
    case = await deps.cases.get_case(case_id)
    cites = await cite_views(deps, answer.sources, deps.today_for(case))
    return AskOut(question=Utterance.model_validate(out["question"]), answer=answer, sources=list(cites.values()),
                  research_started=out.get("intent") == "unknown_need")

