"""Every agent and every tool is exercised: the six demo families, both challenge presets, a paper of a kind no
layout is stored for, and questions that need the plan and the rules. A test fails here when an agent or a tool
stops being used, so nothing in the registry is dead weight."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from conftest import DemoState, offline_settings

pytestmark = pytest.mark.slow

MEMBERSHIP = ["Isar Rowing Club, Munich", "Membership certificate",
              "Member name: Hiroshi Tanaka", "Membership number: IRC-0417", "Member since: 12 April 2004",
              "Annual fee: 180.00 EUR", "Fee paid until: 31 December 2026"]


def _pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(lines):
        page.insert_text((60, 80 + 22 * i), text, fontsize=11)
    return doc.tobytes()


async def test_a_paper_of_an_unknown_kind_gets_a_schema_first(deps: Any) -> None:
    """No stored layout fits, so schema_inducer works one out from the paper; both readers then read with it, and
    the family sees that it happened."""
    from afterledger.domain.case import CaseFile
    from afterledger.modules.papers import add_upload
    from afterledger.modules.reading import read_paper
    await deps.cases.put_case(CaseFile(id="case-u", title="Test", created_at=deps.now()))
    paper = await add_upload(deps, "case-u", [("club.pdf", _pdf(MEMBERSHIP), "application/pdf")], uploaded_by=None)
    read = await read_paper(deps, "case-u", paper.id)
    schema = await deps.knowledge.get("schemas", read.doc_type)
    assert schema and schema.get("induced") and {"member_name", "membership_number"} <= {f["name"] for f in
                                                                                       schema["fields"]}
    values = {v.field: v.value for v in await deps.cases.list("case-u", "values")}
    assert values.get("membership_number") == "IRC-0417"
    assert any("No stored layout" in a.text for a in await deps.cases.list("case-u", "activity"))


def _called(root: Path) -> tuple[set[str], set[tuple[str, str]]]:
    agents: set[str] = set()
    tools: set[tuple[str, str]] = set()
    for f in sorted((root / "traces").glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            x = json.loads(line)
            if x.get("type") == "model_call":
                agents.add(x["agent"])
            elif x.get("type") == "tool_call":
                tools.add((x.get("agent") or "", x["tool"]))
    return agents, tools


def test_every_agent_and_every_tool_is_used(demo_state: DemoState, tmp_path: Path) -> None:
    import shutil

    import yaml

    from afterledger.agents.specs import AGENTS
    from afterledger.runtime import build_deps

    state = tmp_path / "state"
    shutil.copytree(demo_state.root, state)

    async def go() -> None:
        from afterledger.domain.case import CaseFile
        from afterledger.modules.challenge import start_challenge
        from afterledger.modules.conversation import ask
        from afterledger.modules.papers import add_upload
        from afterledger.modules.reading import read_paper
        deps = build_deps(offline_settings(state))
        presets = yaml.safe_load((deps.settings.knowledge_dir / "challenge_presets.yaml").read_text(encoding="utf-8"))
        for p in presets["presets"]:
            await start_challenge(deps, country=p["country"], question=p["question"], lang=p["lang"],
                                  holding_kind=p.get("holding_kind"), situation=p.get("situation"))
        for q in ("Which papers does the Narmada Bank claim need?",
                  "Which rule says the bank must settle within 15 days?",
                  "How do I close his electricity account?"):
            await ask(deps, "demo-verma", q, lang="en")
        await deps.cases.put_case(CaseFile(id="case-u", title="Test", created_at=deps.now()))
        paper = await add_upload(deps, "case-u", [("club.pdf", _pdf(MEMBERSHIP), "application/pdf")], uploaded_by=None)
        await read_paper(deps, "case-u", paper.id)
        await deps.tasks.drain()

    asyncio.run(go())
    agents, tools = _called(state)
    assert sorted(set(AGENTS) - agents) == [], "agents never called"
    expected = {(a.name, t) for a in AGENTS.values() for t in a.tools}
    assert sorted(expected - tools) == [], "tools never called by the agent that has them"
