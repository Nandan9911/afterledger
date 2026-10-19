"""What every router needs: the application's dependencies and server-sent event streams."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException, Request

from ...runtime import Deps


def get_deps(request: Request) -> Deps:
    return request.app.state.deps


def sse(data: Any, event: str | None = None) -> dict:
    out = {"data": json.dumps(data, ensure_ascii=False, default=str)}
    if event:
        out["event"] = event
    return out


# A live stream is closed by the server after this long and the browser opens a new one by itself (EventSource
# reconnects, after RETRY_MS). On 19 October 2026 streams of pages already left were not released by the front end
# in front of the service, and about 80 case pages viewed in 12 minutes filled every request slot of the one
# instance for an hour: since then no stream holds a slot for longer than this, whatever happens to its client.
STREAM_SECONDS = 45.0
RETRY_MS = 3000


def streams_full(deps: Deps) -> bool:
    """Whether every live-stream place (AL_SSE_MAX_STREAMS on the server) is taken, so plain requests keep room."""
    limit = int(getattr(deps.settings, "sse_max_streams", 0) or 0)
    return bool(limit) and int(deps.extras.get("sse_open", 0)) >= limit


def stream_opened(deps: Deps) -> None:
    """Called as a stream starts sending; stream_closed when it ends (in a finally)."""
    deps.extras["sse_open"] = int(deps.extras.get("sse_open", 0)) + 1


def stream_closed(deps: Deps) -> None:
    deps.extras["sse_open"] = max(0, int(deps.extras.get("sse_open", 0)) - 1)


def too_many_streams() -> HTTPException:
    return HTTPException(503, "Too many live connections right now; the page keeps refreshing by itself.",
                         headers={"Retry-After": "30"})


async def queue_events(request: Request, queue: asyncio.Queue, *, until: tuple[str, ...] = (),
                       ping: float = 10.0, seconds: float | None = STREAM_SECONDS) -> AsyncIterator[dict]:
    """Streams a queue as server-sent events until the client goes, a closing event type arrives, or `seconds` pass
    (None: no limit). The first event goes at once, so the response starts without waiting for news."""
    yield {"comment": "ready", "retry": RETRY_MS}
    loop = asyncio.get_running_loop()
    end = loop.time() + seconds if seconds else None
    while True:
        if await request.is_disconnected():
            return
        wait = ping if end is None else min(ping, end - loop.time())
        if wait <= 0:
            return                        # the browser reconnects, and the new stream takes over
        try:
            item = await asyncio.wait_for(queue.get(), timeout=wait)
        except TimeoutError:
            yield {"event": "ping", "data": "{}"}
            continue
        yield sse(item, item.get("type") if isinstance(item, dict) else None)
        if isinstance(item, dict) and item.get("type") in until:
            return
