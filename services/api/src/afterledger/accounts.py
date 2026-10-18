"""Accounts: sign up, sign in, and one admin who manages the others.

A minimal module on purpose. Passwords are hashed with scrypt (a random salt each); sign-ins are stateless tokens
signed with HMAC-SHA256 (`AL_AUTH_SECRET`), valid for seven days and revoked when the account's password changes or
the account is deleted. The one admin comes from the deployment (`AL_ADMIN_EMAIL`, `AL_ADMIN_PASSWORD`,
`AL_ADMIN_NAME`): nobody can sign up as, or be made into, an admin through the API.

Where sign-in is required (`AL_AUTH`, on by default on the public site), each case belongs to the account that
started it: only that account (and the admin) can open it, and the sample cases stay open to everyone.

    users.json (local)  or  users/{user_id} {"json": User, "email"} (Firestore)
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from .domain.ids import new_id

log = logging.getLogger(__name__)

TOKEN_DAYS = 7
MIN_PASSWORD = 8
_SCRYPT = (2 ** 14, 8, 1)                 # n, r, p: about 16 MB and a few tens of milliseconds per check
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class User(BaseModel):
    id: str
    email: str
    name: str
    role: Literal["user", "admin"] = "user"
    password_hash: str
    created_at: datetime
    token_version: int = 0


class AccountError(ValueError):
    """Something a person can fix (a short password, an email already used); the message says what."""


# ---- passwords --------------------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    n, r, p = _SCRYPT
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return "$".join(["scrypt", str(n), str(r), str(p), _b64(salt), _b64(digest)])


def check_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=_unb64(salt), n=int(n), r=int(r), p=int(p),
                             dklen=len(_unb64(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, _unb64(digest))


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---- tokens -----------------------------------------------------------------------------------------------

def secret_for(deps: Any) -> bytes:
    """The signing secret: AL_AUTH_SECRET, or one made for this process (sign-ins then end when it restarts)."""
    if deps.settings.auth_secret:
        return deps.settings.auth_secret.encode("utf-8")
    made = deps.extras.get("auth_secret")
    if made is None:
        made = deps.extras["auth_secret"] = secrets.token_bytes(32)
        if deps.settings.auth_required:
            log.warning("AL_AUTH_SECRET is not set: sign-ins are signed with a key made for this process and end "
                        "when it restarts")
    return made


def make_token(deps: Any, user: User, *, now: float | None = None, days: float = TOKEN_DAYS) -> str:
    payload = {"uid": user.id, "v": user.token_version, "exp": int((now or time.time()) + days * 86400)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64(hmac.new(secret_for(deps), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


async def user_from_token(deps: Any, token: str | None, *, now: float | None = None) -> User | None:
    """The account a token belongs to, or None when it is missing, tampered with, expired or revoked."""
    if not token or token.count(".") != 1 or len(token) > 2048:
        return None
    body, sig = token.split(".")
    try:                                    # anything that is not a token we made is no sign-in, never an error
        want = _b64(hmac.new(secret_for(deps), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(want.encode("ascii"), sig.encode("ascii")):
            return None
        payload = json.loads(_unb64(body))
        uid, version, exp = str(payload["uid"]), int(payload["v"]), int(payload["exp"])
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None
    if exp < (now or time.time()):
        return None
    user = await deps.users.get(uid)
    if user is None or user.token_version != version:
        return None
    return user


# ---- the stores -------------------------------------------------------------------------------------------

class LocalUserStore:
    """users.json in the state directory."""

    def __init__(self, root: Path):
        self.path = root / "users.json"
        self._lock = asyncio.Lock()

    def _load(self) -> dict[str, dict]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def _save(self, data: dict[str, dict]) -> None:
        from .adapters.local.stores import _atomic_write
        _atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1))

    async def get(self, user_id: str) -> User | None:
        raw = self._load().get(user_id)
        return User.model_validate(raw) if raw else None

    async def by_email(self, email: str) -> User | None:
        email = normal_email(email)
        return next((u for u in await self.list() if u.email == email), None)

    async def list(self) -> list[User]:
        return [User.model_validate(v) for v in self._load().values()]

    async def put(self, user: User) -> None:
        async with self._lock:
            data = self._load()
            data[user.id] = user.model_dump(mode="json")
            self._save(data)

    async def delete(self, user_id: str) -> None:
        async with self._lock:
            data = self._load()
            if data.pop(user_id, None) is not None:
                self._save(data)


# ---- what the API does with them --------------------------------------------------------------------------

def _writing(deps: Any) -> asyncio.Lock:
    """One change to the accounts at a time, so an email stays unique (the API runs as one instance)."""
    return deps.extras.setdefault("accounts_lock", asyncio.Lock())


def normal_email(email: str) -> str:
    return (email or "").strip().lower()


def _check(name: str | None, email: str | None, password: str | None) -> None:
    if name is not None and not name.strip():
        raise AccountError("Please give a name.")
    if name is not None and len(name.strip()) > 120:
        raise AccountError("Please give a shorter name.")
    if email is not None and (not _EMAIL.match(normal_email(email)) or len(email) > 254):
        raise AccountError("Please give a valid email address.")
    if password is not None and len(password) < MIN_PASSWORD:
        raise AccountError(f"The password needs at least {MIN_PASSWORD} characters.")
    if password is not None and len(password) > 200:
        raise AccountError("Please choose a shorter password.")


async def create_user(deps: Any, *, name: str, email: str, password: str, role: str = "user") -> User:
    _check(name, email, password)
    async with _writing(deps):
        if await deps.users.by_email(email) is not None:
            raise AccountError("An account with this email already exists.")
        user = User(id=new_id("user"), email=normal_email(email), name=name.strip(), role=role,
                    password_hash=hash_password(password), created_at=datetime.now(UTC))
        await deps.users.put(user)
    return user


async def sign_in(deps: Any, *, email: str, password: str) -> User | None:
    user = await deps.users.by_email(email)
    if user is None:
        hash_password(password)           # as slow as a real check, so the answer does not tell which was wrong
        return None
    return user if check_password(password, user.password_hash) else None


async def update_user(deps: Any, user: User, *, name: str | None = None, email: str | None = None,
                      password: str | None = None) -> User:
    _check(name, email, password)
    async with _writing(deps):
        if email is not None and normal_email(email) != user.email:
            other = await deps.users.by_email(email)
            if other is not None and other.id != user.id:
                raise AccountError("An account with this email already exists.")
            user.email = normal_email(email)
        if name is not None:
            user.name = name.strip()
        if password:
            user.password_hash = hash_password(password)
            user.token_version += 1       # every sign-in made with the old password ends
        await deps.users.put(user)
    return user


async def delete_user(deps: Any, user: User) -> dict:
    """The account, every case it started (with its papers, page images and filled forms) and its questions."""
    from .modules.challenge import knowledge
    removed = 0
    drop = getattr(deps.blobs, "delete_prefix", None)
    for case in await deps.cases.list_cases():
        if case.owner_id == user.id and not case.demo_key:
            await deps.cases.delete_case(case.id)
            if drop is not None:
                await drop(f"cases/{case.id}/")
            removed += 1
    store = knowledge(deps).store
    questions = 0
    for run in await store.list("challenges"):
        if run.get("owner_id") == user.id and run.get("id") and await store.delete("challenges", run["id"]):
            questions += 1
    async with _writing(deps):
        await deps.users.delete(user.id)
    return {"cases_removed": removed, "questions_removed": questions}


async def ensure_admin(deps: Any) -> User | None:
    """The one admin, from the deployment's settings: made if missing, kept an admin, and given the configured
    email, name and password (re-hashed only when the password changed)."""
    s = deps.settings
    if not (s.admin_email and s.admin_password):
        return None
    if not _EMAIL.match(normal_email(s.admin_email)) or len(s.admin_password) < MIN_PASSWORD:
        log.error("AL_ADMIN_EMAIL or AL_ADMIN_PASSWORD is not usable (an email, and at least %d characters)",
                  MIN_PASSWORD)
        return None
    async with _writing(deps):
        users = await deps.users.list()
        admin = next((u for u in users if u.role == "admin"), None) or \
            next((u for u in users if u.email == normal_email(s.admin_email)), None)
        if admin is None:
            admin = User(id=new_id("user"), email=normal_email(s.admin_email), name=s.admin_name or "Admin",
                         role="admin", password_hash=hash_password(s.admin_password), created_at=datetime.now(UTC))
            await deps.users.put(admin)
            log.info("made the admin account")
            return admin
        changed = False
        taken = next((u for u in users if u.email == normal_email(s.admin_email) and u.id != admin.id), None)
        if admin.email != normal_email(s.admin_email) and taken is None:
            admin.email, changed = normal_email(s.admin_email), True
        if admin.role != "admin":
            admin.role, changed = "admin", True
        if s.admin_name and admin.name != s.admin_name:
            admin.name, changed = s.admin_name, True
        if not check_password(s.admin_password, admin.password_hash):
            admin.password_hash, changed = hash_password(s.admin_password), True
            admin.token_version += 1
        if changed:
            await deps.users.put(admin)
        return admin


def public(user: User, cases: int | None = None) -> dict:
    out = {"id": user.id, "name": user.name, "email": user.email, "role": user.role,
           "created_at": user.created_at}
    if cases is not None:
        out["cases"] = cases
    return out


def owners(cases: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in cases:
        if c.owner_id and not c.demo_key:
            counts[c.owner_id] = counts.get(c.owner_id, 0) + 1
    return counts
