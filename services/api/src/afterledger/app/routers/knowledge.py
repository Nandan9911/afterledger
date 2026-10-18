"""Shared knowledge: a rule with its source and the page it was quoted from."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from ...domain.case import Institution
from ...modules.common import knowledge
from ...runtime import Deps
from ..schemas import AssertionView, PageSnapshotView
from ..views import sources as sources_view
from .common import get_deps

router = APIRouter(prefix="/v1/knowledge", tags=["knowledge"])


@router.get("/assertions/{assertion_id}", response_model=AssertionView)
async def assertion(assertion_id: str, deps: Deps = Depends(get_deps)) -> AssertionView:
    view = await sources_view.assertion(deps, assertion_id, deps.clock.today())
    if view is None:
        raise HTTPException(404, f"No rule {assertion_id}")
    return view


@router.get("/pages", response_model=PageSnapshotView)
async def page(url: str = Query(..., min_length=8), deps: Deps = Depends(get_deps)) -> PageSnapshotView:
    view = await sources_view.page(deps, url)
    if view is None:
        raise HTTPException(404, "That page is not in the snapshots.")
    return view


@router.get("/institutions/{institution_id}", response_model=Institution)
async def institution(institution_id: str, deps: Deps = Depends(get_deps)) -> Institution:
    inst = await knowledge(deps).institution(institution_id)
    if inst is None:
        raise HTTPException(404, f"No institution {institution_id}")
    return inst
