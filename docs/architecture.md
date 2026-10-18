# Architecture

AfterLedger is a FastAPI service (Python 3.12) and a Next.js web app, with a domain core that is plain code, nine
modules that run 44 agents on the Agent Development Kit, and ports that are served by local adapters offline and
by Google Cloud adapters live. This page explains how the parts fit; the [decision records](adr) say why.

## Principles

1. **Agents propose; code decides.** An agent reads one JSON context pack and answers in a typed schema. Anything
   exact is code: dates and calendars, money and running balances, clocks and compensation, plan routes, quote
   checks, read-backs of filled forms, and the numbers in every letter.
2. **Two of everything that matters.** Two readers per paper (text layer and image), two researchers per rule
   (official sources first; the country's language first), a critic for every lead and merge, a back-translation
   for every family copy of a letter.
3. **Evidence or nothing.** A rule exists only as an assertion: one sentence, the verbatim quote that supports it,
   the page it is on, the date it was checked and its level of verification. A quote that is not on its page word
   for word is dropped.
4. **Honest labels.** Certainty comes from measured accuracy (a calibration table), not from a model. Every screen
   and report says whether what it shows came from scripted responses, a live run or a replay.
5. **The family's language, the institution's language.** People read and speak in theirs; institutions receive
   theirs; names, numbers and dates are identical in both.
6. **One harness for every call.** Every model call and every tool call goes through the same wrappers and plugins,
   whatever the module: metered and budgeted, given a deadline and a retry, checked before it acts, and traced. What
   code can check, it checks, and a failed check goes back to the agent to be fixed before a person sees it.

## Components

```
apps/web                Next.js 16 app router, React 19, next-intl (6 languages), TanStack Query,
                        openapi-fetch typed client generated from the API's OpenAPI document
services/api            FastAPI app
  app/                  routers (HTTP, SSE, WebSocket), views (screen-shaped responses), actions (locks, tasks)
  modules/              the nine modules and the research service
  agents/               agent registry (specs), output schemas, prompts, ADK runner and model factory
  oracles/              scripted answers for every agent, for offline runs
  domain/               case model, plan language and runtime, calendars, checks, calibration (no I/O)
  forms/                form context, PDF rendering and read-back
  ports/                what the application needs from outside
  adapters/local        offline adapters (files, snapshots, reviewed translations, lexical grounding)
  adapters/gcp          Google Cloud adapters
  adapters/llm          scripted, recording and replaying model adapters
  seed/                 demo families' timelines, synthetic paper generation, demo bundles
  eval/                 the evaluation and the knowledge audit
packages/contracts      openapi.json and generated TypeScript types
data/                   knowledge, families, evaluation data
```

## The modules

| Module | What it does | Agents |
|---|---|---|
| 1 Intake | The family tells the story by voice or text; one question at a time; a summary to confirm (checkpoint C1) | listener, situation_extractor, gap_planner, tone_critic, locale_detector |
| 2 Jurisdiction | Connecting factors; conflict-of-laws rules found and checked; one leg per holding; conflicts kept, not resolved by a model | connecting_factors, jurisdiction_researcher, jurisdiction_verifier, jurisdiction_planner, jurisdiction_adjudicator |
| 3 Reading | Triage, schema (worked out first for a kind of paper with no stored layout), two independent readings, code comparison, a closer look on disagreement, statement rows with running balances; the decision controller sends a value to the family only when the answer is worth their effort (C2) | triage, schema_inducer, reader_a, reader_b, reader_c |
| 4 People | Names in every script split and matched to people on evidence (never the name alone); a name form that cannot be settled is asked about (C3); issuers to institutions | name_splitter, identity_resolver, merge_critic, institution_resolver |
| 5 Discovery | Payments grouped by payee and cadence; what they point to; one investigator per lead and a critic; debts; what families here usually hold (C4) | rail_profiler, narration_interpreter, hypothesis_generator, investigator, devils_advocate, coverage_auditor, liability_scout |
| 6 Research | Questions planned; researchers on the web in pairs per question group (official sources first; the country's language first); citation check; adjudication; more rounds until nothing is left open; a plan compiled and checked by code (and against a gold plan) | question_planner, researcher_primary, researcher_second, research_adjudicator, procedure_compiler |
| 6 Planning | Each claim's plan run on the case's facts: route, papers, clocks, questions only the family can answer and the plan's approval (C5), explained in plain words; a stored plan on a single source or with stale rules is researched again first | explainer |
| 7 Forms and letters | The institution's own forms filled in its script and format and read back from the PDF; a covering letter in its language with a family copy checked by back-translation; what fails a check is composed or written again, then checked again; the family approves the pack (C6) | form_scout, form_understander, value_composer, visual_critic, letter_writer, back_translator |
| 8 Follow-up | Today's few tasks for each family member; replies read against the plan; clocks, late days and compensation; reminders and objections that quote the rule, checked for every number before anyone sees them | supervisor, correspondence_reader, escalation_writer |
| 9 Conversation | Ask anything; answered only from the case and its sources, checked for grounding; a need the case does not cover gets a first answer from an official page | intent_router, case_answerer, unknown_need_handler |

Papers drive the pipeline: an upload is read (module 3), names and issuers resolved (4), payments examined (5),
whose rules apply worked out (2), holdings and leads become claims, each claim planned (6), and packs prepared
when a plan is approved (7). Follow-up (8) plans each family member's day after every change. All of it runs as
background work with one lock per case, and every step writes a plain-language line to the case's activity feed,
which the web app follows over server-sent events.

### Agents

Each agent is an ADK `LlmAgent` with an output schema (`agents/schemas.py`), instructions
(`agents/prompts/<module>.md`, with shared rules in `_shared.md`), a model tier and a thinking level
(`agents/specs.py`). Modules run agents through small ADK workflows (`agents/runner.py`); discovery runs one with
parallel workers (an interpreter per payment group, an investigator and a critic per lead), and research runs its
researchers as parallel workers, a pair per question group.

| Tier | Model | Used for |
|---|---|---|
| flash | `gemini-3.8-flash` | most agents |
| pro | `gemini-3.1-pro-preview` | the closer look (reader C), adjudicators, the plan compiler |
| lite | `gemini-3.5-flash-lite` | the intake conversation (listening, the situation, the next question, tone), triage, name splitting, intent, the payment-rail profile; live web search |
| live | `gemini-3.8-live` | spoken conversation |

Gemini 3 models are tuned for their default sampling, so only the thinking level is set (low, medium or high).
`AL_AGENT_TIERS` moves an agent to another tier without a new build (`situation_extractor=flash,gap_planner=flash`).
The intake agents moved from flash to lite on 18 October 2026, after a live test: each reply took 8-14 s with the
two of them on Gemini 3.8 Flash (3-8 s each), against under a second each on Flash-Lite in asia-south1.
With `AL_LLM_RECORD_DIR` set, a live run also writes each call as a cassette that `AL_LLM=replay` plays back
(`AL_LLM_REPLAY_STRICT=true` makes a call with no cassette an error instead of a scripted answer).

### The agent harness

Every call goes through the same layers ([decision record 0010](adr/0010-guard-layer-and-repairs.md)):

| Layer | What it does | Setting |
|---|---|---|
| `MeteredLlm` (`adapters/llm/harness.py`) | Checks the case's budget, gives the request a deadline, then writes one `model_call` trace line: agent, case, model (and endpoint), tokens in, out and thinking, milliseconds, estimated cost (`data/knowledge/prices.yaml`), and `replayed` for a call answered from a cassette. Activity lines carry each step's use. | `AL_CASE_BUDGET_USD`, `AL_LLM_HTTP_TIMEOUT_S` |
| `FallbackLlm` | Routes that answer equally well: the model on its main endpoint, on the backup one (`gemini-3.8-flash@us`), then its peers (Gemini 3.6 Flash). With no answer after the hedge delay the next route is asked too and the first answer wins; a slow route is passed over for a while ([decision record 0012](adr/0012-routes-hedging-and-resumable-recordings.md)). Out of quota or overloaded everywhere: one more try after a short pause, then pro falls to flash, flash to lite, and the trace names both. Slow everywhere: retried, not given to a smaller model. | `AL_FALLBACK`, `AL_LLM_BACKUP_LOCATION`, `AL_MODEL_FLASH_PEERS`, `AL_LLM_HEDGE_AFTER_S`, `AL_LLM_HEDGE_INTERACTIVE_S`, `AL_LLM_SLOW_COOLDOWN_S`, `AL_LLM_OVERLOAD_PAUSE_S` |
| Agent node (`agent_node`) | A timeout, and one retry on transient errors (ADK `retry_config`); a retry that answers is kept, even though ADK reported the failed try before it | `AL_AGENT_TIMEOUT_S` |
| `call_agent` | An answer that does not fit the schema goes back once with the validation error | `AL_REASK_MALFORMED` |
| `repair_loop` | Check, fix, check again: a letter with a number not in the case, a form value that does not fit its box or read back, goes back with what failed, up to two rounds; what still fails is held for a person | `AL_REPAIR_ROUNDS` |
| `GuardPlugin`, `ToolTracePlugin` (`agents/plugins.py`) | A tool's arguments are checked before it runs (only public http(s) addresses are fetched, checked again after every redirect); an agent past its tool limit is told to answer, and is then asked without its tools; every tool call is traced | `AL_MAX_TOOL_CALLS` |
| Screen port | Fetched pages, uploaded papers and replies are screened for planted instructions; a flagged page is never evidence | `AL_SCREEN`, `AL_MODEL_ARMOR_TEMPLATE` |
| `ServiceHealth` (`health.py`) | Every live adapter reports success or failure; `/v1/meta` and the Evaluation page show it. A strict run stops at the first failure instead of falling back. | `AL_STRICT_LIVE` |

The decision controller (`domain/decision.py`) picks the next move for an uncertain value, lead or rule (act, ask,
research, wait or hand to a professional) from measured probabilities, stakes and costs, and research runs in rounds
until nothing is left open ([decision record 0011](adr/0011-decision-controller-and-adaptive-research.md);
`AL_DECISIONS`, `AL_RESEARCH_MODE`, `AL_RESEARCH_MAX_ROUNDS`, `AL_RESEARCH_PARALLEL`). Each decision goes to the trace
with its scores. With `AL_TRACE_TO_CLOUD=true`, ADK's own spans go to Cloud Trace as well.

Offline, each agent answers from an **oracle** (`oracles/`): deterministic code that derives the answer from the
demo family's truth file and the knowledge fixtures, with deliberate disagreements where a demo needs two readers
to differ. Oracles let the whole application be built and tested without a model; they are not a measure of the
agents.

## Ports and adapters

| Port | Offline adapter | Google Cloud adapter (live) |
|---|---|---|
| CaseStore | JSON files per case and collection | Firestore (records as JSON strings, write-through cache) |
| KnowledgeStore | data/knowledge plus an overlay on disk | the same knowledge, with a Firestore overlay for research results |
| BlobStore | files on disk | Cloud Storage |
| Trace | JSON lines | BigQuery `traces` table (with tokens and cost per model call), and spans to Cloud Trace |
| TaskQueue | asyncio tasks | Cloud Tasks (HTTP tasks back to `/internal/tasks/{kind}`) |
| Ocr | the PDF's own text layer, and prepared OCR output for photos | Document AI Enterprise Document OCR |
| Translator | reviewed translation table | Cloud Translation, after the reviewed table |
| Grounding | word and number match | check grounding API, plus the number check |
| Search | BM25 over page snapshots | Google Search through Gemini grounding; Agent Search first if configured |
| Pages | page snapshots (real pages excerpted and dated; fictional institutions' pages) | the live web (public addresses only), with the snapshot when a page is unreachable |
| Screen | patterns for planted instructions, in the app's six languages | Model Armor (prompt injection and malicious links, any language), then the patterns |
| Models | scripted oracles | Gemini through ADK (Agent Platform or the Developer API) |

`adapters/gcp/wiring.py` swaps in what the settings ask for and records each choice; `/v1/meta` returns the list
and the Evaluation page shows it.

## The domain core

- **Case model** (`domain/case.py`): the case file and its collections (people, papers, OCR lines, read values,
  facts, holdings, transactions, payment groups, leads, claims, plans, packs, tasks, checkpoints,
  correspondence, activity, the verification ledger).
- **Plan language** (`domain/plan_language.py`): assertions with sources and levels (draft, sourced,
  dual_verified, adjudicated, outcome_confirmed, gold), and procedure plans: variables (asked or derived),
  documents (with certification and signers), clocks (deadline, window or wait; calendar days, business days with
  a country's holidays, or months), and steps with conditions in a small expression language.
- **Plan runtime** (`domain/plan_runtime.py`): routes and documents for a case's facts, the questions that would
  change the route, due dates, compensation formulas, and the static check every compiled plan must pass.
- **Checks** (`domain/checks.py`): normalisation across scripts and digits, quotes on pages, two-reading
  reconciliation, and the fidelity check (every number in a letter must be in its facts).
- **Calibration** (`domain/calibration.py`): signals to measured probabilities; "Sure" at 95% and above.
  `afterledger calibrate` refits the tables from cases a live model prepared ([evaluation](evaluation.md)).
- **Time**: every moment is stored with its offset. A demo family's story happens on the family's own clock
  (its `timezone`), new activity on the service's (`AL_TIMEZONE`), and the web app shows each time as it was
  written, so 09:00 in Lucknow reads 09:00 in any browser. Dates that are only dates (deadlines, story days)
  carry no time at all.

Knowledge (`data/knowledge`) holds assertions (each with its page and quote), page snapshots, plans for six
countries, the form library (blank forms described as blocks, rendered to PDF and read back), the institution
directory, payment-rail profiles, coverage lists, rates and the reviewed translations.
`afterledger audit-knowledge` checks every quote on its page and every plan and form.

## The service

- 61 typed HTTP routes under `/v1` (cases and their screens, demo families, knowledge, challenges, the
  institution desk, evaluation), described by an OpenAPI document from which the web client is generated.
- Server-sent events for a case's activity and for a challenge's steps; a WebSocket for live voice.
- Screen-shaped views (`app/views/`): each screen gets what it shows in one call, with the words the server
  writes translated into the page's language (`Accept-Language`) through the translation port.
- Writes go through actions (`app/actions.py`), which hold the case's lock and start background work.

## The web app

Next.js app router with a `[locale]` segment (en, hi, ja, es, ml, ar; Arabic right to left). Screens live in
`src/screens`, the case shell (navigation, viewer, language) in `src/components/CaseShell.tsx`, queries and
mutations in `src/lib/queries.ts`, the typed client in `src/lib/api.ts`. All words are in `messages/<locale>.json`
(checked for missing keys and ICU syntax by `pnpm --filter @afterledger/web check:messages`). Names keep their
own script; a name written in another script also shows its Latin form where the page's language needs it.

## Voice

Offline, the browser's own speech recognition and synthesis are used. Live, `/v1/cases/{id}/voice` bridges the
browser and a Gemini Live session: 16 kHz PCM up (from an AudioWorklet), 24 kHz PCM down, transcripts both ways.
The Live model has one tool, `ask_case`, which runs the same grounded answerer as a typed question; the question
and answer are saved in the conversation.

## Deployment

On Google Cloud: the API and the web app on Cloud Run; Firestore, Cloud Storage and BigQuery for state, files and
traces; Cloud Tasks for background work; Document AI, Cloud Translation, check grounding, Model Armor and Gemini on
Agent Platform; Cloud Trace for spans. The API runs as a single instance (each store keeps a write-through cache and each case a lock in
memory), with CPU always allocated so background work and event streams continue between requests, and a
request timeout of an hour for streams and voice. The demo cases are built offline when the image is built and
imported into Firestore and Cloud Storage at first start. Logs are one JSON object per line, so Cloud Logging
reads their severity. See [runbooks/deploy-gcp.md](runbooks/deploy-gcp.md), and
[runbooks/live-run.md](runbooks/live-run.md) for a live run on a computer.

### Load on the one instance

Work runs side by side wherever it can, with a limit at each level so a busy case never starves the others, and
nothing held in memory grows without bound (changed on 19 October 2026, after a live test with real statements):

| What | Side by side | Limit (where) |
|---|---|---|
| A long or dense paper | its parts, each at most 6 pages and 140 text lines (a dense statement page is about 200 lines, so it goes alone) | 10 parts of one paper (`PARTS_AT_ONCE`), 16 reading requests on the server (`READ_CALLS_AT_ONCE`, `modules/reading.py`) |
| The papers of one upload | read together, outside the case's lock: the family can talk, answer and add papers meanwhile | a paper is read once at a time, however often its job is delivered (`modules/pipeline.py`) |
| A part that fails | read again in halves, down to single pages; a page that still fails is named on the paper's card and left out, and its neighbours are checked across the gap; more than half missing fails the paper | reader A and B: 5 minutes a request, a second endpoint after 2, 10 minutes a try, one more try (`agents/factory.py`, `agents/runner.py`) |
| Questions about a country | two researchers each, several searches at once | 3 questions on the server (`RESEARCH_AT_ONCE`); the next says it is waiting. A finished one leaves memory 15 minutes after it is stored |
| Uploads | each file's pages drawn and stored | 3 uploads taken in at once; 25 MB a file, 60 MB and 20 files an upload, 200 pages a PDF (`app/routers/cases.py`) |
| Pages read from the web | cached for six hours | at most 400 pages and 30 million characters, oldest out first (`adapters/gcp/web.py`) |
| Gemini clients | one per model and endpoint, kept with its open connections | (`agents/factory.py`) |

Background jobs come from Cloud Tasks, four at a time. Measured during the live test: memory peaked at 29% of 2 GiB
and CPU at 10%; the time went to waiting for Gemini.

### Growing beyond one instance

The boundaries are already where a larger deployment needs them:
- case locks move from memory to Firestore leases (or Redis), and the write-through caches become read-through
  with Firestore listeners;
- background work is already expressed as named tasks with JSON payloads, so Cloud Tasks (or Pub/Sub with
  Eventarc) can deliver it to any instance;
- the activity stream moves from an in-process hub to Pub/Sub or Firestore listeners;
- agents are stateless calls on context packs, so they scale with the service, and the research service can
  become its own Cloud Run service behind the same port.
