"""Accounts in Firestore: users/{user_id} {"json": User, "email"}.

The API runs as one instance, so the accounts are read once and kept in memory; every change reaches Firestore
before it returns. Each record is one JSON string, as the case store keeps them.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ...accounts import User, normal_email


class FirestoreUserStore:
    def __init__(self, project: str | None, database: str = "(default)", client: Any = None):
        self._project, self._database, self._client = project, database, client
        self._users: dict[str, dict] | None = None
        self._lock = asyncio.Lock()

    @property
    def db(self) -> Any:
        """The Firestore client, made on first use (a service that never touches an account needs none)."""
        if self._client is None:
            from google.cloud import firestore
            self._client = firestore.AsyncClient(project=self._project, database=self._database)
        return self._client

    async def _all(self) -> dict[str, dict]:
        if self._users is None:
            found: dict[str, dict] = {}
            async for snap in self.db.collection("users").stream():
                raw = snap.to_dict() or {}
                if "json" in raw:
                    found[snap.id] = json.loads(raw["json"])
            self._users = found
        return self._users

    async def get(self, user_id: str) -> User | None:
        raw = (await self._all()).get(user_id)
        return User.model_validate(raw) if raw else None

    async def by_email(self, email: str) -> User | None:
        email = normal_email(email)
        return next((u for u in await self.list() if u.email == email), None)

    async def list(self) -> list[User]:
        return [User.model_validate(v) for v in (await self._all()).values()]

    async def put(self, user: User) -> None:
        data = user.model_dump(mode="json")
        async with self._lock:
            users = await self._all()
            await self.db.collection("users").document(user.id).set(
                {"json": json.dumps(data, ensure_ascii=False), "email": user.email})
            users[user.id] = data

    async def delete(self, user_id: str) -> None:
        async with self._lock:
            users = await self._all()
            await self.db.collection("users").document(user_id).delete()
            users.pop(user_id, None)
