"""The six demo families: listing them, preparing them when the app starts, resetting one, and replaying
one from its first paper so the screens can be watched filling in."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..concurrency import job
from ..seed import snapshot
from ..seed.families import family_keys, load_family
from ..seed.seeder import demo_case_id, seed_family
from .actions import Conflict
from .schemas import DemoFamily

log = logging.getLogger(__name__)


def status(deps: Any) -> dict[str, dict]:
    return deps.extras.setdefault("demo_status", {})


async def families(deps: Any) -> list[DemoFamily]:
    out = []
    st = status(deps)
    for key in deps.settings.shown_families(family_keys()):
        fam = load_family(key, deps.settings.story_day)
        case = await deps.cases.get_case(demo_case_id(key))
        out.append(DemoFamily(key=key, title=fam.get("title", key), family_label=fam.get("family_label", ""),
                              where=fam.get("where", ""), spoken=fam.get("spoken", ""), shows=fam.get("shows", ""),
                              languages=fam.get("languages", []), countries=fam.get("countries", []),
                              order=int(fam.get("order", 99)), case_id=demo_case_id(key),
                              seeded=case is not None and st.get(key, {}).get("state") not in ("seeding", "watching"),
                              viewer=case.viewer_person_id if case else None, story_day=fam.story_day))
    return sorted(out, key=lambda f: f.order)


async def prepare(deps: Any, key: str, *, force: bool = False) -> dict:
    """Makes the demo case exist: from its snapshot when there is one, otherwise by playing its timeline."""
    st = status(deps)
    case_id = demo_case_id(key)
    async with job(deps, case_id):
        if not force and await deps.cases.get_case(case_id) is not None:
            st[key] = {"state": "ready"}
            return st[key]
        st[key] = {"state": "seeding", "events": 0}
        try:
            if await snapshot.restore(deps, key):
                st[key] = {"state": "ready", "restored": True}
                return st[key]

            async def progress(ev: dict) -> None:
                st[key]["events"] = st[key].get("events", 0) + 1

            report = await seed_family(deps, key, story_day=deps.settings.story_day, on_event=progress)
            snapshot.save(deps, key)
            st[key] = {"state": "ready", "events": report.events, "warnings": report.warnings,
                       "seconds": round(report.seconds, 1)}
        except Exception as exc:
            log.exception("seeding %s failed", key)
            st[key] = {"state": "error", "error": str(exc)[:300]}
    return st[key]


async def prepare_all(deps: Any) -> None:
    for key in deps.settings.shown_families(family_keys()):
        await prepare(deps, key)


def start_preparing(deps: Any) -> None:
    task = asyncio.get_running_loop().create_task(prepare_all(deps))
    deps.extras["demo_task"] = task


async def reset(deps: Any, key: str) -> dict:
    if key not in family_keys():
        raise KeyError(key)
    if watching(deps, key):
        raise Conflict("This family's story is being played from its first paper. Reset it once that has finished.")
    return await prepare(deps, key, force=True)


def listeners(deps: Any, key: str) -> set[asyncio.Queue]:
    """The open screens following a family's replay."""
    return deps.extras.setdefault("watch_listeners", {}).setdefault(key, set())


def watching(deps: Any, key: str) -> bool:
    task = deps.extras.setdefault("watch_running", {}).get(key)
    return task is not None and not task.done()


def follow(deps: Any, key: str, queue: asyncio.Queue, *, delay: float) -> bool:
    """A screen follows the family's replay: the one already playing, or a new one. A replay runs on the server
    whether or not anyone watches, so a second screen (or the same page loaded again) joins it instead of playing
    the story, and paying for it, twice. True when this call started the replay."""
    listeners(deps, key).add(queue)
    if watching(deps, key):
        return False
    task = asyncio.get_running_loop().create_task(watch(deps, key, delay=delay))
    deps.extras["watch_running"][key] = task
    return True


async def watch(deps: Any, key: str, *, delay: float) -> None:
    """Replays a family's timeline with a pause after each event, telling every screen that follows it."""
    st = status(deps)
    case_id = demo_case_id(key)

    async def tell(item: dict) -> None:
        for q in list(listeners(deps, key)):
            await q.put(item)

    async with job(deps, case_id):
        st[key] = {"state": "watching", "events": 0}
        try:
            async def progress(ev: dict) -> None:
                st[key]["events"] = st[key].get("events", 0) + 1
                await tell({"type": "event", **ev})

            report = await seed_family(deps, key, story_day=deps.settings.story_day, mode="watch", delay=delay,
                                       on_event=progress)
            st[key] = {"state": "ready", "events": report.events}
            await tell({"type": "done", "case_id": case_id, "events": report.events, "warnings": report.warnings})
        except Exception as exc:
            log.exception("watching %s failed", key)
            st[key] = {"state": "error", "error": str(exc)[:300]}
            await tell({"type": "error", "error": str(exc)[:300]})
