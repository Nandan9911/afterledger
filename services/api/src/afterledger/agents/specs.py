"""The agent registry: every agent's module, model tier, thinking level, output schema and tools.

Instructions live in agents/prompts/<module>.md under a '## <agent name>' heading, so they can be
reviewed and versioned as text. The model tier maps to a model id in settings (flash, pro, lite).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from . import schemas as S

PROMPTS = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class AgentSpec:
    name: str
    module: str
    tier: str                      # flash | pro | lite
    thinking: str | None           # low | medium | high | None
    output: type[BaseModel]
    tools: tuple[str, ...] = ()
    description: str = ""
    prompt_version: str = "2026-10-15"

    @property
    def instruction(self) -> str:
        return prompt_for(self.module, self.name)


def _a(name: str, module: str, tier: str, thinking: str | None, output: type[BaseModel],
       tools: tuple[str, ...] = (), description: str = "") -> AgentSpec:
    return AgentSpec(name, module, tier, thinking, output, tools, description)


AGENTS: dict[str, AgentSpec] = {a.name: a for a in [
    # M1 intake
    _a("listener", "intake", "lite", "low", S.ListenerOut, description="Transcribes and translates each turn"),
    _a("situation_extractor", "intake", "lite", "low", S.SituationOut,
       description="Keeps the situation model up to date from the conversation"),
    _a("gap_planner", "intake", "lite", "low", S.GapOut, description="Chooses the one next question worth asking"),
    _a("tone_critic", "intake", "lite", "low", S.ToneOut, description="Checks tone and puts the question in the family's language"),
    _a("locale_detector", "intake", "lite", "low", S.LocaleOut, description="Languages, scripts and channel"),
    # M2 jurisdiction
    _a("connecting_factors", "jurisdiction", "flash", "low", S.FactorsOut,
       description="Places, nationalities and residences that decide whose rules apply"),
    _a("jurisdiction_researcher", "jurisdiction", "flash", "high", S.JurisdictionResearchOut, ("search_knowledge",),
       description="Finds the conflict-of-laws rules for these factors"),
    _a("jurisdiction_verifier", "jurisdiction", "flash", "medium", S.AdjudicationOut,
       description="Checks each finding against its quote"),
    _a("jurisdiction_planner", "jurisdiction", "flash", "medium", S.JurisdictionPlanOut,
       description="One leg per holding: whose law, which procedure, where papers come from"),
    _a("jurisdiction_adjudicator", "jurisdiction", "pro", None, S.AdjudicationOut,
       description="Decides whether two rules really conflict"),
    # M3 reading
    _a("triage", "reading", "lite", "low", S.TriageOut, description="What kind of paper this is"),
    _a("schema_inducer", "reading", "flash", "medium", S.SchemaOut, description="Fields for an unfamiliar paper"),
    _a("reader_a", "reading", "flash", "medium", S.ReaderOut, description="Reads from the OCR text layer"),
    _a("reader_b", "reading", "flash", "medium", S.ReaderOut, description="Reads from the page image"),
    _a("reader_c", "reading", "pro", None, S.TieBreakOut, description="Looks closely where the two readings disagree"),
    # M4 people and institutions
    _a("name_splitter", "people", "lite", "low", S.NamesOut, description="Splits names into parts in any script"),
    _a("identity_resolver", "people", "flash", "medium", S.IdentityOut, description="Which names are the same person"),
    _a("merge_critic", "people", "flash", "medium", S.MergeCriticOut, description="Challenges each merge"),
    _a("institution_resolver", "people", "flash", "low", S.InstitutionsOut,
       description="Payees and issuers to institutions in the directory"),
    # M5 discovery
    _a("rail_profiler", "discovery", "lite", "low", S.RailOut, description="How this country's statements name payments"),
    _a("narration_interpreter", "discovery", "flash", "low", S.NarrationOut, description="What a payment group is"),
    _a("hypothesis_generator", "discovery", "flash", "medium", S.HypothesesOut, description="What the payments point to"),
    _a("investigator", "discovery", "flash", "high", S.InvestigationOut, ("get_rows", "search_knowledge"),
       description="Builds the case for one lead"),
    _a("devils_advocate", "discovery", "flash", "medium", S.CriticOut, description="Argues against each lead"),
    _a("coverage_auditor", "discovery", "flash", "medium", S.CoverageOut, description="What families here usually hold"),
    _a("liability_scout", "discovery", "flash", "medium", S.LiabilitiesOut, description="Debts and payments to stop"),
    # M6 research and plans
    _a("question_planner", "research", "flash", "high", S.QuestionPlanOut, description="Atomic research questions"),
    _a("researcher_primary", "research", "flash", "medium", S.ResearcherOut, ("web_search", "fetch_page"),
       description="Official sources first"),
    _a("researcher_second", "research", "flash", "high", S.ResearcherOut, ("web_search", "fetch_page"),
       description="A separate search in the country's language"),
    _a("research_adjudicator", "research", "pro", None, S.ResearchAdjudicationOut,
       description="Accepts, rejects or marks contested"),
    _a("procedure_compiler", "research", "pro", None, S.CompiledPlanOut, description="Compiles assertions into a plan"),
    _a("explainer", "planning", "flash", "low", S.ExplainOut, description="The plan in plain words"),
    # M7 forms and letters
    _a("form_scout", "forms", "flash", "medium", S.FormScoutOut, description="Finds the current blank form"),
    _a("form_understander", "forms", "flash", "high", S.FormUnderstandingOut, description="What each field means"),
    _a("visual_critic", "forms", "flash", "medium", S.VisualCriticOut, description="Looks at the filled pages"),
    _a("value_composer", "forms", "flash", "low", S.ComposeOut, description="Each value in the form's script and format"),
    _a("letter_writer", "forms", "flash", "medium", S.LetterOut, description="Letters in the institution's language"),
    _a("back_translator", "forms", "flash", "low", S.BackTranslationOut, description="Checks a translation by translating back"),
    # M8 follow-up
    _a("supervisor", "followup", "flash", "medium", S.SupervisorOut, description="Today's few tasks"),
    _a("correspondence_reader", "followup", "flash", "medium", S.CorrespondenceOut, description="Reads replies"),
    _a("escalation_writer", "followup", "flash", "medium", S.EscalationOut, description="Reminders and objections"),
    # M9 conversation
    _a("intent_router", "conversation", "lite", "low", S.IntentOut, description="What the person is asking"),
    _a("case_answerer", "conversation", "flash", "medium", S.AnswerOut, ("case_lookup", "plan_lookup", "source_lookup"),
       description="Answers from the case, with sources"),
    _a("unknown_need_handler", "conversation", "flash", "high", S.UnknownNeedOut, ("web_search", "fetch_page"),
       description="Needs the case does not cover yet: a first answer from an official page"),
]}

MODULES = ["intake", "jurisdiction", "reading", "people", "discovery", "research", "planning", "forms", "followup",
           "conversation"]


@lru_cache
def _module_prompts(module: str) -> dict[str, str]:
    path = PROMPTS / f"{module}.md"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    common = ""
    shared = PROMPTS / "_shared.md"
    if shared.exists():
        common = shared.read_text(encoding="utf-8").strip() + "\n\n"
    out: dict[str, str] = {}
    for m in re.finditer(r"^## (\w+)\s*\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M):
        out[m.group(1)] = common + m.group(2).strip()
    return out


def prompt_for(module: str, name: str) -> str:
    prompts = _module_prompts(module)
    if name not in prompts:
        raise KeyError(f"No prompt for agent '{name}' in prompts/{module}.md")
    return prompts[name]
