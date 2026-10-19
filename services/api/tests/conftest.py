"""Shared fixtures: settings and dependencies on a scratch state directory, offline (scripted responses), and the six
demo families seeded once per test session for the slow end-to-end tests."""
from __future__ import annotations

import asyncio
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from afterledger.config import Settings, get_settings

OFFLINE = {"mode": "offline", "store": "local", "llm": "scripted", "seed_on_start": False, "demo_bundles": None,
           "internal_token": None, "llm_record_dir": None, "llm_replay_dir": None,
           # a laptop's .env may set up the public site and its fast endpoints; the tests start from the full site
           "site": "", "demo_families": "", "llm_tier_locations": "", "agent_tiers": "",
           "auth": "auto", "auth_secret": None, "admin_email": None, "admin_password": None,
           # Today planned at once after a change, so a test that drains the queue does not wait out the pause
           "today_debounce_s": 0}


def offline_settings(state_dir: Path, **more: Any) -> Settings:
    return get_settings().model_copy(update={**OFFLINE, "state_dir": state_dir, **more})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return offline_settings(tmp_path)


@pytest.fixture
def deps(settings: Settings):
    from afterledger.runtime import build_deps
    return build_deps(settings)


@dataclass
class DemoState:
    root: Path                       # a state directory holding the six demo cases
    reports: dict[str, Any]          # each family's SeedReport
    misses: list[tuple[str, str]]    # (language, text) the reviewed translations did not cover
    answered: int = 0                # model calls the offline oracles answered, counted apart from the meter


@pytest.fixture(scope="session")
def demo_state(tmp_path_factory: pytest.TempPathFactory) -> DemoState:
    """The six demo families, seeded once (about three minutes); tests work on copies of it."""
    from afterledger.runtime import build_deps
    from afterledger.seed.families import family_keys
    from afterledger.seed.seeder import seed_family

    root = tmp_path_factory.mktemp("demo_state")

    answered = [0]

    async def go() -> tuple[dict[str, Any], list[tuple[str, str]]]:
        deps = build_deps(offline_settings(root))
        _count_answers(deps, answered)
        reports = {key: await seed_family(deps, key) for key in family_keys()}
        await deps.tasks.drain()
        return reports, sorted(set(getattr(deps.translator, "misses", [])))

    reports, misses = asyncio.run(go())
    return DemoState(root, reports, misses, answered[0])


def _count_answers(deps: Any, counter: list[int]) -> None:
    """Counts every answer the offline oracles give, independently of the meter that traces model calls."""
    original = deps.models._oracle

    def oracle_for(name: str):
        oracle = original(name)

        async def counted(req: Any) -> Any:
            counter[0] += 1
            out = oracle(req)
            return await out if hasattr(out, "__await__") else out
        return counted
    deps.models._oracle = oracle_for


@pytest.fixture
def demo_copy(demo_state: DemoState, tmp_path: Path) -> Path:
    """A private copy of the seeded state, for a test that changes cases."""
    dst = tmp_path / "state"
    shutil.copytree(demo_state.root, dst)
    return dst


def api_client(state_dir: Path, **more: Any) -> Iterator[Any]:
    """A TestClient on the API over this state directory; client.settle() waits for background work."""
    from fastapi.testclient import TestClient

    from afterledger.app.main import create_app
    from afterledger.runtime import build_deps

    deps = build_deps(offline_settings(state_dir, **more))
    with TestClient(create_app(deps, prepare_demo=False)) as c:
        c.deps = deps
        c.settle = lambda: c.portal.call(deps.tasks.drain)
        yield c
