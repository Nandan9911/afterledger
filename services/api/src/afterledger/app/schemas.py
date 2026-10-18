"""Request bodies and the read models each screen is drawn from.

Records from the case graph (people, papers, holdings, plans, packs...) are embedded as they are;
these models add what a screen needs on top: names resolved, amounts formatted the way the
letters format them, sources resolved to their quotes, and counts. Every model here is exported
to OpenAPI and becomes a TypeScript type in packages/contracts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..domain.case import (
    Activity,
    CaseFile,
    Checkpoint,
    Claim,
    ClockState,
    Correspondence,
    Holding,
    Institution,
    Lead,
    NameForm,
    Pack,
    PackItem,
    Paper,
    Person,
    ReadValue,
    Task,
    Utterance,
)
from ..domain.case import CasePlan as CasePlanRecord

# ---------------------------------------------------------------------------------------------
# requests
# ---------------------------------------------------------------------------------------------


class StartCase(BaseModel):
    lang: str = "en"
    starter_name: str | None = None


class TurnIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    person_id: str | None = None
    lang: str | None = None
    channel: Literal["voice", "text"] = "text"
    seconds: int | None = None


class AnswerIn(BaseModel):
    """An answer to any checkpoint (C1-C7): the option's value, plus a corrected value or a note."""
    answer: str
    value: str | None = None
    note: str | None = None
    by: str | None = None


class MarkLeadIn(BaseModel):
    mark: Literal["know", "check", "not_relevant"]
    value: str | None = None
    note: str | None = None
    by: str | None = None


class CoverageAnswerIn(BaseModel):
    answer: Literal["yes", "no", "unsure"]
    by: str | None = None


class PersonIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    relationship: str | None = None
    roles: list[str] = []
    languages: list[str] = []
    city: str | None = None
    country: str | None = None
    address: str | None = None
    age: int | None = None
    by: str | None = None


class PersonPatch(BaseModel):
    display_name: str | None = None
    name_en: str | None = None
    relationship: str | None = None
    languages: list[str] | None = None
    city: str | None = None
    country: str | None = None
    address: str | None = None
    age: int | None = None
    channel: Literal["voice", "text"] | None = None
    by: str | None = None


class PlanAnswerIn(BaseModel):
    variable: str
    value: str
    by: str | None = None


class ByIn(BaseModel):
    by: str | None = None


class ProcedureIn(BaseModel):
    plan_key: str
    by: str | None = None


class ClaimEventIn(BaseModel):
    event: str = Field(description="submitted, complete_set_confirmed, settled, returned, or any plan event name")
    on: date | None = None
    amount: str | None = None
    note: str | None = None
    by: str | None = None


class LetterIn(BaseModel):
    kind: Literal["reminder", "objection", "grievance"] = "reminder"
    request: str | None = None
    by: str | None = None


class SentIn(BaseModel):
    on: date | None = None
    channel: Literal["letter", "email", "branch", "online", "phone"] = "letter"
    by: str | None = None


class ReplyIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=20000)
    on: date | None = None
    channel: Literal["letter", "email", "branch", "online", "phone", "photo"] = "letter"


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    person_id: str | None = None
    lang: str | None = None
    channel: Literal["voice", "text"] = "text"


class ShareIn(BaseModel):
    days: int = Field(31, ge=1, le=120)
    by: str | None = None


class TodayIn(BaseModel):
    viewer_id: str | None = None


class ChallengeIn(BaseModel):
    country: str = Field(min_length=2, max_length=2, description="ISO 3166-1 alpha-2 code")
    question: str = Field(min_length=3, max_length=2000)
    lang: str | None = None
    institution: str | None = None
    situation: str | None = None
    holding_kind: str | None = None
    asked_by: str | None = Field(None, description="Who asks and from where, in the question's language")
    title: str | None = Field(None, description="A short title in the question's language")
    translation: str | None = Field(None, description="The question in English, when known")


# ---------------------------------------------------------------------------------------------
# shared pieces
# ---------------------------------------------------------------------------------------------


class Money(BaseModel):
    amount: str | None = None              # a decimal as text, so nothing is lost in JSON
    currency: str
    text: str = ""                         # as the letters write it: "₹6,41,870.00"
    short: str = ""                        # without minor units: "₹6,41,870"


class PersonChip(BaseModel):
    id: str
    name: str
    name_en: str | None = None
    initials: str = ""
    color: str = "#DCE6F4"
    relationship: str | None = None
    languages: list[str] = []
    city: str | None = None
    country: str | None = None
    roles: list[str] = []


class CiteView(BaseModel):
    """One rule, in its source's own words, for every Why? on every screen."""
    id: str
    text: str
    quote: str
    quote_translation: str | None = None
    source_title: str = ""
    source_url: str = ""
    publisher: str = ""
    tier: int = 4
    tier_label: str = ""                   # Regulator, Law, Government source, Institution's own page...
    level: str = "sourced"
    level_label: str = ""                  # Gold, Two researchers agree, One official source...
    checked_on: date | None = None
    fresh_until: date | None = None
    fresh: bool = True
    locator: str = ""
    language: str = "en"
    fictional: bool = False
    jurisdiction: str = ""
    institution_id: str | None = None
    contested_with: list[str] = []


class RowView(BaseModel):
    """A line of a statement or passbook, as the evidence behind a lead, a payee or a value."""
    id: str
    date: date
    narration: str
    amount: Money
    direction: Literal["debit", "credit"]
    balance: Money | None = None
    after_death: bool = False
    paper_id: str | None = None
    paper_title: str = ""
    page_id: str | None = None
    page_number: int | None = None
    line_id: str | None = None
    box: list[float] | None = None


class NavCounts(BaseModel):
    papers: int = 0
    papers_to_check: int = 0
    money: int = 0
    leads_open: int = 0
    people: int = 0
    people_questions: int = 0
    plans: int = 0
    plan_questions: int = 0
    packs: int = 0
    packs_ready: int = 0
    tracker_late: int = 0
    sources: int = 0
    activity: int = 0


class CaseHeader(BaseModel):
    case: CaseFile
    deceased: Person | None = None
    viewer: PersonChip | None = None
    family: list[PersonChip] = []
    family_places: list[str] = []          # where the family lives, for "Family in Kochi and Singapore"
    today: date
    nav: NavCounts
    mode: str = "scripted"
    # what the case's background job is doing now: "reading" its new papers, then "finding" what they point to
    # (people, accounts, loans, policies, payments to stop) and planning; None when nothing is running
    working: Literal["reading", "finding"] | None = None
    working_since: datetime | None = None


class CaseSummary(BaseModel):
    id: str
    title: str
    title_en: str | None = None
    family_label: str = ""
    demo_key: str | None = None
    died_on: date | None = None
    died_in: str | None = None
    country_codes: list[str] = []
    languages: list[str] = []
    stage: str = "intake"
    story_day: date | None = None
    synthetic: bool = True
    updated_at: str | None = None


class CaseStarted(BaseModel):
    case: CaseFile
    reply: Utterance | None = None


# ---------------------------------------------------------------------------------------------
# intake
# ---------------------------------------------------------------------------------------------


class KnownLine(BaseModel):
    label: str                             # Who died, Family, Money mentioned, Whose rules apply...
    text: str
    person_id: str | None = None


class ConversationView(BaseModel):
    utterances: list[Utterance]
    people: list[PersonChip]
    known: list[KnownLine]
    urgent: list[str] = []
    summary: Checkpoint | None = None
    stage: str = "intake"
    languages: list[str] = []
    cites: dict[str, CiteView] = {}


class TurnOut(BaseModel):
    heard: Utterance
    reply: Utterance | None = None
    conversation: ConversationView


# ---------------------------------------------------------------------------------------------
# today
# ---------------------------------------------------------------------------------------------


class CurrencyTotals(BaseModel):
    currency: str
    known: Money
    paid: Money
    in_progress: Money
    owed: Money
    holdings_known: int = 0


class TaskView(BaseModel):
    task: Task
    owners: list[PersonChip] = []
    for_viewer: bool = False


class DeadlineView(BaseModel):
    date: date
    title: str
    note: str = ""
    late: bool = False
    kind: str = "clock"
    claim_id: str | None = None
    owner: str | None = None
    cites: list[str] = []


class LateClock(BaseModel):
    claim_id: str
    institution: str
    days_late: int
    due_on: date | None = None
    compensation: Money | None = None
    per_day: Money | None = None
    owners: list[PersonChip] = []


class Uncertainty(BaseModel):
    certainty: Literal["check", "unknown"]
    text: str
    ref: str | None = None


class TodayView(BaseModel):
    date: date
    greeting: str = ""
    greeting_local: str = ""
    viewer: PersonChip | None = None
    totals: list[CurrencyTotals] = []
    unknown_values: int = 0
    tasks: list[TaskView] = []
    deadlines: list[DeadlineView] = []
    late: list[LateClock] = []
    uncertain: list[Uncertainty] = []
    reading: list[str] = []                # papers still being read
    greeting_lang: str | None = None
    greeting_for: str | None = None        # the person the greeting was written for (a stale one is redone)
    cites: dict[str, CiteView] = {}


# ---------------------------------------------------------------------------------------------
# papers
# ---------------------------------------------------------------------------------------------


class PaperRow(BaseModel):
    paper: Paper
    uploaded_by: PersonChip | None = None
    note: str = ""


class PapersView(BaseModel):
    papers: list[PaperRow]
    reading: int = 0
    to_check: int = 0


class PageView(BaseModel):
    id: str
    number: int
    width: int
    height: int
    image_url: str


class ValueView(BaseModel):
    value: ReadValue
    display: str = ""
    checkpoint: Checkpoint | None = None


class PaperView(BaseModel):
    paper: Paper
    pages: list[PageView]
    values: list[ValueView]
    rows: list[RowView] = []
    file_url: str | None = None
    checkpoints: list[Checkpoint] = []


# ---------------------------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------------------------


class HoldingView(BaseModel):
    holding: Holding
    value: Money | None = None
    certainty: Literal["sure", "check", "unknown"] = "sure"
    tag: Literal["confirmed", "lead", "owed", "paid", "closed"] = "confirmed"
    note: str = ""
    claim_id: str | None = None
    claim_status: str | None = None
    plan_id: str | None = None
    lead: Lead | None = None


class InstitutionGroup(BaseModel):
    institution_id: str | None = None
    name: str
    short_name: str | None = None
    kind: str | None = None
    role: str = ""                         # "his employer": what the institution was to the person, when it helps
    from_paper: str | None = None          # for things with no institution yet: the paper they were found on
    holdings: list[HoldingView]


class CountryGroup(BaseModel):
    country: str
    currency: str
    total_known: Money
    owed: Money | None = None
    claimant: PersonChip | None = None
    institutions: list[InstitutionGroup]


class JurisdictionNote(BaseModel):
    country: str
    text: str
    cites: list[str] = []
    contested: bool = False


class MoneyView(BaseModel):
    countries: list[CountryGroup]
    totals: list[CurrencyTotals]
    notes: list[JurisdictionNote] = []
    summary: str = ""
    summary_local: str = ""
    lang_local: str = ""
    contested: bool = False
    cites: dict[str, CiteView] = {}


# ---------------------------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------------------------


class LeadView(BaseModel):
    lead: Lead
    rows: list[RowView] = []
    value: Money | None = None
    institution: str | None = None
    checkpoint: Checkpoint | None = None
    papers: list[str] = []                 # titles of the papers it was seen on


class CoverageItem(BaseModel):
    id: str
    label: str
    status: Literal["found", "lead", "not_seen", "not_applicable"] = "not_seen"
    detail: str = ""
    ask: str | None = None


class LeadsView(BaseModel):
    open: list[LeadView]
    confirmed: list[LeadView]
    closed: list[LeadView]
    coverage: list[CoverageItem] = []
    country: str | None = None
    countries: list[str] = []              # every country the case spans (the coverage list covers them all)
    cites: dict[str, CiteView] = {}


# ---------------------------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------------------------


class NameSeen(BaseModel):
    form: NameForm
    seen_on_title: str = ""
    why: str = ""


class SignCell(BaseModel):
    person_id: str
    text: str
    signs: bool = False


class SignRow(BaseModel):
    claim_id: str
    title: str
    country: str | None = None
    cells: list[SignCell]


class IdentityQuestion(BaseModel):
    checkpoint: Checkpoint
    person: PersonChip | None = None
    asked_of: PersonChip | None = None
    rows: list[RowView] = []


class PeopleView(BaseModel):
    deceased: Person | None = None
    names: list[NameSeen] = []
    family: list[Person]
    questions: list[IdentityQuestion] = []
    signing: list[SignRow] = []
    pending: list[str] = []                # claims whose signers are not known yet
    pronoun: str = "their"                 # his, her or their: how the family speak of the person who died


# ---------------------------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------------------------


class PlanRow(BaseModel):
    claim: Claim
    plan: CasePlanRecord | None = None
    institution: str = ""
    amount: Money | None = None
    status_label: str = ""
    late: bool = False
    questions: int = 0


class PlansView(BaseModel):
    rows: list[PlanRow]


class Compensation(BaseModel):
    clock: ClockState
    base_rate_pct: str | None = None
    spread_pct: str | None = None
    rate_pct: str | None = None
    amount: Money | None = None
    per_day: Money | None = None
    owed: Money | None = None
    rate_on: date | None = None
    note: str = ""


class PlanView(BaseModel):
    plan: CasePlanRecord
    claim: Claim
    institution: Institution | None = None
    amount: Money | None = None
    questions: list[Checkpoint] = []
    approval: Checkpoint | None = None
    approved_by: PersonChip | None = None
    compensation: list[Compensation] = []
    cites: dict[str, CiteView] = {}
    pack_id: str | None = None
    needed_by: list[str] = []              # for a procedure: the claims that wait for it
    procedures: list[str] = []             # for a claim: the procedures it waits for
    family_clocks: list[ClockState] = []   # the family's own deadlines that bear on this plan, its procedures' too


# ---------------------------------------------------------------------------------------------
# packs
# ---------------------------------------------------------------------------------------------


class PackItemView(BaseModel):
    item: PackItem
    file_url: str | None = None
    mirror_url: str | None = None
    signers: list[PersonChip] = []
    pages: int = 0
    page_url: str | None = None            # add the 1-based page number: {page_url}/{n}


class PackRow(BaseModel):
    pack: Pack
    claim_title: str = ""
    institution: str = ""


class PacksView(BaseModel):
    rows: list[PackRow]


class PackView(BaseModel):
    pack: Pack
    claim: Claim
    institution: Institution | None = None
    amount: Money | None = None
    items: list[PackItemView]
    checkpoint: Checkpoint | None = None
    approved_by: PersonChip | None = None
    download_url: str
    cites: dict[str, CiteView] = {}


# ---------------------------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------------------------


class ClockRow(BaseModel):
    claim_id: str
    title: str
    institution: str
    kind: str = "claim"
    status: str
    status_text: str
    clocks: list[ClockState] = []
    events: dict[str, str] = {}
    settled_on: date | None = None
    settled: Money | None = None
    late: bool = False


class LetterView(BaseModel):
    letter: Correspondence
    claim_title: str = ""
    institution: str = ""
    to_line: str = ""
    signer: PersonChip | None = None
    fidelity: str | None = None            # pass/fail of the "every number is in the case" check


class Escalation(BaseModel):
    text: str
    cites: list[str] = []
    verified: bool = False


class TrackerView(BaseModel):
    today: date
    start: date
    end: date
    rows: list[ClockRow]
    letters: list[LetterView] = []
    received: list[LetterView] = []
    expected: list[DeadlineView] = []
    escalation: dict[str, list[Escalation]] = {}
    cites: dict[str, CiteView] = {}


# ---------------------------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------------------------


class SourceRow(BaseModel):
    cite: CiteView
    used_by: list[str] = []                # claim titles that rely on it
    institutions: list[str] = []


class LevelCount(BaseModel):
    level: str
    label: str
    count: int


class SourcesView(BaseModel):
    rows: list[SourceRow]
    counts: list[LevelCount]
    researching: list[str] = []
    institutions: list[dict[str, str]] = []


class AssertionView(BaseModel):
    cite: CiteView
    page_excerpt: str = ""
    quote_found: bool = False


class PageSnapshotView(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    updated: str | None = None
    tier: int | None = None
    publisher: str = ""
    fictional: bool = False
    lang: str | None = None


# ---------------------------------------------------------------------------------------------
# activity, ask, desk
# ---------------------------------------------------------------------------------------------


class ActivityView(BaseModel):
    items: list[Activity]
    usage: UsageView | None = None         # the case's model use so far (live and replayed runs)


class AskOut(BaseModel):
    question: Utterance
    answer: Utterance
    sources: list[CiteView] = []
    follow_ups: list[str] = []
    research_started: bool = False


class DeskRequirement(BaseModel):
    requirement: str
    para: str = ""
    in_pack: str = ""
    status: str = ""
    late: bool = False
    cites: list[str] = []


class DeskView(BaseModel):
    institution: Institution | None = None
    branch: str | None = None
    family_label: str = ""
    until: date | None = None
    claim: Claim
    title: str
    deceased: str
    claimant: str | None = None
    claimant_relationship: str | None = None
    amount: Money | None = None
    route: str = ""
    due: str = ""
    requirements: list[DeskRequirement] = []
    compensation: list[Compensation] = []
    notes: list[str] = []
    checked_against: str = ""
    cites: dict[str, CiteView] = {}


class DeskSettleIn(BaseModel):
    amount: str = Field(min_length=1, max_length=40, description="The amount paid, as a decimal")
    on: date | None = None
    note: str | None = None


class DeskRequestIn(BaseModel):
    text: str = Field(min_length=3, max_length=4000, description="What else the institution needs")
    rule: str | None = Field(None, description="The rule or paragraph the institution relies on")


class ShareOut(BaseModel):
    token: str
    url: str
    until: date


# ---------------------------------------------------------------------------------------------
# demo, challenge, evaluation, meta
# ---------------------------------------------------------------------------------------------


class DemoFamily(BaseModel):
    key: str
    title: str
    family_label: str
    where: str
    spoken: str
    shows: str
    languages: list[str]
    countries: list[str]
    order: int
    case_id: str
    seeded: bool
    viewer: str | None = None
    story_day: date | None = None


class SeedOut(BaseModel):
    case_id: str
    events: int
    warnings: list[str] = []
    seconds: float = 0.0


class ChallengePreset(BaseModel):
    key: str
    country: str
    country_name: str
    lang: str
    question: str
    translation: str = ""
    holding_kind: str | None = None
    institution: str | None = None
    situation: str = ""
    prepared: bool = True
    asked_by: str | None = None
    title: str | None = None


class ChallengeRow(BaseModel):
    id: str
    country: str
    question: str
    lang: str | None = None
    status: str
    started_at: str | None = None
    stored_key: str | None = None


class ChallengeView(BaseModel):
    id: str
    need: dict[str, Any]
    status: str
    started_at: str | None = None
    seconds: float = 0
    events: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    researcher_a: dict[str, Any] = {}
    researcher_b: dict[str, Any] = {}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    adjudication: dict[str, Any] = {}
    agreement: dict[str, Any] = {}
    plan: dict[str, Any] | None = None
    plan_errors: list[str] = []
    gold: dict[str, Any] | None = None
    first_answer: dict[str, Any] | None = None
    letter: dict[str, Any] | None = None
    stored_key: str | None = None
    cites: dict[str, CiteView] = {}
    finished: bool = False
    rounds: int = 0                        # research rounds: adaptive research goes on until nothing is left open


class CalibrationInfo(BaseModel):
    """Where the certainty labels come from: the hand-set starting tables, or tables refitted on live cases."""
    status: str = "default"                # default | fitted
    fitted_on: date | None = None
    live_cases: list[str] = []
    items: dict[str, int] = {}             # items measured per module


class UsageView(BaseModel):
    """Model calls and their tokens; the cost is an estimate from the price table in data/knowledge/prices.yaml."""
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    tokens_cached: int = 0
    cost_usd: float = 0.0
    ms: int = 0


class CaseUsage(BaseModel):
    case_id: str
    title: str
    demo_key: str | None = None
    usage: UsageView


class UsageReport(BaseModel):
    """What the models were used for: each case's total (kept with its activity, so it survives a restart), and
    each agent's and model's since this copy of the service started."""
    prices_as_of: str = ""
    budget_usd: float | None = None        # most a case may spend on optional work (research rounds, repairs)
    since: str = ""                        # when this copy of the service started
    total: UsageView = UsageView()
    by_agent: dict[str, UsageView] = {}
    by_model: dict[str, UsageView] = {}
    cases: list[CaseUsage] = []


class ServiceProblem(BaseModel):
    count: int
    at: str = ""
    error: str = ""


class SlowRoute(BaseModel):
    """A model endpoint ("gemini-3.8-flash", "gemini-3.8-flash@us") that was slow or overloaded: its calls went to the
    same model on another endpoint, and it is passed over for a while (passed_over: it still is)."""
    count: int
    at: str = ""
    error: str = ""
    passed_over: bool = False


class HealthInfo(BaseModel):
    """Which live services answered since the service started, and which failed. A failure falls back to the
    offline stand-in (and says so here) unless the run is strict, when it stops the work instead. Slow model
    endpoints are listed apart: their calls were answered by the same model elsewhere, so nothing failed."""
    strict: bool = False
    ok: dict[str, int] = {}
    failures: dict[str, ServiceProblem] = {}
    slow: dict[str, SlowRoute] = {}


class Meta(BaseModel):
    version: str
    mode: str
    llm: str
    store: str
    story_day: date | None = None
    today: date
    languages: list[str]
    live_gateway: str | None = None        # the voice WebSocket path ({case_id} to fill in), when live voice is on
    countries_researched: int = 0
    services: dict[str, str] = {}          # what each part runs on: store, blobs, ocr, translation, search...
    calibration: CalibrationInfo = CalibrationInfo()
    health: HealthInfo = HealthInfo()
