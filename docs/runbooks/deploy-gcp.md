# Deploy to Google Cloud

This runbook puts AfterLedger on Google Cloud: the API and the web app on Cloud Run, with Firestore, Cloud Storage,
BigQuery, Cloud Tasks, Document AI, Cloud Translation, check grounding, Model Armor, Cloud Trace and Gemini on Agent
Platform. There are two ways to do it, and both create the same things:

- **The deploy script** (`infra/scripts/deploy.sh`): gcloud, bq and curl only. Run it in Cloud Shell. Running it
  again updates the deployment.
- **Terraform** (`infra/terraform`), if you keep infrastructure as code.

## What gets created

| Resource | Name | Notes |
|---|---|---|
| Cloud Run service | `afterledger-api` | 1 instance at most, 2 vCPU, 2 GiB, CPU always allocated, requests up to 60 minutes (event streams, voice), session affinity |
| Cloud Run service | `afterledger-web` | 0 to 3 instances, 1 vCPU, 1 GiB |
| Artifact Registry | `afterledger` | the two images |
| Firestore | `(default)`, Native mode | case files, and research results for the next family |
| Cloud Storage bucket | `afterledger-PROJECT_ID` (script) | papers, page images, filled forms |
| BigQuery | dataset `afterledger`, table `traces` (partitioned by day) | one row per model call (with tokens and estimated cost), agent run, tool call and decision; a table from an earlier deployment gets the token and cost columns |
| Cloud Tasks queue | `afterledger-work` | background work, delivered back to the API |
| Document AI processor | `afterledger-ocr` (Enterprise Document OCR) | reading papers |
| Model Armor template | `afterledger-screen`, in `us-central1` | screens fetched pages, uploaded papers and replies for planted instructions and malicious links, in any language (`asia-south1` offers neither filter) |
| Service account | `afterledger-api` | the API's identity, with the roles below |

The API's service account gets: Cloud Datastore User (Firestore), BigQuery Data Editor and Job User, Cloud Tasks
Enqueuer, Document AI API User, Cloud Translation API User, Discovery Engine User (Agent Search) and Discovery Engine
Viewer (check grounding needs its `groundingConfigs.check`), `roles/aiplatform.user` (Gemini on Agent Platform, formerly Vertex AI), Model Armor User (screening),
Cloud Trace Agent (spans), Logs Writer, and Storage Object Admin on the bucket only.

The API runs with `AL_TRACE_TO_CLOUD=true` (spans to Cloud Trace) and `AL_STRICT_LIVE=false` (a failing service is
reported on `/v1/meta` and the Evaluation page, and the work goes on with its offline stand-in). `CASE_BUDGET_USD`
(script) or `case_budget_usd` (Terraform) caps what one case may spend on optional model work.

Why one API instance: each store keeps a write-through cache and each case a lock in memory (ADR 0006). Why CPU
always allocated: background work and event streams continue between requests. The demo cases are built offline
when the image is built, and imported into Firestore and Cloud Storage the first time the API starts (ADR 0009).

Regions: Cloud Run, Firestore, the bucket, BigQuery and Cloud Tasks use one region (`asia-south1`, Mumbai, by
default). Gemini is called through the global endpoint; the Live (voice) model runs in `us-central1` (it is
served only in US and EU regions); Document AI's processor is in `us` (or `eu`).

## Before you start

- A Google Cloud project with billing, and an account that is Owner of it (or can enable APIs, create the
  resources above and grant roles).
- Cloud Shell (it has gcloud, bq, git, Python and Terraform), or a computer with the Google Cloud CLI signed in.
- The repository: `git clone https://github.com/YOUR_ACCOUNT/AfterLedger.git && cd AfterLedger`

## A. The deploy script

```bash
PROJECT_ID=your-project-id bash infra/scripts/deploy.sh
```

It turns on the APIs, creates what is missing, builds both images with Cloud Build (about ten minutes, most of
it building the demo cases), deploys the web app and then the API, and prints both addresses. Options, as
environment variables in front of the command:

| Variable | Default | What it does |
|---|---|---|
| `REGION` | `asia-south1` | region for Cloud Run and the data services |
| `MODE` | `live` | `live`: Gemini and the Google Cloud services; `offline`: scripted responses on the same cloud stores (a smoke test without model calls) |
| `MIN_INSTANCES` | `0` | `1` keeps the API running, so nobody waits for a cold start during a demonstration (it costs more) |
| `DOCAI_LOCATION` | `us` | Document AI region, `us` or `eu` |
| `LIVE_LOCATION` | `us-central1` | region for the Gemini Live model |
| `TIMEZONE` | `Asia/Kolkata` | the clock new activity is written on (a demo family's story keeps its own) |
| `BUCKET` | `afterledger-PROJECT_ID` | the Cloud Storage bucket |
| `TAG` | the date and time | the image tag |
| `SITE` | `product` | `product`: the public site, with sample cases only, no Evaluation page and no replayed stories; empty: everything, for development |
| `DEMO_FAMILIES` | `verma,nair,sato` | the sample families the public site shows (empty: all six) |
| `CASE_BUDGET_USD` | `3` on the public site | what one case may spend on optional model work, in US dollars (empty: no limit) |
| `WEB_MIN_INSTANCES` | `0` | `1` keeps the web app warm as well |
| `AGENT_TIERS` | empty | an agent on another model tier than its own, for example `situation_extractor=flash,gap_planner=flash`; the intake conversation (listener, situation_extractor, gap_planner, tone_critic) runs on Flash-Lite by default |
| `LLM_TIER_LOCATIONS` | `lite=asia-south1,flash=us` | the Gemini endpoint tried first for each model tier, picked by measured speed (Flash-Lite answers in under a second from Mumbai; Gemini 3.8 Flash is several times faster on `us` than on `global`); the others stay as fallbacks |
| `FIREBASE` | `yes` | publishes `https://PROJECT_ID.web.app` through Firebase Hosting at the end (needs the `firebase` CLI signed in; see B below); `no` skips it |

Run the same command again after a change: it builds new images and deploys them. The internal token the API
and Cloud Tasks share is kept across deploys, so tasks already queued are still accepted.

### Accounts and the admin

On the public site (`SITE=product`) people sign up with a name, an email and a password, and each case is open only
to the account that started it; the sample cases stay open to everyone. One admin account sees every account (name,
email, cases) on the **Accounts** page and can add, edit and delete them; deleting an account deletes its cases and
their papers. Give the admin's details the first time you deploy (they are kept on later deploys):

```bash
ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a long password' ADMIN_NAME='Your name' \
  PROJECT_ID=your-project-id bash infra/scripts/deploy.sh
```

Sign in with that email and password; **Accounts** appears in the header. To change the admin's password, deploy
again with a new `ADMIN_PASSWORD` (the admin's own sign-ins then end). The script makes a random `AL_AUTH_SECRET`
once, which signs every sign-in, and keeps it across deploys. Accounts live in Firestore (`users`), passwords as
salted scrypt hashes. `AL_AUTH=off` turns accounts off; `AL_AUTH=required` turns them on outside the public site.

## B. The public address: Firebase Hosting

Firebase Hosting gives the web app the address `https://PROJECT_ID.web.app`, with SSL and Google's CDN in front,
and passes every request to the `afterledger-web` service on Cloud Run (`firebase.json` at the repository root;
change its `region` if you deploy elsewhere than `asia-south1`). The browser calls the API at its own `run.app`
address, which already accepts the Firebase addresses, so voice and live updates work as before.

Once per project, in Cloud Shell:

```bash
firebase login --no-localhost                 # follow the link, sign in with the project owner's account
firebase projects:addfirebase PROJECT_ID      # adds Firebase to the Google Cloud project (skip if it has it)
```

The deploy script then creates the Hosting site named in `firebase.json` (`afterledger`, so
`https://afterledger.web.app`) if the project has none, and runs `firebase deploy --only hosting`. To run it on its
own: `firebase hosting:sites:create afterledger --project PROJECT_ID` (once), then
`firebase deploy --only hosting --project PROJECT_ID`. If that site name is taken, put another in `firebase.json`. A custom domain can be added later under Hosting in the
Firebase console; add it to `AL_CORS_ORIGINS` too.

## C. Terraform

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars      # set project_id; see the comments for the options
terraform init

# 1. the APIs and the image registry first: the Cloud Run services need images to exist
terraform apply -target=google_project_service.apis -target=google_artifact_registry_repository.images

# 2. build the images (from the repository root)
cd ../..
gcloud builds submit --config infra/cloudbuild.yaml --substitutions=_REGION=asia-south1,_TAG=v1 .

# 3. everything else
cd infra/terraform
terraform apply -var image_tag=v1
terraform output web_url
```

Set `create_firestore_database = false` if the project already has a `(default)` Firestore database, and
`keep_one_instance_warm = true` for no cold starts. To update: build with a new tag, then
`terraform apply -var image_tag=NEW_TAG`.

## After deploying: check it

1. `curl https://afterledger-api-PROJECT_NUMBER.REGION.run.app/v1/health` answers `{"ok": true, ...}`.
2. `/v1/meta` lists what each part runs on. A live deployment shows `firestore`, `cloud_storage`, `bigquery`,
   `cloud_tasks`, `document_ai`, `cloud_translation`, `check_grounding`, `live_web`, `google_search`, `gemini`
   for the agents and `gemini_live` for voice.
3. Open the web app (`https://PROJECT_ID.web.app`). The first start imports the sample cases (a minute or two);
   the home page shows them being prepared.
4. With `SITE` empty, the Evaluation page's **What this copy runs on** should match step 2 (the public site has no
   Evaluation page).
5. Try the live challenge, a new case, and **Speak** on a case's Ask screen (voice works over HTTPS).

Logs: Cloud Run → `afterledger-api` → Logs in the console, or
`gcloud run services logs read afterledger-api --region REGION --limit 100`. Each line is structured, so it can be
filtered by severity. Agent calls are in BigQuery:

```sql
SELECT agent, model, COUNT(*) AS calls, ROUND(AVG(ms)) AS avg_ms, SUM(tokens_in) AS tokens_in,
       SUM(tokens_out + IFNULL(tokens_thinking, 0)) AS tokens_out, ROUND(SUM(cost_usd), 4) AS cost_usd
FROM `PROJECT_ID.afterledger.traces`
WHERE type = 'model_call' AND DATE(at) = CURRENT_DATE()
GROUP BY agent, model ORDER BY cost_usd DESC
```

The same totals are on the Evaluation page (**Model use**), and spans in Cloud Trace (Trace explorer, service
`afterledger-api`).

## Things to know

- **Demo cases are imported once.** A redeploy does not overwrite cases already in Firestore. **Reset this demo
  case** in the app imports the bundled version again.
- **The API's addresses.** Cloud Run gives each service a deterministic address
  (`https://SERVICE-PROJECT_NUMBER.REGION.run.app`) and another `run.app` address. The web app is told the API's
  deterministic address, and the API accepts requests from both of the web app's addresses. If you add a custom
  domain, add it to `AL_CORS_ORIGINS` (a comma-separated list; gcloud needs another delimiter for commas):

  ```bash
  gcloud run services update afterledger-api --region REGION \
    --update-env-vars "^@^AL_CORS_ORIGINS=https://afterledger-web-PROJECT_NUMBER.REGION.run.app,https://your.domain"
  ```
- **Cold starts.** With no minimum instance, the first request after a quiet spell waits for the API to start
  (a few seconds; the cases are already in Firestore). Use `MIN_INSTANCES=1` or `keep_one_instance_warm` while
  people are trying it.
- **Background work** goes through Cloud Tasks to `POST /internal/tasks/{kind}` on the API, with a shared token
  in a header; a task can run for up to 30 minutes and is retried up to three times.
- **After live runs**, measure the agents and refit the certainty labels ([evaluation.md](../evaluation.md)).
  `afterledger calibrate --write` changes `data/knowledge/calibration.json`: commit it and redeploy, so the
  deployment uses the refitted tables.

## Troubleshooting

| What you see | What to do |
|---|---|
| The build fails with a permission error for `PROJECT_NUMBER-compute@developer.gserviceaccount.com` | New projects build with the Compute Engine default service account, which may lack the roles a build needs. `gcloud builds get-default-service-account` shows which account builds use; give it the Cloud Build Service Account role: `gcloud projects add-iam-policy-binding PROJECT_ID --member serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com --role roles/cloudbuild.builds.builder` |
| `gcloud firestore databases create` fails because a database exists | Fine: the script goes on. In Terraform set `create_firestore_database = false`. |
| The web app says it cannot reach the API | Open the web app's own address (the deploy script prints it). For another address, add it to `AL_CORS_ORIGINS` (above). |
| `/v1/meta` shows an offline stand-in in a live deployment | The API's start-up log line `services: {...}` lists what is live; check the matching environment variable on `afterledger-api` and the service account's roles. |
| Gemini answers `404` for a model | Set `AL_MODEL_FLASH`, `AL_MODEL_PRO`, `AL_MODEL_LITE` or `AL_MODEL_LIVE` on `afterledger-api` to a model your project can call. |
| `429 RESOURCE_EXHAUSTED` | A Gemini quota; wait, or request more in the console (IAM & Admin → Quotas). |
| Voice does not start | Live voice needs `MODE=live`. `AL_LIVE_LOCATION` must be a US or EU region. The browser must allow the microphone. |
| Tasks fail with `403` | The `AL_INTERNAL_TOKEN` changed while tasks were queued (the deploy script keeps it; a hand edit may not). Those tasks stop after three tries; new work uses the new token. |

## Taking it down

Script deployment:

```bash
gcloud run services delete afterledger-web afterledger-api --region REGION
gcloud tasks queues delete afterledger-work --location REGION
gcloud storage rm --recursive gs://afterledger-PROJECT_ID
bq rm -r -f -d PROJECT_ID:afterledger
gcloud artifacts repositories delete afterledger --location REGION
gcloud iam service-accounts delete afterledger-api@PROJECT_ID.iam.gserviceaccount.com
```

Delete the Document AI processor in the console (Document AI → `afterledger-ocr`), and the Firestore database
(`gcloud firestore databases delete --database='(default)'`) if nothing else uses it. A deleted Cloud Tasks queue's
name cannot be used again for seven days, so a new deployment soon after needs another queue name.

Terraform deployment: `terraform destroy` (the Firestore database, if Terraform created it, is deleted with it).
