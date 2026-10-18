#!/usr/bin/env bash
# Clears the deployed site before a demonstration or judging: removes every case people started (with its papers,
# page images and filled forms) and forgets the country questions they asked. The sample families stay as they
# are; RESET_SAMPLES=1 also puts each back as the image built it (as the Reset button on a sample case does). What
# research learned about institutions' rules stays, with its sources. Run it in Cloud Shell from the repository root:
#
#   PROJECT_ID=my-project bash infra/scripts/clean-site.sh
#
# Optional: REGION (asia-south1), RESET_SAMPLES=1. It asks before deleting; set YES=1 to skip the question.
set -euo pipefail

PROJECT_ID=${PROJECT_ID:?Set PROJECT_ID to your Google Cloud project id}
REGION=${REGION:-asia-south1}

gcloud config set project "$PROJECT_ID" >/dev/null
SERVICE=$(gcloud run services describe afterledger-api --region "$REGION" --format=json)
API_URL=$(printf '%s' "$SERVICE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"]["url"])')
# The internal token the API already holds; it is read here and never printed.
TOKEN=$(printf '%s' "$SERVICE" | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
print(next((e["value"] for e in env if e["name"] == "AL_INTERNAL_TOKEN"), ""))')
[ -n "$TOKEN" ] || { echo "The API has no internal token; deploy it with infra/scripts/deploy.sh first." >&2; exit 1; }

if [ "${YES:-}" != "1" ]; then
  read -r -p "Delete every case people started on $API_URL and reset the sample families? [y/N] " answer
  case "$answer" in [yY]*) ;; *) echo "Nothing changed."; exit 0 ;; esac
fi

QUERY=""
[ "${RESET_SAMPLES:-}" != "1" ] || QUERY="?samples=reset"
curl -fsS -X POST -H "X-AfterLedger-Token: $TOKEN" --max-time 600 "$API_URL/internal/clean$QUERY" | python3 -c '
import json, sys
out = json.load(sys.stdin)
print("Cases removed:", out["cases_removed"])
print("Questions forgotten:", out["questions_removed"])
for key, state in out["samples_reset"].items():
    print("Sample family " + key + " reset:", state)'
echo "Done. Visitors who started a case still have its address in their own browser, but the case is gone."
