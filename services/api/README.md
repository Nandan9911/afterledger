# AfterLedger API

The FastAPI service: the domain core, the nine modules and their 44 agents on the Agent Development Kit, and
the adapters that run it offline or on Google Cloud. Setup is in the repository README
(`python tools/dev.py setup`); this page is for working on the service. How the parts fit:
[docs/architecture.md](../../docs/architecture.md).

## Commands

Run from `services/api` (or through `python tools/dev.py` from the repository root):

```bash
uv run uvicorn afterledger.app.main:app --port 8080 --reload   # the API alone
uv run pytest -m "not slow"                                    # fast tests (about a minute)
uv run pytest                                                  # everything, including the slow end-to-end runs
uv run ruff check src tests                                    # lint
uv run afterledger --help                                      # the command line
```

| Command | What it does |
|---|---|
| `afterledger seed [KEY ...] [--snapshot]` | plays the demo families' timelines into the case store (with the configured model) |
| `afterledger build-demos --out DIR` | seeds them offline and writes bundles a deployment imports (`AL_DEMO_BUNDLES`) |
| `afterledger audit-knowledge` | checks every assertion's quote on its page, every plan and every form |
| `afterledger export-openapi [PATH]` | writes `packages/contracts/openapi.json`, from which the web client's types are generated |
| `afterledger eval [--no-seed] [--json PATH]` | the evaluation ([docs/evaluation.md](../../docs/evaluation.md)) |
| `afterledger calibrate [--write]` | refits the certainty tables from cases a live model prepared |
| `afterledger challenge COUNTRY QUESTION` | runs the live challenge in the terminal |

Settings come from the environment and the repository's `.env`; `.env.example` explains each one.

## Layout

```
src/afterledger/
  app/          main.py (app factory), routers/ (HTTP, SSE, WebSocket), views/ (screen-shaped responses),
                actions.py (writes: case locks, background work), schemas.py (response models)
  modules/      intake, jurisdiction, papers and reading, people, discovery, research, planning, packs (forms
                and letters), follow-up, conversation, the live challenge; pipeline.py chains them
  agents/       specs.py (the registry), schemas.py (output schemas), prompts/<module>.md, runner.py (ADK
                workflows), factory.py (models for each mode)
  oracles/      scripted answers for every agent (offline)
  domain/       the case model, plan language and runtime, calendars, money, names, checks, calibration
  forms/        form context, PDF rendering and read-back, letters
  ports/        what the application needs from outside
  adapters/     local/ (offline), gcp/ (Google Cloud), llm/ (scripted, recording, replaying models)
  seed/         the demo families' timelines, synthetic papers, demo bundles
  eval/         evaluation, calibration, knowledge audit
tests/          pytest; tests marked slow play whole families
```

## Adding to it

**An agent.** Add its output schema to `agents/schemas.py` (typed fields only: the Gemini Developer API refuses
free-form maps), its spec to `agents/specs.py` (module, tier, thinking level, tools), its instructions under a
`## agent_name` heading in `agents/prompts/<module>.md`, and an oracle in `oracles/<module>.py`
(`@oracle("agent_name")`) so the offline flow keeps working. Call it from a module with
`call_agent(deps, "agent_name", pack)`, where the pack is the one JSON context the agent sees. `tests/test_agents.py`
checks that every agent has instructions, an oracle and a schema both Gemini endpoints accept.

**A Google Cloud service.** Define what the application needs in `ports/`, write the offline adapter in
`adapters/local/` and the cloud one in `adapters/gcp/`, and switch it in `adapters/gcp/wiring.py`, recording the
choice in `deps.extras["services"]` so the Evaluation page shows it. A cloud adapter that fails should fall back
to its offline stand-in and say so.

**A country.** Rules go in `data/knowledge` as assertions (one sentence, the verbatim quote, the page, the date
checked) and plans in the plan language; the page itself is saved as a snapshot so the quote can be checked.
`afterledger audit-knowledge` must pass. Without that, a live run researches the country when a family needs it.

**A screen's words.** Server-written text is in English and is translated through the translation port in
`app/views/localize.py`; the web app's own words are in `apps/web/messages/<locale>.json`.

## Conventions

- Agents propose; code decides. Anything exact (dates, money, clocks, routes, quotes, read-backs, numbers in
  letters) is code in `domain/`, never a model's answer.
- Every file is read and written as UTF-8, and paths are built with `pathlib`, so the service runs the same on
  Windows.
- A route returns a screen-shaped view in one call; writes go through `app/actions.py`, which holds the case's
  lock and starts background work.
- After changing a route or a response model, run `python tools/dev.py contracts` to regenerate the web client.
