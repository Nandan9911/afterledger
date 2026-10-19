"""Accounts: sign up, sign in, who is signed in, and the admin's table of accounts.

A sign-in travels as `Authorization: Bearer <token>`, or as `?access_token=<token>` where a browser cannot set
headers (event streams, images, downloads, the voice socket). Where sign-in is required (AL_AUTH), each case is
open only to the account that started it and to the admin; the sample cases are open to everyone; and anyone else
is told the case does not exist.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ... import accounts
from ...accounts import AccountError, User
from ...runtime import Deps
from .common import get_deps

router = APIRouter(prefix="/v1", tags=["accounts"])


# ---- who is asking ----------------------------------------------------------------------------------------

def token_of(conn: Any) -> str | None:
    """The sign-in a request or a socket carries: its Authorization header, else its access_token parameter."""
    header = conn.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return conn.query_params.get("access_token") or None


async def signed_in(deps: Deps, conn: Any) -> User | None:
    return await accounts.user_from_token(deps, token_of(conn))


async def optional_user(request: Request, deps: Deps = Depends(get_deps)) -> User | None:
    return await signed_in(deps, request)


async def required_user(request: Request, deps: Deps = Depends(get_deps)) -> User:
    user = await signed_in(deps, request)
    if user is None:
        raise HTTPException(401, "Please sign in first.")
    return user


async def admin_user(user: User = Depends(required_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Only the admin can do this.")
    return user


async def may_open(deps: Deps, case: Any, user: User | None) -> bool:
    if not deps.settings.auth_required or case.demo_key:
        return True
    return user is not None and (user.role == "admin" or case.owner_id == user.id)


SAMPLE_READ_ONLY = ("This is a sample case, kept the same for everyone who opens it, so its buttons are switched "
                    "off. Start a case of your own to try them.")


async def case_access(request: Request, deps: Deps = Depends(get_deps)) -> None:
    """Every /v1/cases/{case_id}/... route: another account's case does not exist, as far as this caller knows; and on
    the public site a sample case is read only to all but the admin (until 19 October 2026 any visitor could approve,
    upload or answer on a sample, changing it for everyone and paying for its model work)."""
    case_id = request.path_params.get("case_id")
    if not case_id or not (deps.settings.auth_required or deps.settings.product):
        return
    case = await deps.cases.get_case(case_id)
    if case is None:
        return                              # the route says it is not found
    user = await signed_in(deps, request)
    if deps.settings.auth_required and not await may_open(deps, case, user):
        raise HTTPException(404, f"No case {case_id}")
    if sample_read_only(deps, case, user) and request.method not in ("GET", "HEAD", "OPTIONS") \
            and not request.url.path.endswith("/ask"):      # a question is answered there without being saved
        raise HTTPException(403, SAMPLE_READ_ONLY)


def sample_read_only(deps: Deps, case: Any, user: User | None) -> bool:
    """On the public site a sample case stays as it is for everyone but the admin."""
    return bool(case is not None and case.demo_key and deps.settings.product
                and not (user is not None and user.role == "admin"))


def may_see_question(deps: Deps, run: dict | None, user: User | None) -> bool:
    """A live question (challenge) is its asker's, where sign-in is required."""
    if run is None or not deps.settings.auth_required or run.get("shared"):
        return True                       # research a claim needed is about rules only, so it is open to anyone
    return user is not None and (user.role == "admin" or (run.get("owner_id") is not None
                                                          and run.get("owner_id") == user.id))


# ---- the shapes -------------------------------------------------------------------------------------------

class AccountView(BaseModel):
    id: str
    name: str
    email: str
    role: Literal["user", "admin"]
    created_at: datetime


class AccountRow(AccountView):
    cases: int = 0


class SignedIn(BaseModel):
    token: str
    user: AccountView


class SignUpIn(BaseModel):
    name: str
    email: str
    password: str


class SignInIn(BaseModel):
    email: str
    password: str


class AccountIn(BaseModel):
    name: str
    email: str
    password: str


class AccountPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None


class AccountRemoved(BaseModel):
    deleted: str
    cases_removed: int
    questions_removed: int


def _view(user: User) -> AccountView:
    return AccountView(**accounts.public(user))


def _fixable(exc: AccountError) -> HTTPException:
    return HTTPException(409 if "already exists" in str(exc) else 400, str(exc))


# ---- signing up and in ------------------------------------------------------------------------------------

@router.post("/auth/signup", response_model=SignedIn)
async def sign_up(body: SignUpIn, deps: Deps = Depends(get_deps)) -> SignedIn:
    try:
        user = await accounts.create_user(deps, name=body.name, email=body.email, password=body.password)
    except AccountError as exc:
        raise _fixable(exc) from None
    return SignedIn(token=accounts.make_token(deps, user), user=_view(user))


@router.post("/auth/signin", response_model=SignedIn)
async def sign_in(body: SignInIn, deps: Deps = Depends(get_deps)) -> SignedIn:
    user = await accounts.sign_in(deps, email=body.email, password=body.password)
    if user is None:
        raise HTTPException(401, "The email or the password is not right.")
    return SignedIn(token=accounts.make_token(deps, user), user=_view(user))


@router.get("/auth/me", response_model=AccountView)
async def me(user: User = Depends(required_user)) -> AccountView:
    return _view(user)


# ---- the admin's table ------------------------------------------------------------------------------------

@router.get("/admin/users", response_model=list[AccountRow])
async def list_accounts(_: User = Depends(admin_user), deps: Deps = Depends(get_deps)) -> list[AccountRow]:
    counts = accounts.owners(await deps.cases.list_cases())
    users = sorted(await deps.users.list(), key=lambda u: (u.role != "admin", u.created_at))
    return [AccountRow(**accounts.public(u, counts.get(u.id, 0))) for u in users]


@router.post("/admin/users", response_model=AccountRow)
async def create_account(body: AccountIn, _: User = Depends(admin_user), deps: Deps = Depends(get_deps)) -> AccountRow:
    try:
        user = await accounts.create_user(deps, name=body.name, email=body.email, password=body.password)
    except AccountError as exc:
        raise _fixable(exc) from None
    return AccountRow(**accounts.public(user, 0))


async def _account(deps: Deps, user_id: str) -> User:
    user = await deps.users.get(user_id)
    if user is None:
        raise HTTPException(404, "No such account.")
    return user


@router.patch("/admin/users/{user_id}", response_model=AccountRow)
async def edit_account(user_id: str, body: AccountPatch, _: User = Depends(admin_user),
                       deps: Deps = Depends(get_deps)) -> AccountRow:
    user = await _account(deps, user_id)
    if user.role == "admin":
        raise HTTPException(400, "The admin account is set by the deployment (AL_ADMIN_EMAIL, AL_ADMIN_PASSWORD).")
    try:
        user = await accounts.update_user(deps, user, name=body.name, email=body.email, password=body.password or None)
    except AccountError as exc:
        raise _fixable(exc) from None
    counts = accounts.owners(await deps.cases.list_cases())
    return AccountRow(**accounts.public(user, counts.get(user.id, 0)))


@router.delete("/admin/users/{user_id}", response_model=AccountRemoved)
async def delete_account(user_id: str, _: User = Depends(admin_user), deps: Deps = Depends(get_deps)) -> AccountRemoved:
    user = await _account(deps, user_id)
    if user.role == "admin":
        raise HTTPException(400, "The admin account cannot be deleted.")
    out = await accounts.delete_user(deps, user)
    return AccountRemoved(deleted=user.id, **out)
