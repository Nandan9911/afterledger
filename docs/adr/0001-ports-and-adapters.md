# 0001 One codebase runs offline and live, through ports and adapters

## Context
The prototype must be built and tested end to end without network calls, then run live on Gemini and Google
Cloud with the same behaviour. Judges and reviewers also need to run it on a laptop without a cloud project.

## Decision
The application depends only on ports (`ports/`): case store, knowledge store, blob store, trace, task queue, OCR,
translator, grounding, search, pages and models. Offline adapters (`adapters/local`, scripted models) and Google
Cloud adapters (`adapters/gcp`, Gemini through ADK) implement them. `runtime.build_deps` builds the offline set;
`adapters/gcp/wiring.py` swaps in each cloud adapter the settings switch on, and records which ones are live.

## Consequences
- Every module is tested offline, deterministically, on the six demo families.
- Each Google Cloud service can be switched on alone (`AL_USE_*`), and falls back to its offline adapter when it is
  off or unreachable (Document AI to the text layer, check grounding to the lexical check, live pages to the dated
  snapshot), saying so in the result.
- `/v1/meta` and the Evaluation page show what the running copy uses, so nothing offline is presented as live.
