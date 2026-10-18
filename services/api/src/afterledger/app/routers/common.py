"""What every router needs: the application's dependencies and server-sent event streams."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request

from ...runtime import Deps


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def sse(data: Any, event: str | None = None) -> dict:
    out = {"data": json.dumps(data, ensure_ascii=False, default=str)}
    if event:
        out["event"] = event
    return out


async def queue_events(request: Request, queue: asyncio.Queue, *, until: tuple[str, ...] = (),
                       ping: float = 15.0) -> AsyncIterator[dict]:
    """Streams a queue as server-sent events until the client goes or a closing event type arrives."""
    while True:
        if await request.is_disconnected():
            return
        try:
            item = await asyncio.wait_for(queue.get(), timeout=ping)
        except TimeoutError:
            yield {"event": "ping", "data": "{}"}
            continue
        yield sse(item, item.get("type") if isinstance(item, dict) else None)
        if isinstance(item, dict) and item.get("type") in until:
            return
