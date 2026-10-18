# 0012 A slow model gives way to the same model elsewhere; a recorded run can be resumed

## Context
On 15 October 2026 Gemini 3.8 Flash on the global endpoint was slow: a one-word prompt took 4 to 38 seconds, and
about one call in three ended in `504 DEADLINE_EXCEEDED`, from Google Cloud's own network as well as from a
family's computer in India. The same model on the `us` multi-region endpoint answered in 3 to 8 seconds, and Gemini
3.6 Flash on global in 1 to 3. The harness then knew one move for a slow call: wait for its deadline (90 seconds,
180 for slow agents) and try the same endpoint again. Reading one statement took ten minutes.

A live test run on a family's own computer has a second limit: a command there may run for three minutes at most,
and a family's case takes longer than that.

## Decision
**Routes.** A call's routes are its model on the main endpoint (`GOOGLE_CLOUD_LOCATION`, normally global), the same
model on a backup multi-region endpoint (`AL_LLM_BACKUP_LOCATION`, `us`), then the model's peers
(`AL_MODEL_FLASH_PEERS`: Gemini 3.6 Flash for Gemini 3.8 Flash, the same price). These answer equally well, so moving
between them is not a fallback to a smaller model and does not stop a strict run.
- **Hedging.** With no answer after `AL_LLM_HEDGE_AFTER_S` (20 seconds; 40 for pro, tool-using and image-reading
  agents), the same request also goes to the next route, and the first answer wins; the other request is cancelled.
  The agents a person waits on as they type or speak (intake and answering questions) hedge after
  `AL_LLM_HEDGE_INTERACTIVE_S` (8 seconds): on 15 October a spoken question took 38 seconds to answer on the
  slower timing.
- **Passing over.** A route that ran past its deadline, was overloaded, or stayed silent while another answered is
  passed over for `AL_LLM_SLOW_COOLDOWN_S` (300 seconds), twice as long each further time it is still slow (at most
  an hour); calls that find it slow at the same moment count once. A route that does not serve the model (a preview
  model is on global only) is dropped after its first 404.
- **Smaller models.** Out of quota or overloaded on every route, the call waits `AL_LLM_OVERLOAD_PAUSE_S` (8
  seconds) and asks the same model once more: on 15 October Gemini 3.1 Pro answered `500 INTERNAL` three times in a
  row and normally a minute later. Still failing, pro falls to flash and flash to lite, as before, and that is
  reported as a failure (a strict run stops). Slow on every route, the call is tried again later rather than
  answered by a smaller model.

Every move is in the response's metadata, so the trace's `model` column names the route that answered
(`gemini-3.8-flash@us`), priced with the non-global markup in `data/knowledge/prices.yaml`; the slow routes are on
the Evaluation page under the services, with when they were last slow and whether they are still passed over.

**Resumable recordings.** With `AL_LLM_RECORD_RESUME=true`, a recording run that was cut off can be run again: a
request it already recorded (the same key, step and fingerprint, the same instructions and answer shape, and the
same tool results since the turn began) is answered from its cassette, and only the rest goes to Gemini: a prompt
edited between two pieces is asked again, not answered with what the old prompt got. Cassettes keep each tool call's thought signature, because Gemini 3 refuses a later step that sends back a
call without one; a cassette from before that has none, and its call is made again. The search results and pages the
run saw are kept beside the cassettes (`_web/`), so every piece of a run sees what the first saw.

## Consequences
- A slow endpoint costs a call 20 seconds once, not 90 seconds on every call.
- A non-global endpoint costs about a tenth more per token while global is passed over.
- Gemini 3.6 Flash is retired on the Agent Platform on 19 November 2026; `AL_MODEL_FLASH_PEERS` must change by then.
- A live run on a computer that limits each command to a few minutes is made in pieces
  (`docs/runbooks/live-run.md`), and is billed once: a replayed call uses no tokens, and its trace line says
  `replayed`.
