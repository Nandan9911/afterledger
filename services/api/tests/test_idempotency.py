"""A change asked for again with the same Idempotency-Key is answered, not done twice (app/idempotency.py): the web app
asks again when the connection drops before the answer comes, and so learns whether the change went through."""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from afterledger.app.idempotency import IdempotencyMiddleware
from conftest import api_client


class _Counting:
    """An ASGI app that counts its calls, answers each with its number, and can be slow or fail."""

    def __init__(self, *, slow: float = 0.0, fail_first: bool = False) -> None:
        self.calls = 0
        self.slow, self.fail_first = slow, fail_first

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        self.calls += 1
        n = self.calls
        if self.slow:
            await asyncio.sleep(self.slow)
        status = 500 if self.fail_first and n == 1 else 200
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": f"call {n}".encode()})


async def _ask(app: Any, *, key: str | None = "k1", method: str = "POST", path: str = "/v1/x",
               auth: bytes = b"") -> tuple[int, bytes, dict]:
    headers = [(b"idempotency-key", key.encode())] if key else []
    if auth:
        headers.append((b"authorization", auth))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    out: dict = {"body": b""}

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            out["status"], out["headers"] = message["status"], dict(message["headers"])
        else:
            out["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return out["status"], out["body"], out["headers"]


def test_a_change_asked_for_again_is_answered_from_the_first_time() -> None:
    inner = _Counting()
    app = IdempotencyMiddleware(inner)

    async def go() -> list:
        return [await _ask(app), await _ask(app), await _ask(app, key="k2"), await _ask(app, key=None),
                await _ask(app, method="GET"), await _ask(app, path="/v1/y"), await _ask(app, auth=b"Bearer other")]

    first, again, other_key, no_key, read, other_path, other_person = asyncio.run(go())
    assert first[1] == again[1] == b"call 1" and again[2][b"idempotent-replay"] == b"true"
    assert again[2][b"content-length"] == b"6"
    # another key, no key, a read, another path or another person's sign-in is a request of its own
    assert [r[1] for r in (other_key, no_key, read, other_path, other_person)] == [
        b"call 2", b"call 3", b"call 4", b"call 5", b"call 6"]
    assert inner.calls == 6


def test_a_repeat_sent_while_the_first_is_worked_on_waits_for_its_answer() -> None:
    inner = _Counting(slow=0.3)
    app = IdempotencyMiddleware(inner)

    async def go() -> list:
        return list(await asyncio.gather(_ask(app), _ask(app)))

    a, b = asyncio.run(go())
    assert a[1] == b[1] == b"call 1" and inner.calls == 1


def test_a_first_try_that_failed_is_made_again() -> None:
    inner = _Counting(fail_first=True)
    app = IdempotencyMiddleware(inner)

    async def go() -> list:
        return [await _ask(app), await _ask(app), await _ask(app)]

    failed, made, repeated = asyncio.run(go())
    assert failed[0] == 500 and made == (200, b"call 2", made[2]) and repeated[1] == b"call 2"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Any]:
    yield from api_client(tmp_path)


def test_through_the_api_a_case_started_twice_with_one_key_is_one_case(client: Any) -> None:
    key = {"Idempotency-Key": "start-1"}
    a = client.post("/v1/cases", json={"lang": "en", "starter_name": "Asha"}, headers=key)
    b = client.post("/v1/cases", json={"lang": "en", "starter_name": "Asha"}, headers=key)
    assert a.status_code == b.status_code == 200
    assert a.json()["case"]["id"] == b.json()["case"]["id"] and b.headers["idempotent-replay"] == "true"
    mine = [c for c in client.portal.call(client.deps.cases.list_cases) if not c.demo_key]
    assert len(mine) == 1
