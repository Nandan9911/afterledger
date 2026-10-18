"""The endpoint Cloud Tasks delivers background work to: the token, retries and dropped work."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from afterledger.app.actions import NotFound
from afterledger.app.main import create_app


class DeliveredQueue:
    """Stands in for CloudTasksQueue: enqueue records, run calls the registered handler."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}
        self.enqueued: list[tuple[str, dict]] = []

    def register(self, kind: str, handler: Any) -> None:
        self.handlers[kind] = handler

    async def enqueue(self, kind: str, payload: dict, *, delay_seconds: int = 0) -> str:
        self.enqueued.append((kind, payload))
        return f"t{len(self.enqueued)}"

    async def run(self, kind: str, payload: dict) -> None:
        if kind not in self.handlers:
            raise KeyError(f"no handler registered for task kind '{kind}'")
        await self.handlers[kind](payload)

    async def drain(self) -> None:
        return None


@pytest.fixture
def client(settings: Any):
    from afterledger.runtime import build_deps
    deps = build_deps(settings.model_copy(update={"internal_token": "s3cret"}))
    deps.tasks = DeliveredQueue()
    with TestClient(create_app(deps, prepare_demo=False)) as c:
        c.deps = deps
        yield c


def _post(c: TestClient, kind: str, body: Any, token: str | None = "s3cret") -> Any:
    headers = {"X-AfterLedger-Token": token} if token else {}
    return c.post(f"/internal/tasks/{kind}", json=body, headers=headers)


def test_token_is_required(client: TestClient) -> None:
    assert _post(client, "plan_today", {"case_id": "x"}, token=None).status_code == 403
    assert _post(client, "plan_today", {"case_id": "x"}, token="wrong").status_code == 403


def test_work_for_a_missing_case_is_dropped_not_retried(client: TestClient) -> None:
    r = _post(client, "plan_today", {"case_id": "case_does_not_exist"})
    assert r.status_code == 200 and r.json()["ok"] is False and "not found" in r.json()["dropped"]


def test_unknown_kind_and_bad_bodies(client: TestClient) -> None:
    assert _post(client, "no_such_work", {}).status_code == 404
    assert _post(client, "plan_today", ["not", "an", "object"]).status_code == 400


def test_failures_ask_for_a_retry(client: TestClient) -> None:
    async def boom(payload: dict) -> None:
        raise RuntimeError("model overloaded")

    async def gone(payload: dict) -> None:
        raise NotFound("claim k1")

    client.deps.tasks.register("boom", boom)
    client.deps.tasks.register("gone", gone)
    assert _post(client, "boom", {}).status_code == 500
    r = _post(client, "gone", {})
    assert r.status_code == 200 and r.json() == {"ok": False, "dropped": "claim k1"}


def test_handlers_are_registered_and_run(client: TestClient) -> None:
    assert {"after_papers", "prepare_pack", "plan_today"} <= set(client.deps.tasks.handlers)
    ran: list[dict] = []

    async def fine(payload: dict) -> None:
        ran.append(payload)

    client.deps.tasks.register("fine", fine)
    assert _post(client, "fine", {"a": 1}).json() == {"ok": True}
    assert ran == [{"a": 1}]


def test_in_process_queue_takes_no_deliveries(settings: Any) -> None:
    from afterledger.runtime import build_deps
    deps = build_deps(settings.model_copy(update={"internal_token": "s3cret"}))
    with TestClient(create_app(deps, prepare_demo=False)) as c:
        assert _post(c, "plan_today", {"case_id": "x"}).status_code == 404


def test_not_in_the_public_contract(client: TestClient) -> None:
    doc = client.get("/openapi.json").json()
    assert not any(p.startswith("/internal") for p in doc["paths"])
    meta = client.get("/v1/meta").json()
    assert meta["services"]["tasks"] == "in_process" and meta["services"]["llm"] == "scripted"
