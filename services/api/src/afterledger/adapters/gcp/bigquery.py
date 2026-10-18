"""Traces in BigQuery: one row for every agent call (and anything else traced), for the evaluation pages and
for looking at latency and cost across runs.

Table `{project}.{dataset}.traces` (created by the infrastructure):
    at TIMESTAMP, type STRING, agent STRING, case_id STRING, ms INT64, model STRING, payload JSON,
    tokens_in INT64, tokens_out INT64, tokens_thinking INT64, cost_usd FLOAT64   (model calls only)
Rows are buffered and written in the background every few seconds. Writing a trace never fails a request: an
error is logged and the rows are dropped.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)

_FLUSH_SECONDS = 3.0
_MAX_BUFFER = 500


class BigQueryTrace:
    def __init__(self, project: str, dataset: str, client: Any = None, health: Any = None):
        self.health = health
        if client is None:
            from google.cloud import bigquery
            client = bigquery.Client(project=project)
        self.client = client
        self.table = f"{project}.{dataset}.traces"
        self.buffer: list[dict] = []
        self._task: asyncio.Task | None = None

    async def record(self, event: dict) -> None:
        self.buffer.append({
            "at": datetime.now(UTC).isoformat(), "type": str(event.get("type", "")),
            "agent": event.get("agent"), "case_id": event.get("case_id"),
            "ms": int(event["ms"]) if isinstance(event.get("ms"), (int, float)) else None,
            "model": event.get("model"), "tokens_in": event.get("tokens_in"), "tokens_out": event.get("tokens_out"),
            "tokens_thinking": event.get("tokens_thinking"), "cost_usd": event.get("cost_usd"),
            "payload": json.dumps(event, ensure_ascii=False, default=str)})
        if len(self.buffer) > _MAX_BUFFER:
            self.buffer = self.buffer[-_MAX_BUFFER:]
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._flush_soon())

    async def _flush_soon(self) -> None:
        await asyncio.sleep(_FLUSH_SECONDS)
        await self.flush()

    async def flush(self) -> None:
        rows, self.buffer = self.buffer, []
        if not rows:
            return
        try:
            errors = await asyncio.to_thread(self.client.insert_rows_json, self.table, rows)
            if errors:
                log.warning("BigQuery rejected %d trace rows: %s", len(errors), str(errors)[:300])
                self._failed(f"rejected {len(errors)} rows: {str(errors)[:200]}")
            elif self.health is not None:
                self.health.succeeded("bigquery")
        except Exception as e:
            log.warning("could not write %d trace rows to BigQuery: %s", len(rows), e)
            self._failed(e)

    def _failed(self, error: Any) -> None:
        """Recorded for the run's health check; a background flush never raises, even in a strict run."""
        if self.health is None:
            return
        with contextlib.suppress(Exception):
            self.health.failed("bigquery", error)
