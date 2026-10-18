"""The institution's read-only desk view of one shared claim."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from ...runtime import Deps
from .. import actions
from ..schemas import DeskRequestIn, DeskSettleIn, DeskView
from ..views import desk as desk_view
from .common import get_deps

router = APIRouter(prefix="/v1/desk", tags=["desk"])


@router.get("/{token}", response_model=DeskView)
async def desk(token: str, deps: Deps = Depends(get_deps)) -> DeskView:
    return await _view(deps, await actions.desk_lookup(deps, token))


async def _view(deps: Deps, share: dict) -> DeskView:
    view = await desk_view.desk(deps, share["case_id"], share["claim_id"], until=date.fromisoformat(share["until"]))
    if view is None:
        raise HTTPException(404, "This claim is no longer shared.")
    return view


@router.post("/{token}/settle", response_model=DeskView)
async def settle(token: str, body: DeskSettleIn, deps: Deps = Depends(get_deps)) -> DeskView:
    """The institution records that it has paid. The family sees it at once."""
    share = await actions.desk_lookup(deps, token)
    note = "Recorded by the institution on the AfterLedger desk." + (f" {body.note}" if body.note else "")
    await actions.claim_event(deps, share["case_id"], share["claim_id"], event="settled", on=body.on,
                              amount=body.amount, note=note, by=None)
    return await _view(deps, share)


@router.post("/{token}/request", response_model=DeskView)
async def request_more(token: str, body: DeskRequestIn, deps: Deps = Depends(get_deps)) -> DeskView:
    """The institution asks for something else. It reaches the family as a reply, read against the rules, so a
    request the rules do not allow is shown beside the paragraph it conflicts with."""
    share = await actions.desk_lookup(deps, token)
    inst = await actions.knowledge(deps).institution(share.get("institution_id")) if share.get("institution_id") else None
    who = (inst.short_name or inst.name) if inst else "The institution"
    text = body.text + (f"\n\nRule relied on: {body.rule}" if body.rule else "")
    await actions.record_reply(deps, share["case_id"], share["claim_id"], title=f"{who} asked for something else",
                               text=text, on=None, channel="online")
    return await _view(deps, share)
