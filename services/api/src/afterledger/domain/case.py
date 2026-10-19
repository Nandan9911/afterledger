"""The case graph: every record the agents and the family work on, for one bereaved family.

Records are plain Pydantic models. The case store keeps them in named collections, so the
same models serve Firestore, local JSON files and the HTTP API.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field

Certainty = Literal["sure", "check", "unknown"]
"""Shown everywhere as Sure (ink), Please check (pencil) and We don't know yet (blank)."""

Tier = Literal["A", "B", "C"]


def _aware(v: datetime | None) -> datetime | None:
    """Every moment carries its offset, so moments from the story's clock and the real one sort together; a
    moment saved without one is read as UTC."""
    return v.replace(tzinfo=UTC) if v is not None and v.tzinfo is None else v


Moment = Annotated[datetime, AfterValidator(_aware)]


class Provenance(BaseModel):
    by: Literal["agent", "code", "person", "seed"] = "code"
    agent: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    person_id: str | None = None
    run_id: str | None = None
    at: Moment | None = None


class Evidence(BaseModel):
    kind: Literal["page_region", "statement", "source", "row", "paper"]
    ref: str
    paper_id: str | None = None
    page_id: str | None = None
    line_ids: list[str] = []
    box: list[float] | None = None        # normalised [x0, y0, x1, y1] on the page image
    quote: str | None = None
    label: str | None = None              # "Sahyadri Bank statement, page 3, line 14"


# ---------------------------------------------------------------------------------------------
# People and the case itself
# ---------------------------------------------------------------------------------------------

class NameForm(BaseModel):
    text: str
    script: str = "Latn"
    lang: str | None = None
    reading: str | None = None            # e.g. katakana reading or romanisation
    seen_on: str | None = None            # paper id or "family"
    parts: dict[str, str] = {}            # given, family, patronymic, initials...
    why: str | None = None                # the evidence that this name is this person ("Same FIN number")


class Residence(BaseModel):
    country: str
    city: str | None = None
    since: date | None = None
    until: date | None = None


Role = Literal["deceased", "claimant", "heir", "nominee", "beneficiary", "joint_holder", "executor",
               "helper", "witness", "declarant", "other"]


class Person(BaseModel):
    id: str
    case_id: str
    display_name: str
    name_en: str | None = None            # in Latin letters, when the name is in another script
    names: list[NameForm] = []
    roles: list[Role] = []
    relationship: str | None = None       # to the deceased: wife, son, mother...
    languages: list[str] = []             # BCP-47, first is preferred
    channel: Literal["voice", "text"] = "text"
    country: str | None = None
    city: str | None = None
    address: str | None = None            # as the family gives it, for forms that ask for it
    birth_date: date | None = None
    death_date: date | None = None
    death_place: str | None = None
    nationalities: list[str] = []
    residences: list[Residence] = []
    age: int | None = None
    initials: str = ""
    color: str = "#DCE6F4"
    certainty: Certainty = "sure"         # "unknown" for someone whose relationship is not known yet
    notes: str = ""
    access: Literal["owner", "member", "invited", "none"] = "none"   # who can open the case
    joined_on: date | None = None


class JurisdictionLeg(BaseModel):
    holding_id: str | None = None
    label: str
    succession_law: str | None = None     # ISO code, or "contested"
    procedure_jurisdiction: str
    papers_from: list[str] = []
    certification: list[str] = []         # e.g. "apostille (RBI para 324 mode 5)"
    cites: list[str] = []
    contested: bool = False
    note: str = ""


class JurisdictionMap(BaseModel):
    domestic: bool = True
    countries: list[str] = []
    succession_law: str | None = None
    contested: bool = False
    legs: list[JurisdictionLeg] = []
    summary: str = ""
    summary_local: str = ""
    lang_local: str = ""
    cites: list[str] = []


class SituationModel(BaseModel):
    deceased_name: str = ""
    died_on: date | None = None
    died_in: str | None = None
    country_codes: list[str] = []
    family: list[dict[str, Any]] = []
    holdings_mentioned: list[str] = []
    holdings: list[dict[str, Any]] = []   # what the family named: {said, kind, institution, direction}
    papers_at_hand: list[str] = []
    urgent: list[str] = []
    will: Literal["yes", "no", "unknown"] = "unknown"
    dispute: Literal["yes", "no", "unknown"] = "unknown"
    death_cause: Literal["illness", "accident", "other", "unknown"] = "unknown"
    death_country: str | None = None
    occupation: str | None = None
    mode: Literal["family", "helper", "planning_ahead"] = "family"
    unknowns: list[str] = []
    cites: dict[str, list[str]] = {}      # field -> utterance ids


class TodayNote(BaseModel):
    """One family member's greeting for the day, in English and in their own language."""
    text: str
    local: str = ""
    on: date | None = None


class CaseFile(BaseModel):
    id: str
    title: str                            # "Rajesh Kumar Verma", "佐藤 健二"
    title_en: str | None = None           # in Latin letters, when the name is in another script
    family_label: str = ""                # "The Vermas"
    demo_key: str | None = None           # verma, sato, clarke, morales, nair, tanaka
    synthetic: bool = True
    deceased_id: str | None = None
    died_on: date | None = None
    died_in: str | None = None
    country_codes: list[str] = []
    languages: list[str] = []
    story_day: date | None = None         # the day the story is set on (seeded demo cases)
    created_at: Moment | None = None
    updated_at: Moment | None = None
    viewer_person_id: str | None = None   # default family member viewing the case
    stage: Literal["intake", "papers", "working", "settled"] = "intake"
    situation: SituationModel | None = None
    jurisdiction: JurisdictionMap | None = None
    summary: str = ""
    model_mode: str = "scripted"          # scripted, gemini, replay: shown honestly in the app
    coverage: list[dict[str, Any]] = []   # what families here usually hold, checked against this case
    today_note: str = ""                  # the supervisor's one-line greeting for today
    today_note_local: str = ""
    today_note_for: str | None = None     # the person that greeting was written for
    replay_on: date | None = None         # while a demo story is being replayed, the day it has reached
    today_notes: dict[str, TodayNote] = {}  # each family member's own greeting, by person id
    shared_with: list[dict[str, Any]] = []   # institution desk shares: {institution_id, until, claim_id}
    challenge_id: str | None = None       # set when the case was saved from a live challenge
    owner_id: str | None = None           # the account that started it, where sign-in is required


# ---------------------------------------------------------------------------------------------
# Papers and what was read from them
# ---------------------------------------------------------------------------------------------

class PageRef(BaseModel):
    id: str
    number: int
    image: str                            # blob ref of the page image
    width: int
    height: int


class Paper(BaseModel):
    id: str
    case_id: str
    title: str
    title_en: str | None = None           # when the title is in another language
    doc_type: str = "unknown"             # death_certificate, bank_statement, passbook, ...
    country: str | None = None
    languages: list[str] = []
    scripts: list[str] = []
    issuer: str | None = None
    institution_id: str | None = None
    file: str | None = None               # blob ref of the original upload
    pages: list[PageRef] = []
    origin: Literal["upload", "photo", "seed"] = "upload"
    status: Literal["queued", "reading", "read", "needs_check", "failed"] = "queued"
    stage: str = ""                       # human words for the current step
    summary: str = ""
    values_total: int = 0
    values_to_check: int = 0
    uploaded_by: str | None = None
    uploaded_at: Moment | None = None
    synthetic: bool = True
    fixture_key: str | None = None        # links a seeded paper to its truth file


class OcrLine(BaseModel):
    id: str
    case_id: str
    paper_id: str
    page_id: str
    text: str
    box: list[float]                      # normalised
    confidence: float = 1.0
    source: Literal["document_ai", "pdf_text", "sidecar", "vision"] = "pdf_text"


class ReadValue(BaseModel):
    id: str
    case_id: str
    paper_id: str
    page_id: str | None = None
    field: str                            # schema field name, e.g. "balance_closing"
    label: str                            # "Balance on 15 September 2026"
    kind: Literal["text", "name", "date", "amount", "identifier", "choice"] = "text"
    value: str | None = None              # agreed, normalised value
    as_printed: str | None = None
    reading_a: str | None = None
    reading_b: str | None = None
    reading_c: str | None = None
    status: Literal["matched", "resolved", "one_path", "disagree", "family_confirmed",
                    "family_corrected"] = "matched"
    certainty: Certainty = "sure"
    tier: Tier = "B"
    note: str = ""
    evidence: list[Evidence] = []
    checks: list[str] = []                # names of the exact checks that passed


class Fact(BaseModel):
    id: str
    case_id: str
    subject: str                          # "person:p_rajesh", "holding:h_narmada_fd"
    predicate: str                        # "date_of_death", "balance"
    value: str | int | float | bool | None
    as_printed: str | None = None
    evidence: list[Evidence] = Field(min_length=1)
    level: Literal["single_read", "dual_read", "adjudicated", "family_confirmed", "derived"] = "dual_read"
    tier: Tier = "A"
    provenance: Provenance = Provenance()


class Utterance(BaseModel):
    id: str
    case_id: str
    person_id: str | None = None          # None for the assistant
    role: Literal["family", "assistant"] = "family"
    lang: str = "en"
    text: str
    translation: str | None = None
    channel: Literal["voice", "text"] = "text"
    at: Moment | None = None
    sources: list[str] = []               # assertion ids or evidence refs behind an answer
    grounded: bool | None = None
    intent: str | None = None
    seconds: int | None = None            # length of a voice note
    why: str = ""                         # why a question is asked, shown under it


# ---------------------------------------------------------------------------------------------
# Money: institutions, holdings, transactions, leads
# ---------------------------------------------------------------------------------------------

InstitutionKind = Literal["bank", "insurer", "lender", "depository", "fund_house", "registrar", "pension",
                          "government", "employer", "regulator", "court", "chit_fund", "company", "other"]


class Institution(BaseModel):
    id: str
    name: str                             # the institution's own name, in its own script
    short_name: str | None = None         # a short English name for lists: "PMJJBY", "Naniwa Bank"
    aliases: list[str] = []
    country: str
    kind: InstitutionKind = "bank"
    identifiers: dict[str, str] = {}
    domain: str | None = None
    bereavement_url: str | None = None
    fictional: bool = True
    note: str = ""
    bank_type: str | None = None          # India: commercial or urban_coop, which sets the RBI claim limit
    branch: str | None = None             # the branch a letter goes to, when a paper names it
    learned: bool = False                 # not in the directory: named on one family's papers or by the family
    source: str = ""                      # where a learned institution was named ("the ICICI Bank statement")


HoldingKind = Literal[
    "savings", "current", "nre", "nro", "fixed_deposit", "recurring_deposit", "demat", "mutual_fund",
    "shares_physical", "life_policy", "government_cover", "accident_cover", "pension", "family_pension",
    "provident_fund", "cpf", "locker", "premium_bonds", "social_security", "employer_benefit", "salary",
    "chit_fund", "gold", "property", "loan", "card_loan", "credit_card", "other"]


class Holding(BaseModel):
    id: str
    case_id: str
    institution_id: str | None = None
    kind: HoldingKind
    direction: Literal["asset", "debt", "entitlement", "obligation"] = "asset"
    label: str
    account_mask: str | None = None       # "ending 0932"
    currency: str = "INR"
    value: Decimal | None = None
    value_as_of: date | None = None
    nominee: Literal["yes", "no", "unknown", "not_applicable"] = "unknown"
    nominee_person_id: str | None = None
    holders: list[str] = []
    status: Literal["confirmed", "lead", "closed"] = "confirmed"
    country: str | None = None
    claim_id: str | None = None
    settled_amount: Decimal | None = None
    settled_on: date | None = None
    note: str = ""
    evidence: list[Evidence] = []
    lead_id: str | None = None
    source: Literal["paper", "lead", "family"] = "paper"   # where it was first found
    paper_ids: list[str] = []             # papers that show it


class Transaction(BaseModel):
    id: str
    case_id: str
    paper_id: str
    page_id: str | None = None
    line_id: str | None = None
    holding_id: str | None = None
    date: date
    narration: str
    amount: Decimal
    direction: Literal["debit", "credit"]
    balance: Decimal | None = None
    currency: str = "INR"
    after_death: bool = False
    balance_ok: bool | None = None        # result of the running-balance check
    group_id: str | None = None


class PaymentGroup(BaseModel):
    id: str
    case_id: str
    key: str                              # normalised counterparty key
    label: str                            # representative narration
    direction: Literal["debit", "credit"]
    count: int
    cadence: Literal["weekly", "monthly", "quarterly", "half_yearly", "yearly", "irregular", "once"]
    median_gap_days: float | None = None
    amount_typical: Decimal
    amount_stable: bool
    currency: str = "INR"
    first: date
    last: date
    after_death_count: int = 0
    transaction_ids: list[str] = []
    counterparty: str | None = None       # interpreted payee or payer
    counterparty_kind: str | None = None
    interpretation: str | None = None
    alternatives: list[str] = []


class Lead(BaseModel):
    id: str
    case_id: str
    kind: Literal["asset", "debt", "entitlement", "stop_payment", "refund"]
    category: str                         # life_cover, mutual_fund, locker, shares, loan, pension...
    holding_kind: str | None = None       # what it becomes on the money map, when the hypothesis says
    title: str
    sentence: str
    title_local: str = ""                 # in the family's language, when it is not English
    sentence_local: str = ""
    lang_local: str = ""
    evidence: list[Evidence] = []
    group_ids: list[str] = []
    paper_ids: list[str] = []
    investigator_note: str = ""
    critic_note: str = ""
    how_to_confirm: str = ""
    next_step_note: str = ""
    probability: float = 0.5
    signals: dict[str, str] = {}          # what the calibration table looked up for this lead's certainty
    label: Literal["sure", "likely", "possible", "unlikely"] = "possible"
    status: Literal["open", "confirmed", "closed"] = "open"
    family_mark: Literal["know", "check", "not_relevant"] | None = None
    marked_by: str | None = None
    holding_id: str | None = None
    institution_id: str | None = None
    value: Decimal | None = None
    currency: str | None = None
    closed_reason: str = ""
    cites: list[str] = []                 # assertion ids
    next_due: date | None = None          # for debts: the next instalment
    monthly: Decimal | None = None


# ---------------------------------------------------------------------------------------------
# Plans, packs, claims, tasks and checkpoints
# ---------------------------------------------------------------------------------------------

class StepView(BaseModel):
    id: str
    text: str                             # in plain words, in the viewer's language at render time
    status: Literal["done", "active", "blocked", "skipped", "late", "todo"] = "todo"
    note: str = ""
    cites: list[str] = []
    must_not_be_asked: list[str] = []


class DocumentView(BaseModel):
    id: str
    name: str
    status: Literal["have", "missing", "needs_certifying", "needs_translating", "filled", "signed",
                    "submitted", "unknown"] = "missing"
    note: str = ""
    certification: list[str] = []
    cites: list[str] = []


class ClockState(BaseModel):
    id: str
    clock_id: str
    label: str
    owner: Literal["institution", "family"]
    started_on: date | None = None
    due_on: date | None = None
    unit: str = "calendar_days"
    length: int = 0
    status: Literal["not_started", "running", "met", "late", "ended"] = "not_started"
    kind: Literal["deadline", "window", "wait"] = "deadline"
    short: str = ""                       # how it reads in a list of deadlines: "settlement"
    short_local: str = ""                 # the same in the plan's own language
    ended_on: date | None = None          # the day the event that stops the clock happened
    days_late: int = 0
    compensation_formula: str | None = None
    compensation: Decimal | None = None
    per_day: Decimal | None = None
    base_rate_pct: Decimal | None = None
    spread_pct: Decimal | None = None
    amount: Decimal | None = None
    currency: str | None = None
    cites: list[str] = []
    rule: bool = True                     # False: AfterLedger's own date to follow up, not a rule
    starts_on: str = ""                   # the event that starts it: complete_set_confirmed, claim_lodged, death...


class CasePlan(BaseModel):
    id: str
    case_id: str
    claim_id: str | None = None
    plan_key: str
    title: str
    institution_id: str | None = None
    jurisdiction: str
    level: str = "draft"
    verified_label: str = ""
    gold_match: bool | None = None
    facts: dict[str, Any] = {}
    questions: list[str] = []
    steps: list[StepView] = []
    documents: list[DocumentView] = []
    clocks: list[ClockState] = []
    must_not_be_asked: list[str] = []
    contested: list[str] = []
    approved_by: str | None = None
    approved_on: date | None = None
    summary: str = ""
    research_label: str = ""              # "verified on 14 October 2026" / "researched just now"
    verified_on: date | None = None       # when the plan's rules were last verified (None: researched just now)
    unverified: bool = False              # the plan starts by asking the institution in writing
    explained: list[dict[str, Any]] = []  # the plan in plain words: [{text, text_local, who, step_id, cites}]
    summary_local: str = ""
    lang_local: str = ""                  # the language of the *_local texts


class FilledField(BaseModel):
    key: str
    label: str
    value: str
    source: str = ""                      # fact id, person id or "family"
    evidence: list[Evidence] = []
    readback_ok: bool | None = None
    certainty: Certainty = "sure"
    note: str = ""
    page: int | None = None               # where the value sits on the filled form (1-based page)
    box: list[float] | None = None        # normalised [x0, y0, x1, y1] on that page


class PackItem(BaseModel):
    id: str
    kind: Literal["form", "letter", "declaration", "fill_by_hand", "instructions"]
    title: str
    form_key: str | None = None
    language: str = "en"
    file: str | None = None               # blob ref of the filled PDF
    mirror_file: str | None = None        # translation for the family
    mirror_language: str | None = None
    text: str | None = None               # letter body
    mirror_text: str | None = None
    fields: list[FilledField] = []
    signers: list[str] = []
    status: Literal["draft", "ready", "approved", "submitted"] = "draft"
    note: str = ""


class Enclosure(BaseModel):
    name: str
    status: Literal["have", "missing", "needs_notarising", "needs_apostille", "needs_translation",
                    "unknown"] = "have"
    note: str = ""


class CheckResult(BaseModel):
    name: str
    result: Literal["pass", "fail", "unknown"]
    detail: str = ""
    params: dict[str, Any] = {}           # the figures behind the detail, so a page can word it in its language


class Pack(BaseModel):
    id: str
    case_id: str
    claim_id: str
    plan_key: str | None = None           # the plan it was prepared for (an interim plan's letter, then the claim's)
    title: str
    summary: str = ""
    items: list[PackItem] = []
    enclosures: list[Enclosure] = []
    checks: list[CheckResult] = []
    status: Literal["draft", "ready", "approved", "submitted"] = "draft"
    approved_by: str | None = None
    approved_on: date | None = None
    submit_how: str = ""


class Claim(BaseModel):
    id: str
    case_id: str
    institution_id: str
    kind: Literal["claim", "procedure", "debt"] = "claim"
    """claim: money to collect; procedure: a paper or decision other claims need (a court affidavit, an heir
    certificate, a renunciation); debt: what the estate owes, where the rules say how to deal with it."""
    plan_key: str | None = None           # set for procedures, whose plan is not found from a holding
    needed_by: list[str] = []             # for a procedure: the claims that need it
    started_by: str | None = None         # a procedure the family started themselves is never closed for them
    claimant_person_id: str | None = None # who claims, when not the case's main claimant (a named beneficiary)
    holding_ids: list[str] = []
    title: str
    plan_id: str | None = None
    pack_id: str | None = None
    status: Literal["planning", "ready", "submitted", "acknowledged", "settled", "returned",
                    "escalated", "handed_off", "closed"] = "planning"
    closed_reason: str = ""
    amount_due: Decimal | None = None
    currency: str = "INR"
    submitted_on: date | None = None
    complete_set_on: date | None = None
    settled_on: date | None = None
    settled_amount: Decimal | None = None
    clocks: list[ClockState] = []
    correspondence_ids: list[str] = []
    next_action: str = ""
    country: str | None = None
    events: dict[str, str] = {}           # event name -> ISO date (claim_lodged, forwarded_to_insurer...)
    signers: dict[str, list[str]] = {}    # document id -> person ids who sign it
    research: dict[str, Any] = {}         # the rules being researched for it: {signature, run_id, status, started...}


class TaskAction(BaseModel):
    label: str
    kind: Literal["open", "answer", "approve", "mark", "record", "external"] = "open"
    target: str = ""                      # route or checkpoint id
    value: str | None = None


class Task(BaseModel):
    id: str
    case_id: str
    kind: Literal["ask", "research", "acquire", "fill", "sign", "certify", "submit", "wait", "follow_up",
                  "handoff", "review", "notify", "decide", "find"]
    title: str
    body: str = ""
    title_local: str = ""                 # in the language of the people it is for, when not English
    body_local: str = ""
    lang_local: str = ""
    owner_person_ids: list[str] = []
    owner_agent: str | None = None
    status: Literal["todo", "doing", "done", "blocked", "waiting"] = "todo"
    due_on: date | None = None
    priority: float = 0.0
    depends_on: list[str] = []
    claim_id: str | None = None
    plan_step: str | None = None
    checkpoint_id: str | None = None
    actions: list[TaskAction] = []
    cites: list[str] = []
    created_at: Moment | None = None


class Option(BaseModel):
    value: str
    label: str


class Checkpoint(BaseModel):
    id: str
    case_id: str
    code: Literal["C1", "C2", "C3", "C4", "C5", "C6", "C7"]
    kind: str
    title: str
    question: str
    question_local: str | None = None     # in the asked person's language
    for_person_id: str | None = None
    language: str = "en"
    options: list[Option] = []
    payload: dict[str, Any] = {}
    status: Literal["open", "answered", "expired"] = "open"
    answer: str | None = None
    answer_note: str | None = None
    answered_by: str | None = None
    answered_at: Moment | None = None
    created_at: Moment | None = None
    subject_ref: str | None = None        # "read_value:rv_..", "lead:..", "plan:.."
    why: str = ""                         # why the question is asked


class Correspondence(BaseModel):
    id: str
    case_id: str
    claim_id: str | None = None
    direction: Literal["in", "out"]
    channel: Literal["letter", "email", "branch", "online", "phone", "photo"] = "letter"
    on: date
    title: str
    summary: str = ""
    requests: list[str] = []
    outcome: str | None = None
    paper_id: str | None = None
    pack_item_id: str | None = None
    objection: str | None = None          # the rule an institution's request conflicts with
    expected_on: date | None = None       # when an answer is expected
    expected_title: str | None = None     # how the expected answer shows among the deadlines
    expected_note: str | None = None
    letter_text: str | None = None
    letter_mirror: str | None = None
    letter_lang: str | None = None
    status: Literal["draft", "approved", "sent", "received"] = "received"
    cites: list[str] = []
    kind: str = ""                        # a drafted letter's kind: reminder, chase, objection, grievance, request


class Activity(BaseModel):
    id: str
    case_id: str
    at: Moment
    module: str                           # intake, jurisdiction, reading, people, discovery, research...
    agent: str | None = None
    kind: Literal["step", "check", "decision", "question", "research", "info", "warning", "done"] = "step"
    text: str                             # one plain sentence for the family
    detail: dict[str, Any] = {}
    run_id: str | None = None


class LedgerEntry(BaseModel):
    id: str
    case_id: str
    at: Moment
    subject: str                          # what was checked: "read_value:..", "assertion:..", "pack:.."
    check: str                            # quote_check, support_check, two_paths, read_back, fidelity...
    method: str = ""
    result: Literal["pass", "fail", "unknown"]
    detail: str = ""
    evidence: list[str] = []


COLLECTIONS: dict[str, type[BaseModel]] = {
    "people": Person,
    "papers": Paper,
    "lines": OcrLine,
    "values": ReadValue,
    "facts": Fact,
    "utterances": Utterance,
    "institutions": Institution,
    "holdings": Holding,
    "transactions": Transaction,
    "groups": PaymentGroup,
    "leads": Lead,
    "plans": CasePlan,
    "packs": Pack,
    "claims": Claim,
    "tasks": Task,
    "checkpoints": Checkpoint,
    "correspondence": Correspondence,
    "activity": Activity,
    "ledger": LedgerEntry,
}
