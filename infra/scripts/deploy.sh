#!/usr/bin/env bash
# Deploys AfterLedger to Google Cloud with gcloud, bq and curl only. Run it from the repository root in Cloud Shell
# (or anywhere the Google Cloud CLI is installed and signed in):
#
#   PROJECT_ID=my-project bash infra/scripts/deploy.sh
#
# Optional: AUTH (off, for a short test window without sign-in; required; empty: on for the public site),
# REGION (asia-south1), MODE (live | offline), MIN_INSTANCES (0; 1 keeps the API warm for a demo),
# DOCAI_LOCATION (us), LIVE_LOCATION (us-central1), MODEL_ARMOR_LOCATION (us-central1: it must offer the
# prompt-injection and malicious-URI filters with multi-language detection, which asia-south1 does not),
# CASE_BUDGET_USD (3 on the public site; empty: no limit on a case's optional model work), TIMEZONE (Asia/Kolkata),
# BUCKET, TAG, SITE (product: no evaluation pages, sample cases only; empty: everything), DEMO_FAMILIES
# (verma,nair,sato), WEB_MIN_INSTANCES (0; 1 keeps the web app warm), LLM_TIER_LOCATIONS (each model tier's fastest
# Gemini endpoint), AGENT_TIERS (an agent on another model tier than its own, e.g. situation_extractor=flash;
# empty: each on its own), FIREBASE (yes: publish the Firebase Hosting address https://PROJECT_ID.web.app at the end, when
# the firebase CLI is signed in; no: skip it), ADMIN_EMAIL, ADMIN_PASSWORD and ADMIN_NAME (the one admin account,
# who manages the others; kept from the last deploy when not given).
# Running it again updates the deployment.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}
REGION=${REGION:-asia-south1}
MODE=${MODE:-live}
MIN_INSTANCES=${MIN_INSTANCES:-0}
DOCAI_LOCATION=${DOCAI_LOCATION:-us}
LIVE_LOCATION=${LIVE_LOCATION:-us-central1}
MODEL_ARMOR_LOCATION=${MODEL_ARMOR_LOCATION:-us-central1}
SITE=${SITE-product}
DEMO_FAMILIES=${DEMO_FAMILIES-verma,nair,sato}
WEB_MIN_INSTANCES=${WEB_MIN_INSTANCES:-0}
LLM_TIER_LOCATIONS=${LLM_TIER_LOCATIONS-lite=asia-south1,flash=us}
FIREBASE=${FIREBASE:-yes}
if [ "$SITE" = "product" ]; then CASE_BUDGET_USD=${CASE_BUDGET_USD-3}; else CASE_BUDGET_USD=${CASE_BUDGET_USD-}; fi
TIMEZONE=${TIMEZONE:-Asia/Kolkata}
BUCKET=${BUCKET:-afterledger-${PROJECT_ID}}
TAG=${TAG:-$(date +%Y%m%d-%H%M%S)}

say() { printf '\n== %s\n' "$*"; }

# The admin's password is asked for here, hidden, when ADMIN_EMAIL is given without it, so it is never typed into
# a command line (and kept out of the shell's history). Enter on its own keeps the admin's current password.
if [ -n "${ADMIN_EMAIL:-}" ] && [ -z "${ADMIN_PASSWORD:-}" ] && [ -t 0 ]; then
  read -r -s -p "Password for the admin account $ADMIN_EMAIL (8 characters or more; Enter keeps the current one): " ADMIN_PASSWORD
  echo
fi
AUTH=${AUTH:-}             # off: no sign-in for a test window; required: always; empty: on for the public site

gcloud config set project "$PROJECT_ID" >/dev/null
NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
API_URL="https://afterledger-api-${NUMBER}.${REGION}.run.app"      # Cloud Run's deterministic URLs
WEB_URL="https://afterledger-web-${NUMBER}.${REGION}.run.app"
FIREBASE_SITE=$(python3 -c 'import json; print(json.load(open("firebase.json"))["hosting"].get("site", ""))' 2>/dev/null || true)
FIREBASE_URL="https://${FIREBASE_SITE:-$PROJECT_ID}.web.app"          # Firebase Hosting's address for the web app
PUBLIC_URL=$WEB_URL                                                  # the address share links and letters use
[ "$FIREBASE" = "no" ] || PUBLIC_URL=$FIREBASE_URL
SA="afterledger-api@${PROJECT_ID}.iam.gserviceaccount.com"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/afterledger"

say "Turning on the Google Cloud services AfterLedger uses"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  firestore.googleapis.com storage.googleapis.com bigquery.googleapis.com cloudtasks.googleapis.com \
  documentai.googleapis.com translate.googleapis.com discoveryengine.googleapis.com aiplatform.googleapis.com \
  iam.googleapis.com modelarmor.googleapis.com cloudtrace.googleapis.com telemetry.googleapis.com \
  firebasehosting.googleapis.com

say "Artifact Registry"
gcloud artifacts repositories describe afterledger --location "$REGION" >/dev/null 2>&1 ||
  gcloud artifacts repositories create afterledger --repository-format docker --location "$REGION"

say "Firestore"
gcloud firestore databases describe --database='(default)' >/dev/null 2>&1 ||
  gcloud firestore databases create --location "$REGION" --type firestore-native

say "Cloud Storage"
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 ||
  gcloud storage buckets create "gs://$BUCKET" --location "$REGION" --uniform-bucket-level-access \
    --public-access-prevention

say "BigQuery (traces)"
bq --location "$REGION" show "$PROJECT_ID:afterledger" >/dev/null 2>&1 ||
  bq --location "$REGION" mk --dataset "$PROJECT_ID:afterledger"
bq show "$PROJECT_ID:afterledger.traces" >/dev/null 2>&1 ||
  bq mk --table --time_partitioning_field at --time_partitioning_type DAY "$PROJECT_ID:afterledger.traces" \
    at:TIMESTAMP,type:STRING,agent:STRING,case_id:STRING,ms:INT64,model:STRING,payload:JSON,tokens_in:INT64,tokens_out:INT64,tokens_thinking:INT64,cost_usd:FLOAT64
# A table made by an earlier deployment gets the usage columns (tokens and cost of each model call)
SCHEMA=$(mktemp)
bq show --schema --format=json "$PROJECT_ID:afterledger.traces" > "$SCHEMA"
if python3 - "$SCHEMA" <<'PY'
import json, sys
path = sys.argv[1]
schema = json.load(open(path))
have = {f["name"] for f in schema}
new = [{"name": n, "type": t, "mode": "NULLABLE"} for n, t in (("tokens_in", "INTEGER"), ("tokens_out", "INTEGER"),
       ("tokens_thinking", "INTEGER"), ("cost_usd", "FLOAT")) if n not in have]
json.dump(schema + new, open(path, "w"))
sys.exit(0 if new else 1)
PY
then
  bq update "$PROJECT_ID:afterledger.traces" "$SCHEMA" >/dev/null
fi
rm -f "$SCHEMA"

say "Cloud Tasks (background work)"
gcloud tasks queues describe afterledger-work --location "$REGION" >/dev/null 2>&1 ||
  gcloud tasks queues create afterledger-work --location "$REGION" --max-attempts 3 --min-backoff 10s \
    --max-backoff 120s --max-concurrent-dispatches 4 --max-dispatches-per-second 5

say "Document AI OCR processor"
DOCAI="https://${DOCAI_LOCATION}-documentai.googleapis.com/v1/projects/${PROJECT_ID}/locations/${DOCAI_LOCATION}/processors"
TOKEN=$(gcloud auth print-access-token)
PROCESSOR=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$DOCAI" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print(next((p["name"] for p in d.get("processors", []) if p.get("displayName") == "afterledger-ocr"), ""))')
if [ -z "$PROCESSOR" ]; then
  PROCESSOR=$(curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"type": "OCR_PROCESSOR", "displayName": "afterledger-ocr"}' "$DOCAI" |
    python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')
fi
echo "Processor: $PROCESSOR"

say "Model Armor template (screens fetched pages, uploaded papers and replies)"
MA="https://modelarmor.${MODEL_ARMOR_LOCATION}.rep.googleapis.com/v1/projects/${PROJECT_ID}/locations/${MODEL_ARMOR_LOCATION}/templates"
TEMPLATE="projects/${PROJECT_ID}/locations/${MODEL_ARMOR_LOCATION}/templates/afterledger-screen"
if ! curl -fsS -o /dev/null -H "Authorization: Bearer $TOKEN" "$MA/afterledger-screen"; then
  curl -fsS -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -o /dev/null \
    -d '{"filterConfig": {"piAndJailbreakFilterSettings": {"filterEnforcement": "ENABLED",
         "confidenceLevel": "MEDIUM_AND_ABOVE"}, "maliciousUriFilterSettings": {"filterEnforcement": "ENABLED"}},
         "templateMetadata": {"multiLanguageDetection": {"enableMultiLanguageDetection": true}}}' \
    "$MA?templateId=afterledger-screen"
fi
echo "Template: $TEMPLATE"

say "The API's service account"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create afterledger-api --display-name "AfterLedger API"
# discoveryengine.viewer carries groundingConfigs.check (check grounding), which discoveryengine.user does not
for role in roles/datastore.user roles/bigquery.dataEditor roles/bigquery.jobUser roles/cloudtasks.enqueuer \
            roles/documentai.apiUser roles/cloudtranslate.user roles/discoveryengine.user \
            roles/discoveryengine.viewer roles/aiplatform.user roles/modelarmor.user roles/cloudtrace.agent \
            roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$SA" --role "$role" \
    --condition None >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" \
  --role roles/storage.objectAdmin >/dev/null

say "The build's service account"
# Projects created since 2024 have no default Cloud Build account (an organisation policy may forbid it), so the
# build runs as its own account, with only what it needs: read the uploaded source, push images, write logs.
BUILD_SA="afterledger-build@${PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$BUILD_SA" >/dev/null 2>&1 ||
  gcloud iam service-accounts create afterledger-build --display-name "AfterLedger build"
for role in roles/cloudbuild.builds.builder roles/artifactregistry.writer roles/logging.logWriter \
            roles/storage.objectViewer; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$BUILD_SA" --role "$role" \
    --condition None >/dev/null
done

say "Building the images with Cloud Build (about ten minutes)"
gcloud builds submit --config infra/cloudbuild.yaml --substitutions "_REGION=$REGION,_TAG=$TAG" \
  --service-account "projects/$PROJECT_ID/serviceAccounts/$BUILD_SA" .

LLM=scripted
TRACE_TO_CLOUD=false
if [ "$MODE" = "live" ]; then LLM=gemini; TRACE_TO_CLOUD=true; fi
# Values the API already holds are kept across redeploys: the internal token (tasks already queued are still
# accepted), the sign-in key (people stay signed in), and the admin account unless ADMIN_* give new details.
EXISTING=$(gcloud run services describe afterledger-api --region "$REGION" --format=json 2>/dev/null || echo '{}')
current() {      # the API's current value of one environment variable, or nothing
  printf '%s' "$EXISTING" | python3 -c '
import json, sys
try:
    env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
    print(next((e.get("value", "") for e in env if e["name"] == sys.argv[1]), ""))
except Exception:
    print("")' "$1"
}
quoted() {       # a value written safely into the YAML file of environment variables
  python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}
INTERNAL_TOKEN=$(current AL_INTERNAL_TOKEN)
[ -n "$INTERNAL_TOKEN" ] || INTERNAL_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(30))')
AUTH_SECRET=$(current AL_AUTH_SECRET)
[ -n "$AUTH_SECRET" ] || AUTH_SECRET=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
ADMIN_EMAIL=${ADMIN_EMAIL:-$(current AL_ADMIN_EMAIL)}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-$(current AL_ADMIN_PASSWORD)}
ADMIN_NAME=${ADMIN_NAME:-$(current AL_ADMIN_NAME)}

say "Deploying the web app"
gcloud run deploy afterledger-web --image "$REGISTRY/web:$TAG" --region "$REGION" --allow-unauthenticated \
  --min-instances "$WEB_MIN_INSTANCES" --max-instances 3 --cpu 1 --memory 1Gi --cpu-boost \
  --set-env-vars "AL_API_BASE=$API_URL,AL_SITE=$SITE,AL_AUTH=${AUTH:-auto}"

# The API accepts requests from every address the web app has (its deterministic URL, its other run.app URL, and
# Firebase Hosting's two addresses).
WEB_URLS=$(gcloud run services describe afterledger-web --region "$REGION" --format=json | python3 -c '
import json, sys
d = json.load(sys.stdin)
urls = json.loads((d.get("metadata", {}).get("annotations") or {}).get("run.googleapis.com/urls") or "[]")
urls += [sys.argv[1], d.get("status", {}).get("url", "")] + sys.argv[2:]
print(",".join(dict.fromkeys(u for u in urls if u)))' "$WEB_URL" "$FIREBASE_URL" "${FIREBASE_URL%.web.app}.firebaseapp.com")

say "Deploying the API"
ENV_FILE=$(mktemp)
trap 'rm -f "$ENV_FILE"' EXIT
cat > "$ENV_FILE" <<YAML
AL_MODE: "$MODE"
AL_LLM: "$LLM"
AL_STORE: "gcp"
GOOGLE_GENAI_USE_ENTERPRISE: "true"
GOOGLE_CLOUD_PROJECT: "$PROJECT_ID"
GOOGLE_CLOUD_LOCATION: "global"
AL_LIVE_LOCATION: "$LIVE_LOCATION"
AL_GCS_BUCKET: "$BUCKET"
AL_BQ_DATASET: "afterledger"
AL_TASKS_QUEUE: "projects/$PROJECT_ID/locations/$REGION/queues/afterledger-work"
AL_API_URL: "$API_URL"
AL_INTERNAL_TOKEN: "$INTERNAL_TOKEN"
AL_DOCAI_LOCATION: "$DOCAI_LOCATION"
AL_DOCAI_OCR_PROCESSOR: "$PROCESSOR"
AL_CORS_ORIGINS: "$WEB_URLS"
AL_PUBLIC_WEB_URL: "$PUBLIC_URL"
AL_TIMEZONE: "$TIMEZONE"
AL_MODEL_ARMOR_TEMPLATE: "$TEMPLATE"
AL_TRACE_TO_CLOUD: "$TRACE_TO_CLOUD"
AL_STRICT_LIVE: "false"
AL_SITE: "$SITE"
AL_DEMO_FAMILIES: "$DEMO_FAMILIES"
AL_LLM_TIER_LOCATIONS: "$LLM_TIER_LOCATIONS"
AL_LLM_HEDGE_INTERACTIVE_S: "5"
AL_AUTH_SECRET: $(quoted "$AUTH_SECRET")
AL_AUTH: "${AUTH:-auto}"
YAML
# The one admin account (made, or updated, when the API starts); never printed
if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
  {
    echo "AL_ADMIN_EMAIL: $(quoted "$ADMIN_EMAIL")"
    echo "AL_ADMIN_PASSWORD: $(quoted "$ADMIN_PASSWORD")"
    [ -z "$ADMIN_NAME" ] || echo "AL_ADMIN_NAME: $(quoted "$ADMIN_NAME")"
  } >> "$ENV_FILE"
else
  echo "No admin account: give ADMIN_EMAIL and ADMIN_PASSWORD (8 characters or more) to make one."
fi
# AL_STRICT_LIVE false: a Google service that fails is reported on /v1/meta and the work goes on without it
[ -z "$CASE_BUDGET_USD" ] || echo "AL_CASE_BUDGET_USD: \"$CASE_BUDGET_USD\"" >> "$ENV_FILE"
[ -z "${AGENT_TIERS:-}" ] || echo "AL_AGENT_TIERS: \"$AGENT_TIERS\"" >> "$ENV_FILE"
# --concurrency 500: every open case page holds one live update stream (AL_SSE_MAX_STREAMS, 300 by default, each
# closed by the service after 45 s), so plain requests keep at least 200 slots. With 80 slots, on 19 October 2026,
# about 80 case pages left open by one visitor filled the one instance for an hour.
gcloud run deploy afterledger-api --image "$REGISTRY/api:$TAG" --region "$REGION" --service-account "$SA" \
  --allow-unauthenticated --max-instances 1 --min-instances "$MIN_INSTANCES" --no-cpu-throttling \
  --cpu 2 --memory 2Gi --timeout 3600 --concurrency 500 --session-affinity --cpu-boost \
  --env-vars-file "$ENV_FILE"

if [ "$FIREBASE" != "no" ]; then
  if command -v firebase >/dev/null 2>&1 && firebase projects:list --json >/dev/null 2>&1; then
    say "Firebase Hosting (the public address, in front of the web app)"
    # firebase.json names the site "afterledger"; a project without it (no Hosting yet) gets it here
    SITE_ID=$(python3 -c 'import json; print(json.load(open("firebase.json"))["hosting"]["site"])')
    firebase hosting:sites:get "$SITE_ID" --project "$PROJECT_ID" >/dev/null 2>&1 ||
      firebase hosting:sites:create "$SITE_ID" --project "$PROJECT_ID" --non-interactive
    firebase deploy --only hosting --project "$PROJECT_ID" --non-interactive
  else
    say "Firebase Hosting skipped: run  firebase login --no-localhost  and then  firebase deploy --only hosting --project $PROJECT_ID"
  fi
fi

say "Done"
[ "$FIREBASE" = "no" ] || echo "Public address: $FIREBASE_URL"
echo "Web app: $WEB_URL"
echo "API:     $API_URL   (health: $API_URL/v1/health, what is live: $API_URL/v1/meta)"
