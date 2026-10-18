# Live run on your computer

This runbook switches AfterLedger from scripted responses to Gemini and, if you want them, the Google Cloud
services, while the app still runs on your computer. Nothing else changes: the same screens, the same demo cases,
the same checks. Each part goes live separately, and the Evaluation page (**What this copy runs on**) shows
which parts are live at any moment.

| Part | Offline | Live | Needs |
|---|---|---|---|
| Agents | Scripted responses | Gemini (`gemini-3.8-flash`, `gemini-3.1-pro-preview`, `gemini-3.5-flash-lite`) | `AL_LLM=gemini` and an AI Studio key or Agent Platform |
| Spoken conversation | The browser's own speech recognition and voice | Gemini Live (`gemini-3.8-live`) | `AL_LLM=gemini`, `AL_USE_LIVE_VOICE=true` |
| Research search | Saved page snapshots | Google Search through Gemini | `AL_LLM=gemini`, `AL_USE_LIVE_WEB=true` |
| Reading official pages | Saved page snapshots | The live web (snapshot if a page cannot be reached) | `AL_USE_LIVE_WEB=true` |
| Reading papers (OCR) | The PDF's text layer and prepared OCR output for photos | Document AI Enterprise Document OCR | a project, a processor |
| Translation | Translations reviewed by hand | Cloud Translation, after the reviewed ones | a project |
| Checking claims against sources | Words and numbers matched by code | Check grounding API, and numbers by code | a project |
| Screening pages, papers and replies for planted instructions | Patterns in the app's six languages | Model Armor, then the patterns | a project, a Model Armor template (`AL_MODEL_ARMOR_TEMPLATE`) |
| Case files, papers, traces | Files under `.data` | Firestore, Cloud Storage, BigQuery | a project, a bucket (optional on a computer) |
| Spans of each run | none | Cloud Trace | a project, `AL_TRACE_TO_CLOUD=true` |

`AL_MODE=live` switches the Google Cloud services on; `AL_LLM=gemini` switches the agents on. A service whose
setting or credentials are missing keeps its offline stand-in and says so; nothing offline is shown as live.

## 1. Choose how to reach Gemini

**A. An AI Studio key (quickest).** Create a key at https://aistudio.google.com/apikey. Gemini, Gemini Live and
Google Search grounding work with the key alone. Document AI, Cloud Translation and check grounding still need
a Google Cloud project (B's steps 2 to 4); without one they stay offline.

**B. Gemini Enterprise Agent Platform (formerly Vertex AI) in your Google Cloud project.** Everything can go live.

1. Install the Google Cloud CLI and sign in: `gcloud init`.
2. Turn on the APIs (the last three only for screening and spans):
   ```bash
   gcloud services enable aiplatform.googleapis.com documentai.googleapis.com translate.googleapis.com \
     discoveryengine.googleapis.com modelarmor.googleapis.com cloudtrace.googleapis.com telemetry.googleapis.com
   ```
3. Give the app your credentials, and a project to bill the calls to (Translation and check grounding refuse
   calls from your own credentials without one):
   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project YOUR_PROJECT_ID
   ```
4. Your account needs these roles in the project (an Owner or Editor has them already): `roles/aiplatform.user`
   (Agent Platform; its title was Vertex AI User before the rename),
   Document AI API User, Cloud Translation API User, Discovery Engine User and Discovery Engine Viewer (check
   grounding needs the viewer role's `groundingConfigs.check`), and for screening and spans Model Armor User and
   Cloud Trace Agent. A service account key works instead of your own sign-in: put its path in
   `GOOGLE_APPLICATION_CREDENTIALS` (under `.data/`, which git ignores).

## 2. Create the Document AI processor (option B, once)

In the console: **Document AI → Create processor → Enterprise Document OCR**, region **US** (or EU), name
`afterledger-ocr`. Copy the processor's ID. Or from a terminal:

```bash
curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" \
  -d '{"type": "OCR_PROCESSOR", "displayName": "afterledger-ocr"}' \
  "https://us-documentai.googleapis.com/v1/projects/YOUR_PROJECT_ID/locations/us/processors"
```

In Windows PowerShell:

```powershell
$token = gcloud auth print-access-token
Invoke-RestMethod -Method Post -Headers @{ Authorization = "Bearer $token" } -ContentType "application/json" `
  -Body '{"type": "OCR_PROCESSOR", "displayName": "afterledger-ocr"}' `
  -Uri "https://us-documentai.googleapis.com/v1/projects/YOUR_PROJECT_ID/locations/us/processors"
```

The answer's `name` ends with `/processors/<ID>`; either the ID or the whole name goes in
`AL_DOCAI_OCR_PROCESSOR`.

## 3. Edit `.env`

Change these lines in the `.env` at the repository root (`python tools/dev.py setup` created it from
`.env.example`, where every setting is explained):

```ini
AL_MODE=live
AL_LLM=gemini
# keep every model call as a cassette, to replay this run offline later (optional)
AL_LLM_RECORD_DIR=./.data/cassettes

# A: the AI Studio key
GOOGLE_API_KEY=your-key
# B: Agent Platform instead (leave GOOGLE_API_KEY empty)
GOOGLE_GENAI_USE_ENTERPRISE=true
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
AL_LIVE_LOCATION=us-central1

# with a project (B, or A plus a project)
AL_DOCAI_OCR_PROCESSOR=the-processor-id
AL_DOCAI_LOCATION=us
```

`AL_USE_DOCUMENT_AI`, `AL_USE_TRANSLATION`, `AL_USE_CHECK_GROUNDING`, `AL_USE_LIVE_WEB` and `AL_USE_LIVE_VOICE` are
already `true`; set one to `false` to keep that part offline. Case files stay on your computer (`AL_STORE=local`);
section 7 moves them to Firestore and Cloud Storage.

The model names are the defaults in `.env.example` (`AL_MODEL_FLASH`, `AL_MODEL_PRO`, `AL_MODEL_LITE`,
`AL_MODEL_LIVE`). On Agent Platform the Live model is served only in US and EU regions, which is why
`AL_LIVE_LOCATION` is separate from `GOOGLE_CLOUD_LOCATION=global`.

Screening with Model Armor needs a template in a region that offers the prompt-injection and malicious-URL filters
with multi-language detection: `us-central1` does, `asia-south1` offers neither (October 2026). The deploy script
creates one; by hand:

```bash
curl -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" -H "Content-Type: application/json" \
  -d '{"filterConfig": {"piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED", "confidenceLevel": "MEDIUM_AND_ABOVE"},
       "maliciousUriFilterSettings": {"filterEnforcement": "ENABLED"}},
       "templateMetadata": {"multiLanguageDetection": {"enableMultiLanguageDetection": true}}}' \
  "https://modelarmor.us-central1.rep.googleapis.com/v1/projects/YOUR_PROJECT_ID/locations/us-central1/templates?templateId=afterledger-screen"
```

then `AL_MODEL_ARMOR_TEMPLATE=projects/YOUR_PROJECT_ID/locations/us-central1/templates/afterledger-screen`.

## 4. Start, and check what is live

```bash
python tools/dev.py dev
```

Open http://localhost:3000/en/evaluation and look at **What this copy runs on**: each part says its Google
service or its offline stand-in, and **Agents** lists the models. The same list is at
http://localhost:8080/v1/meta (`services`), and the API logs it when it starts (`services: {...}`).

The six demo cases are still the ones built offline, and their headers say **Offline: scripted responses**. A
case the live model prepares says **Live: Gemini**.

**What it costs.** Each step's model use (calls, tokens, an estimated cost from the list prices in
`data/knowledge/prices.yaml`) is on its Activity line, the case's total at the top of Activity, and every case's,
agent's and model's on the Evaluation page under **Model use**. `AL_CASE_BUDGET_USD` caps what a case may spend on
optional work (extra research rounds, repairs); required steps always run. **Problems since the service started**
on the same page lists any live service that failed, with its last error.

**A strict test run.** `AL_STRICT_LIVE=true` makes the first failing Google Cloud service stop the work with its
error, instead of falling back to its offline stand-in, so a test cannot pass on a stand-in unnoticed. Leave it
`false` for a demonstration.

## 5. Things to try

- **Start a case of your own** on the home page, and tell the story by voice or text in any of the six
  languages. With live voice, **Speak** on the Ask screen opens a conversation with Gemini Live; it answers
  questions about the case only through the same grounded answerer as typed questions. The microphone works on
  `localhost` and on HTTPS addresses.
- **Upload papers**: photos and scans are read by Document AI and two readers; disagreements get a closer look.
- **Live challenge**: name a country, an institution and a situation, and watch the research.
- **Watch it from the first paper** (a demo case's menu, or **Watch** on the home page) replays a family's whole
  story through Gemini. On 15 October a story took 15 to 45 minutes, 120 to 340 model calls and $0.80 to $2 of
  model use ([evaluation.md](../evaluation.md), section 5); afterwards the case is a live case. **Reset this demo case** puts back the saved version: the offline one, unless you saved a live one with
  `afterledger seed KEY --snapshot`.

From the command line (in `services/api`):

```bash
uv run afterledger challenge FR "My father had life insurance; how do I find out if I am a beneficiary?"
uv run afterledger seed verma --snapshot     # prepare one family with the live model, and keep it
```

## 6. Recording and replaying

With `AL_LLM_RECORD_DIR` set, every model call is written as a cassette (one JSON file per call, by agent). To
play a recorded run back offline, without keys:

```ini
AL_MODE=offline
AL_LLM=replay
AL_LLM_REPLAY_DIR=./.data/cassettes
```

A replayed case counts as a live case. If a call has no cassette (the story went somewhere the recording did not),
the replay answers it from the scripted responses, marks the case scripted and says so in its activity feed, so
it is never measured as live. `AL_LLM_REPLAY_STRICT=true` makes such a call an error instead (a test of a pure
replay). Calls are matched by agent, the context pack's key and the tool step; two different requests under the same
key (the day's note for two family members, say) are kept apart by a fingerprint of the request that ignores record
ids and clock times. A replay itself uses no tokens.

### A run made in pieces

Where a command may only run for a few minutes (a remote shell, a CI step), set `AL_LLM_RECORD_RESUME=true` with a
record folder and run the same command again until it finishes: each run answers what earlier runs recorded from their
cassettes (matched by key, step, fingerprint, instructions and the tool results so far) and sends only the rest to
Gemini, so the run is billed once. Change a prompt between pieces and the calls it shapes go to Gemini again. The
search results and pages a run saw are kept in the record folder's `_web/`, so each piece sees the same web. Start
each piece from an empty state folder; a replayed call's trace line says `replayed`.

### When Gemini is slow

A model that is slow on its endpoint gives way to the same model on `AL_LLM_BACKUP_LOCATION` (`us`), then to its peers
(`AL_MODEL_FLASH_PEERS`), after `AL_LLM_HEDGE_AFTER_S` seconds with no answer, or `AL_LLM_HEDGE_INTERACTIVE_S` for
the agents a person waits on as they type or speak ([decision record
0012](../adr/0012-routes-hedging-and-resumable-recordings.md)). The Evaluation page lists the endpoints that were slow;
the trace's `model` column names the one that answered (`gemini-3.8-flash@us`). On 15 October Gemini 3.6 Flash
answered more than half of all calls this way. It is retired on the Agent Platform on 19 November 2026: before then,
set `AL_MODEL_FLASH_PEERS` to the model that replaces it, or leave it empty.

## 7. Firestore and Cloud Storage from your computer (optional)

To keep case files in the cloud while the app runs locally, create a Firestore database (Native mode), a bucket
and a BigQuery dataset (the deploy script does all three; see [deploy-gcp.md](deploy-gcp.md)), then:

```ini
AL_STORE=gcp
AL_GCS_BUCKET=your-bucket
AL_BQ_DATASET=afterledger
```

At start the API imports the demo bundles into Firestore and Cloud Storage. Background work stays in the API
process unless `AL_TASKS_QUEUE` is set, which needs an address Cloud Tasks can reach, so leave it empty on a
computer.

## 8. After a live run

1. Measure the agents on the cases the live model prepared: `uv run afterledger eval --no-seed`, or **Run the
   evaluation now** on the Evaluation page.
2. Refit the certainty labels from the same cases: `uv run afterledger calibrate`, then `--write` to save, and
   restart the API ([evaluation.md](../evaluation.md)).
3. Keep the cassettes if you want to show the run again without keys.

## Troubleshooting

| What you see | What to do |
|---|---|
| `API key not valid` | Check `GOOGLE_API_KEY`, and that `GOOGLE_GENAI_USE_ENTERPRISE` is empty when you use a key. |
| `PERMISSION_DENIED` from Agent Platform | `gcloud services enable aiplatform.googleapis.com`; your account needs `roles/aiplatform.user`. |
| `... requires a quota project` | `gcloud auth application-default set-quota-project YOUR_PROJECT_ID` |
| A model is not found (404) | The names in `AL_MODEL_*` must be models your project can call; change them in `.env`. |
| `RESOURCE_EXHAUSTED` (429) | A quota was reached. Each call is tried up to three times, with a pause, then falls to the next model tier (pro, flash, lite) and Activity's trace says so (`AL_FALLBACK=false` turns that off); wait a minute, or ask for more quota. Watching a whole family's story makes the most calls. |
| A step stops with `timed out` | A call ran past its deadline twice. `AL_LLM_HTTP_TIMEOUT_S` (90 seconds, twice that for pro, tool-using and image-reading agents) is how long one request may wait; `AL_AGENT_TIMEOUT_S` (300, doubled the same way) is the agent's whole turn. Reading a long statement from its image can take a few minutes; raise both if your connection is slow. |
| Check grounding: `PERMISSION_DENIED` (`groundingConfigs.check`) | The account needs Discovery Engine Viewer as well as Discovery Engine User. |
| Model Armor: `CAPABILITY_NOT_SUPPORTED` or `template not found` | The template must be in a region with the prompt-injection and malicious-URL filters (`us-central1`), and `AL_MODEL_ARMOR_TEMPLATE` must name it in full. Screening falls back to the patterns, and **Problems since the service started** says so. |
| `Max number of llm calls limit of 400 exceeded` | An agent kept calling its tools; the run stopped safely. Try again, or raise `AL_MAX_LLM_CALLS`. |
| A part still shows its offline stand-in | Its `AL_USE_...` setting, `GOOGLE_CLOUD_PROJECT` or the processor ID is missing; the API's start-up log lists what is live. |
| Document AI: processor not found | `AL_DOCAI_LOCATION` must be the processor's region (`us` or `eu`). |
| **Speak** uses the browser's voice, not Gemini Live | Live voice needs `AL_MODE=live`, `AL_LLM=gemini` and `AL_USE_LIVE_VOICE=true`. On Agent Platform, `AL_LIVE_LOCATION` must be a US or EU region. |
| Voice stops with an error | The message says why (closed by the model, no microphone permission). Typing always works. |
| The page cannot reach the API | The API must be on http://localhost:8080 (or set `AL_API_BASE` in `.env`), and `AL_CORS_ORIGINS` must include the web app's address. |
