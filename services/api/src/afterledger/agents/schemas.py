"""Structured outputs of every agent. Agents are ADK LlmAgents with one of these as output_schema,
so a live Gemini model and the offline oracle must both produce exactly these shapes."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Tri = Literal["yes", "no", "unknown"]


# ---- M1 intake ------------------------------------------------------------------------------
class ListenerOut(BaseModel):
    lang: str = Field(description="BCP-47 language of the utterance")
    text: str = Field(description="What was said, in the original language and script")
    translation: str = Field(description="English translation; the same text if already English")
    intent: Literal["story", "answer", "question", "correction", "other"] = "story"


class FamilyMember(BaseModel):
    name: str
    name_en: str | None = Field(None, description="The name in Latin letters, when it is written in another script")
    relationship: str = Field(description="To the person who died: wife, son, mother...")
    city: str | None = None
    country: str | None = None
    languages: list[str] = []
    roles: list[str] = []
    alive: bool = True
    source_utterances: list[str] = []


class SituationOut(BaseModel):
    deceased_name: str = ""
    deceased_name_en: str | None = Field(None, description="In Latin letters, when written in another script")
    family_label: str | None = Field(None, description="How to call the family in English: 'The Satos'")
    died_on: str | None = Field(None, description="ISO date if said")
    died_in: str | None = None
    death_country: str | None = None
    country_codes: list[str] = []
    occupation: str | None = None
    family: list[FamilyMember] = []
    speaker_relationship: str | None = Field(None, description="What the person speaking is to the person who died "
                                                                "(wife, son...), when they said it")
    holdings_mentioned: list[str] = []
    papers_at_hand: list[str] = []
    urgent: list[str] = []
    will: Tri = "unknown"
    dispute: Tri = Field("unknown", description="Whether anyone in the family disagrees about who inherits")
    death_cause: Literal["illness", "accident", "other", "unknown"] = Field(
        "unknown", description="Only if the family said how the person died")
    mode: Literal["family", "helper", "planning_ahead"] = "family"
    unknowns: list[str] = []


class GapOut(BaseModel):
    done: bool = Field(description="True when nothing more is needed before the summary")
    question: str = Field("", description="The single next question, in English")
    why: str = Field("", description="Why it helps, in one sentence")
    cites: list[str] = Field([], description="Assertion ids behind the question, if any")
    topic: str = ""


class ToneOut(BaseModel):
    ok: bool
    question: str = Field(description="The question, possibly rewritten, in English")
    question_local: str = Field(description="The question in the family member's language")
    lang: str
    changes: str = ""


class LocaleOut(BaseModel):
    languages: list[str]
    locale: str
    scripts: list[str] = []
    channel: Literal["voice", "text"] = "text"


# ---- M2 jurisdiction ---------------------------------------------------------------------------
class Factor(BaseModel):
    kind: Literal["death_place", "residence", "nationality", "holding_country", "personal_law", "domicile",
                  "property_location"]
    value: str
    evidence: str = ""


class FactorsOut(BaseModel):
    factors: list[Factor]


class JurisdictionFinding(BaseModel):
    assertion_ids: list[str] = []
    rule: str
    points_to: str = Field(description="ISO country code the rule points to, or 'contested'")
    applies_to: str = "succession as a whole"


class JurisdictionResearchOut(BaseModel):
    findings: list[JurisdictionFinding]
    gaps: list[str] = []


class LegOut(BaseModel):
    holding_id: str | None = None
    label: str
    succession_law: str | None = None
    procedure_jurisdiction: str
    papers_from: list[str] = []
    certification: list[str] = []
    cites: list[str] = []
    contested: bool = False
    note: str = ""


class JurisdictionPlanOut(BaseModel):
    legs: list[LegOut]
    succession_law: str | None = None
    contested: bool = False
    summary: str = ""
    summary_local: str = Field("", description="The summary in the family's language, when it is not English")
    cites: list[str] = []


class AdjudicationOut(BaseModel):
    contested: bool
    decision: str
    keep: list[str] = []
    contested_ids: list[str] = []
    reasons: str = ""


# ---- M3 reading -------------------------------------------------------------------------------
class TriageOut(BaseModel):
    doc_type: str
    country: str | None = None
    languages: list[str] = []
    scripts: list[str] = []
    issuer: str | None = None
    is_photo: bool = False
    quality: Literal["good", "fair", "poor"] = "good"
    title: str = ""
    known_schema: bool = True


class SchemaField(BaseModel):
    name: str
    label: str
    kind: Literal["text", "name", "date", "amount", "identifier", "choice"] = "text"
    required: bool = False
    tier: Literal["A", "B", "C"] = "B"


class SchemaOut(BaseModel):
    doc_type: str
    fields: list[SchemaField]
    has_transactions: bool = False


class FieldReading(BaseModel):
    field: str
    value: str | None = None
    line_ids: list[str] = []
    page: int | None = None
    confidence: float = 0.9


class RowReading(BaseModel):
    row: int
    date: str
    narration: str
    amount: str
    direction: Literal["debit", "credit"]
    balance: str | None = None
    page: int | None = None
    line_ids: list[str] = []


class ReaderOut(BaseModel):
    fields: list[FieldReading] = []
    rows: list[RowReading] = []
    notes: str = ""


class TieBreakOut(BaseModel):
    field: str
    value: str | None = None
    reason: str
    confidence: float = 0.9


# ---- M4 people and institutions ----------------------------------------------------------------------
class NameParts(BaseModel):
    text: str
    script: str = "Latn"
    given: str | None = None
    family: str | None = None
    initials: str | None = None
    reading: str | None = None
    order: Literal["given_family", "family_given", "unknown"] = "unknown"


class NamesOut(BaseModel):
    names: list[NameParts]


class Merge(BaseModel):
    name_text: str
    seen_on: str | None = None
    person_id: str | None = Field(None, description="Existing person this name belongs to, or null for a new person")
    new_person: str | None = Field(None, description="Display name for a new person")
    relationship: str | None = None
    confidence: float = 0.8
    evidence: str = ""
    question: str | None = Field(None, description="For a person only payments show: the question for the family")
    question_local: str | None = Field(None, description="The question in the family member's own language")
    why: str | None = Field(None, description="Why the answer matters, in one sentence")


class IdentityOut(BaseModel):
    merges: list[Merge]


class CriticVerdict(BaseModel):
    name_text: str
    keep: bool
    reason: str
    ask_family: bool = False
    question: str | None = None


class MergeCriticOut(BaseModel):
    verdicts: list[CriticVerdict]


class InstitutionMatch(BaseModel):
    text: str
    institution_id: str | None = None
    confidence: float
    reason: str = ""


class InstitutionsOut(BaseModel):
    matches: list[InstitutionMatch]


# ---- M5 discovery ------------------------------------------------------------------------------
class Convention(BaseModel):
    prefix: str = Field(description="As printed on statements")
    meaning: str


class RailOut(BaseModel):
    country: str
    conventions: list[Convention] = []


class NarrationOut(BaseModel):
    group_id: str
    counterparty: str | None = None
    counterparty_kind: str | None = None
    interpretation: str
    alternatives: list[str] = []


class Hypothesis(BaseModel):
    key: str
    kind: Literal["asset", "debt", "entitlement", "stop_payment", "refund"]
    category: str
    holding_kind: str | None = Field(None, description="The kind of holding it would be on the money map, if any: "
                                     "savings, life_policy, premium_bonds, social_security, pension...")
    title: str
    title_local: str = Field("", description="The title in the family's language, when it is not English")
    group_ids: list[str] = []
    paper_ids: list[str] = []
    institution_id: str | None = None
    reason: str = ""


class HypothesesOut(BaseModel):
    hypotheses: list[Hypothesis]


class Signals(BaseModel):
    """What the calibration table looks up: each lead's certainty is measured from these, not from a model."""
    cadence: Literal["yearly", "monthly", "four_weekly", "quarterly", "irregular"] | None = None
    product_match: Literal["yes", "partial", "no"] | None = None
    paper: Literal["yes", "no"] | None = None
    debt: Literal["yes", "no"] | None = None
    known_rent: Literal["yes", "no"] | None = None
    source: Literal["family_note"] | None = None
    confirmed: Literal["yes", "no"] | None = None


class InvestigationOut(BaseModel):
    key: str
    sentence: str = Field(description="What we think, for the family")
    sentence_local: str = Field("", description="The same in the family's language, when it is not English")
    investigator_note: str
    how_to_confirm: str
    next_step_note: str = ""
    # Required, not defaulted: a live model left both out of over half its answers when they were optional, and a
    # lead without signals falls to the base rate, so every lead read "possible" whatever the payments showed
    signals: Signals = Field(description="Calibration signals")
    cites: list[str] = Field(description="The assertion ids relied on (an empty list when none)")
    value: str | None = None


class CriticOut(BaseModel):
    key: str
    critic_note: str
    alternatives: list[str] = []
    downgrade: bool = False


class CoverageItemOut(BaseModel):
    id: str
    label: str
    status: Literal["found", "lead", "not_seen", "not_applicable"]
    detail: str = ""
    ask: str | None = None


class CoverageOut(BaseModel):
    items: list[CoverageItemOut]


class DebtOut(BaseModel):
    key: str
    title: str
    sentence: str
    category: str = Field("loan", description="loan, card_loan, credit_card...")
    holding_kind: str | None = None
    title_local: str = ""
    sentence_local: str = ""
    group_ids: list[str] = []
    institution_id: str | None = None
    monthly: str | None = None
    next_due: str | None = None
    cites: list[str] = []


class LiabilitiesOut(BaseModel):
    debts: list[DebtOut]


# ---- M6 research and plans ----------------------------------------------------------------------
class ResearchQuestion(BaseModel):
    id: str
    question: str
    variable: str | None = None
    language: str = "en"
    queries: list[str] = []


class QuestionPlanOut(BaseModel):
    questions: list[ResearchQuestion]


class FoundAssertion(BaseModel):
    id: str
    text: str
    quote: str
    quote_translation: str | None = None
    source_url: str
    jurisdiction: str
    topic: str = ""
    question_id: str | None = None


class ResearcherOut(BaseModel):
    assertions: list[FoundAssertion] = []
    pages_read: list[str] = []
    dropped: list[str] = []
    gaps: list[str] = []


class AdjudicatedAssertion(BaseModel):
    id: str
    verdict: Literal["accept", "reject", "contested"]
    reason: str = ""
    settled_text: str | None = Field(None, description="When the two researchers worded the same quote "
                                     "differently: the wording the quote itself supports")
    same_as: list[str] = Field([], description="Ids of the other researcher's assertions that state the same "
                                "point; they are merged into this one")


class ResearchAdjudicationOut(BaseModel):
    decisions: list[AdjudicatedAssertion]
    summary: str = ""
    re_research: list[str] = []


# The compiled plan, as the compiler writes it: the procedure-plan language with conditions as text (code parses
# them) and no free-form maps, so the same schema works on every Gemini endpoint.
Certification = Literal["original", "certified_copy", "self_attested", "notarised", "stamp_paper", "apostille",
                        "consular", "translation"]


class PlanVariableOut(BaseModel):
    name: str
    type: Literal["tri", "number", "money", "date", "choice", "text"]
    question: str | None = Field(None, description="How to ask the family, in plain words")
    derive: str | None = Field(None, description="A derivation code knows, such as holding_nominee")
    options: list[str] = []
    unit: str | None = None
    cites: list[str] = []


class PlanDocumentOut(BaseModel):
    id: str
    name: str
    certification: list[Certification] = []
    acquire_plan: str | None = None
    form_key: str | None = None
    signed_by: list[str] = []
    when: str | None = Field(None, description="Condition, in the plan language")
    note: str = ""
    cites: list[str] = []


class PlanClockOut(BaseModel):
    id: str
    label: str = ""
    short: str = ""
    kind: Literal["deadline", "window", "wait"] = "deadline"
    owner: Literal["institution", "family"]
    starts_on: str
    ends_on: str = ""
    length: int
    unit: Literal["calendar_days", "business_days", "months"]
    holidays: str | None = None
    notes: str = ""
    when: str | None = None
    cites: list[str] = []


class PlanStepOut(BaseModel):
    id: str
    kind: Literal["ask", "acquire", "fill", "sign", "certify", "submit", "notify", "wait", "follow_up", "handoff",
                  "decide", "info"]
    actor: Literal["family", "institution", "authority", "professional", "agent"]
    text: str
    when: str | None = Field(None, description="Condition, in the plan language")
    documents: list[str] = []
    one_of: list[list[str]] = []
    forms: list[str] = []
    must_not_be_asked: list[str] = []
    clocks: list[str] = []
    after: list[str] = []
    decides: str | None = None
    cites: list[str] = []


class PlanOut(BaseModel):
    key: str
    title: str = ""
    jurisdiction: str
    institution_kind: str = ""
    holding_kinds: list[str] = []
    language: str = "en"
    variables: list[PlanVariableOut] = []
    documents: list[PlanDocumentOut] = []
    clocks: list[PlanClockOut] = []
    steps: list[PlanStepOut] = []
    compiled_from: list[str] = []
    unverified: bool = False
    notes: str = ""


class CompiledPlanOut(BaseModel):
    plan: PlanOut = Field(description="A procedure plan in the plan language")
    notes: str = ""


class ExplainedStep(BaseModel):
    step_id: str
    text: str = Field(description="In English")
    text_local: str = Field("", description="In the viewer's language, when it is not English")
    who: str = ""
    cites: list[str] = Field([], description="Assertion ids, only for a step that is not one of the plan's own")


class ExplainOut(BaseModel):
    summary: str = Field(description="In English")
    summary_local: str = Field("", description="In the viewer's language, when it is not English")
    steps: list[ExplainedStep]
    lang: str = Field("en", description="The viewer's language")


# ---- M7 forms and letters --------------------------------------------------------------------------
class FormScoutOut(BaseModel):
    form_key: str | None = None
    source_url: str | None = None
    letter_instead: bool = False
    reason: str = ""


class FieldMeaning(BaseModel):
    field_id: str
    meaning: str
    fill_by: Literal["agent", "family", "institution", "witness", "notary"] = "agent"


class FormUnderstandingOut(BaseModel):
    form_key: str
    fields: list[FieldMeaning]
    conditions: list[str] = []


class ComposedValue(BaseModel):
    field_id: str
    value: str
    source: str = ""
    certainty: Literal["sure", "check", "unknown"] = "sure"
    note: str = ""


class ComposeOut(BaseModel):
    values: list[ComposedValue]


class VisualCriticOut(BaseModel):
    ok: bool
    problems: list[str] = []


class LetterOut(BaseModel):
    subject: str
    body: str
    lang: str
    salutation: str = ""
    closing: str = ""
    body_en: str = Field("", description="An English copy, when the letter is in another language")
    subject_en: str = ""


class BackTranslationOut(BaseModel):
    back_translation: str
    differences: list[str] = []
    ok: bool = True


# ---- M8 follow-up ------------------------------------------------------------------------------------
class TaskOut(BaseModel):
    key: str
    kind: str
    title: str
    body: str = ""
    title_local: str = Field("", description="The title in the language of the person it is for, if not English")
    body_local: str = ""
    lang_local: str = ""
    owner_person_ids: list[str] = []
    claim_id: str | None = None
    due_on: str | None = None
    priority: float = 0.5
    cites: list[str] = []


class SupervisorOut(BaseModel):
    greeting: str
    greeting_local: str = ""
    tasks: list[TaskOut]


class CorrespondenceOut(BaseModel):
    summary: str
    requests: list[str] = []
    outcome: str | None = None
    complete_set_confirmed_on: str | None = None
    settled_amount: str | None = None
    objection: str | None = None


class EscalationOut(BaseModel):
    subject: str
    body: str
    lang: str = "en"
    kind: Literal["reminder", "objection", "grievance", "ombudsman", "request"] = "reminder"


# ---- M9 conversation ---------------------------------------------------------------------------------
class IntentOut(BaseModel):
    intent: Literal["case_question", "how_to", "unknown_need", "outside", "smalltalk"]
    topic: str = ""
    lang: str = "en"
    question_en: str = Field("", description="The question in English, when it was asked in another language")


class AnswerOut(BaseModel):
    answer: str
    answer_en: str
    lang: str
    sources: list[str] = []
    follow_ups: list[str] = []
    grounded_on: list[str] = Field(default_factory=list, description="Facts or quotes the answer rests on")


class UnknownNeedOut(BaseModel):
    can_answer: bool
    answer: str
    answer_en: str = ""
    research_request: str | None = None
    lang: str = "en"
    sources: list[str] = Field([], description="URLs of the official pages the answer rests on")
    cites: list[str] = Field([], description="Assertion ids, when the answer rests on stored rules")
    found_official: bool = Field(False, description="True only if an official page supports the answer")
    general: list[str] = Field([], description="What is usually asked, labelled as general guidance, not a rule")
    general_en: list[str] = []
