"""Which live Google Cloud services worked in this process, and which failed.

Every live adapter reports here. Normally a failure is recorded and the adapter falls back to its offline
stand-in, so the family's case keeps moving; with AL_STRICT_LIVE=true (how live test runs are made) a failure
raises ServiceFailure instead, so no test passes on a stand-in without anyone noticing. The Evaluation page and
/v1/meta show the counts, so a fallback is never silent.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


class ServiceFailure(RuntimeError):
    """A live service failed while AL_STRICT_LIVE was on."""

    def __init__(self, service: str, error: str):
        super().__init__(f"{service} failed: {error}")
        self.service = service
        self.error = error


class ServiceHealth:
    def __init__(self, strict: bool = False):
        self.strict = strict
        self.ok: dict[str, int] = {}
        self.failures: dict[str, list[dict[str, str]]] = {}
        # model endpoints ("gemini-3.8-flash", "gemini-3.8-flash@us") that were slow or overloaded, and passed over
        self.slow: dict[str, list[dict[str, str]]] = {}
        self._strikes: dict[str, int] = {}
        self._avoid_until: dict[str, float] = {}
        self._gone: set[str] = set()

    def succeeded(self, service: str) -> None:
        self.ok[service] = self.ok.get(service, 0) + 1

    def failed(self, service: str, error: BaseException | str) -> None:
        """Records a failure; raises ServiceFailure in strict mode. Call it inside the adapter's except block."""
        text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "error": text[:400]}
        self.failures.setdefault(service, []).append(entry)
        del self.failures[service][:-20]                  # the last twenty are enough to see what went wrong
        log.warning("live service %s failed: %s", service, entry["error"])
        if self.strict:
            raise ServiceFailure(service, entry["error"])

    # ---- model endpoints: slow is not failing, so these never stop a strict run ----

    def route_slow(self, route: str, error: BaseException | str, cooldown_s: float) -> float:
        """An endpoint ran past a request's deadline or was overloaded: it is passed over for cooldown_s, twice as
        long each further time in a row (at most an hour). Returns how long."""
        if self.route_avoided(route):        # already passed over: calls that were under way when it was found slow
            n = self._strikes.get(route, 1)  # are the same spell of slowness, not a further one
        else:
            n = self._strikes.get(route, 0) + 1
            self._strikes[route] = n
        wait = min(3600.0, float(cooldown_s) * 2 ** (n - 1))
        self._avoid_until[route] = max(self._avoid_until.get(route, 0.0), time.monotonic() + wait)
        text = error if isinstance(error, str) else f"{type(error).__name__}: {error}"
        entry = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "error": text[:300]}
        self.slow.setdefault(route, []).append(entry)
        del self.slow[route][:-20]
        log.warning("model endpoint %s passed over for %ds: %s", route, wait, entry["error"])
        return wait

    def route_ok(self, route: str) -> None:
        self.succeeded(f"gemini:{route}")
        if self._strikes.pop(route, None):
            self._avoid_until.pop(route, None)

    def route_avoided(self, route: str) -> bool:
        return time.monotonic() < self._avoid_until.get(route, 0.0)

    def route_gone(self, route: str) -> None:
        self._gone.add(route)

    def route_missing(self, route: str) -> bool:
        return route in self._gone

    def snapshot(self) -> dict[str, Any]:
        return {"strict": self.strict, "ok": dict(self.ok),
                "failures": {s: {"count": len(v), "last": v[-1]} for s, v in self.failures.items() if v},
                "slow": {r: {"count": len(v), "last": v[-1], "passed_over": self.route_avoided(r)}
                         for r, v in self.slow.items() if v}}


def health_of(deps: Any) -> ServiceHealth:
    h = deps.extras.get("health")
    if h is None:
        h = ServiceHealth(strict=bool(getattr(deps.settings, "strict_live", False)))
        deps.extras["health"] = h
    return h
