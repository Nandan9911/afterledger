"""A change asked for twice is done once.

The web app sends each change (a POST, PATCH or DELETE) with an Idempotency-Key of its own, and asks again with the
same key when the connection drops before the answer comes. The first request's answer is kept for a while; asked
again, the service sends that answer instead of doing the change a second time, or waits for it when the first is still
being worked on. So a family whose connection dropped learns whether the change went through, and an approval or a
payment is never recorded twice. A failed first try (5xx) is not kept: asked again, the change is made then.

One server holds the answers in memory (the service runs on one instance); a restart forgets them, and a change asked
again after that is checked by the action itself (an answered question cannot be answered again).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from typing import Any

KEPT_FOR = 900.0            # seconds an answer is kept
KEPT_MAX = 1000             # answers kept at most (the oldest go first)
BODY_MAX = 512 * 1024       # an answer longer than this is not kept (it is a view; asking again is safe)
WAIT_MAX = 300.0            # seconds a repeat waits for the first request to finish


class IdempotencyMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app
        self.done: OrderedDict[tuple, tuple[float, tuple]] = OrderedDict()
        self.running: dict[tuple, asyncio.Future] = {}

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        key = headers.get(b"idempotency-key")
        if not key or len(key) > 128:
            await self.app(scope, receive, send)
            return
        who = hashlib.sha256(headers.get(b"authorization", b"")).hexdigest()[:16]
        ident = (key, scope["method"], scope["path"], who)
        self._forget_old()
        kept = self.done.get(ident)
        answer = kept[1] if kept is not None else await self._finished(ident)
        if answer is not None:
            await self._replay(answer, send)
            return
        await self._first(ident, scope, receive, send)

    async def _finished(self, ident: tuple) -> tuple | None:
        """The answer of the same request still being worked on, once it is done (None when there is none, or it
        failed and is to be made again)."""
        running = self.running.get(ident)
        if running is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(running), WAIT_MAX)
        except TimeoutError:
            return None

    async def _first(self, ident: tuple, scope: dict, receive: Any, send: Any) -> None:
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self.running[ident] = future
        status: list[int] = []
        head: list[tuple[bytes, bytes]] = []
        body = bytearray()
        keep = [True]

        async def capture(message: dict) -> None:
            if message["type"] == "http.response.start":
                status.append(message["status"])
                head.extend(message.get("headers") or [])
            elif message["type"] == "http.response.body" and keep[0]:
                body.extend(message.get("body", b""))
                if len(body) > BODY_MAX:
                    keep[0] = False
                    body.clear()
            await send(message)

        answer = None
        try:
            await self.app(scope, receive, capture)
            if status and status[0] < 500 and keep[0]:
                answer = (status[0], [(k, v) for k, v in head if k.lower() != b"content-length"], bytes(body))
                self.done[ident] = (time.monotonic(), answer)
                while len(self.done) > KEPT_MAX:
                    self.done.popitem(last=False)
        finally:
            self.running.pop(ident, None)
            if not future.done():
                future.set_result(answer)

    def _forget_old(self) -> None:
        now = time.monotonic()
        while self.done:
            at, _ = next(iter(self.done.values()))
            if now - at <= KEPT_FOR:
                break
            self.done.popitem(last=False)

    @staticmethod
    async def _replay(answer: tuple, send: Any) -> None:
        status, head, body = answer
        await send({"type": "http.response.start", "status": status,
                    "headers": [*head, (b"content-length", str(len(body)).encode()), (b"idempotent-replay", b"true")]})
        await send({"type": "http.response.body", "body": body})
