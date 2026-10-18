"""Durable background work through Cloud Tasks.

Each task is an HTTP call back to this service (POST {AL_API_URL}/internal/tasks/{kind}) carrying the internal
token; Cloud Tasks retries it if the service fails or restarts, so reading a batch of papers or preparing a pack
survives an instance being recycled. Without a queue configured, the in-process queue is used instead.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ...ports import Handler

_DEADLINE_SECONDS = 1800      # the longest Cloud Tasks waits for an HTTP task: reading a large batch of papers


class CloudTasksQueue:
    def __init__(self, queue_path: str, api_url: str, token: str, client: Any = None):
        from google.cloud import tasks_v2
        self.tasks_v2 = tasks_v2
        if client is None:
            client = tasks_v2.CloudTasksAsyncClient()
        self.client = client
        self.queue_path = queue_path
        self.api_url = api_url.rstrip("/")
        self.token = token
        self._handlers: dict[str, Handler] = {}

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    async def enqueue(self, kind: str, payload: dict, *, delay_seconds: int = 0) -> str:
        if kind not in self._handlers:
            raise KeyError(f"no handler registered for task kind '{kind}'")
        from google.protobuf import duration_pb2
        t = self.tasks_v2
        task = t.Task(http_request=t.HttpRequest(
            http_method=t.HttpMethod.POST, url=f"{self.api_url}/internal/tasks/{kind}",
            headers={"Content-Type": "application/json", "X-AfterLedger-Token": self.token},
            body=json.dumps(payload, default=str).encode("utf-8")),
            dispatch_deadline=duration_pb2.Duration(seconds=_DEADLINE_SECONDS))
        if delay_seconds:
            from google.protobuf import timestamp_pb2
            when = timestamp_pb2.Timestamp()
            when.FromDatetime(datetime.now(UTC) + timedelta(seconds=delay_seconds))
            task.schedule_time = when
        created = await self.client.create_task(parent=self.queue_path, task=task)
        return created.name

    async def run(self, kind: str, payload: dict) -> None:
        """Called by the internal endpoint when Cloud Tasks delivers the task."""
        handler = self._handlers.get(kind)
        if handler is None:
            raise KeyError(f"no handler registered for task kind '{kind}'")
        await handler(payload)

    async def drain(self) -> None:
        """Nothing to wait for here: Cloud Tasks delivers the work."""
        return None
