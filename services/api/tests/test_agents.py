"""The agent registry: every agent has its instructions, an output schema every Gemini endpoint accepts, and an
offline oracle; and live model settings follow Gemini 3's guidance (thinking level only)."""
from __future__ import annotations

import re
from typing import Any

import pytest

from afterledger.agents.specs import AGENTS, MODULES, PROMPTS, prompt_for


def test_every_agent_has_instructions_with_the_shared_rules() -> None:
    shared = (PROMPTS / "_shared.md").read_text(encoding="utf-8").strip()
    for name, spec in AGENTS.items():
        text = prompt_for(spec.module, name)
        assert text.startswith(shared[:80]), name
        assert len(text) > len(shared) + 200, f"{name}: instructions too thin"


def test_no_instructions_for_agents_that_do_not_exist() -> None:
    for module in MODULES:
        path = PROMPTS / f"{module}.md"
        if not path.exists():
            continue
        for name in re.findall(r"^## (\w+)", path.read_text(encoding="utf-8"), flags=re.M):
            assert name in AGENTS and AGENTS[name].module == module, f"{module}.md has a section for '{name}'"


@pytest.mark.parametrize("vertex", [True, False], ids=["vertex", "developer_api"])
def test_output_schemas_convert_for_gemini(vertex: bool) -> None:
    """The SDK turns each schema into Gemini's; the Developer API refuses free-form maps (additionalProperties)."""
    from google.genai import _transformers as t
    from google.genai._api_client import BaseApiClient

    client = BaseApiClient(vertexai=vertex, project="p" if vertex else None, location="global" if vertex else None,
                           api_key=None if vertex else "test-key")
    problems = []
    for name, spec in AGENTS.items():
        try:
            t.t_schema(client, spec.output)
        except ValueError as exc:
            problems.append(f"{name}: {exc}")
    assert not problems, problems


def test_the_investigator_must_give_its_signals_and_cites() -> None:
    """Gemini may leave out what a schema leaves optional, and on 15 October it left out the investigator's signals in
    99 of 185 answers, so those leads fell to the base rate and read "possible"; required, they are always written."""
    from google.genai import _transformers as t

    schema = t.t_schema(None, AGENTS["investigator"].output)
    assert {"signals", "cites"} <= set(schema.required or [])


def test_every_agent_has_an_offline_oracle(deps: Any) -> None:
    from afterledger import oracles
    for name in AGENTS:
        assert oracles.oracle_for(name, deps) is not None, name
    assert set(oracles.REGISTRY) <= set(AGENTS), "oracles for agents that do not exist"


def test_live_config_sets_thinking_level_only(deps: Any) -> None:
    from google.genai import types

    from afterledger.agents.factory import ModelProvider

    deps.settings = deps.settings.model_copy(update={"llm": "gemini"})
    provider = ModelProvider(deps)
    for spec in AGENTS.values():
        cfg = provider.config_for(spec)
        if spec.thinking is None:
            assert cfg is None
            continue
        assert isinstance(cfg, types.GenerateContentConfig)
        assert cfg.temperature is None and cfg.top_p is None
        assert cfg.thinking_config.thinking_level.value == spec.thinking.upper()


def test_scripted_agents_build(deps: Any) -> None:
    for name in AGENTS:
        agent = deps.models.agent(name)
        assert agent.name == name and agent.output_schema is AGENTS[name].output
