# Infrastructure

How AfterLedger runs on Google Cloud. The step-by-step guide, with checks and troubleshooting, is
[docs/runbooks/deploy-gcp.md](../docs/runbooks/deploy-gcp.md).

```
cloudbuild.yaml          builds the API and web images in Cloud Build and pushes them to Artifact Registry
scripts/deploy.sh        the whole deployment with gcloud, bq and curl (run it in Cloud Shell); safe to run again
terraform/               the same resources as code (APIs, registry, Firestore, bucket, BigQuery, Cloud Tasks,
                         Document AI processor, service account and roles, the two Cloud Run services)
```

The images are built from the repository root: `services/api/Dockerfile` (it builds the six demo cases while the
image is built) and `apps/web/Dockerfile` (a standalone Next.js server that reads the API's address when a page is
served, so one image runs anywhere).

| Cloud Run service | Instances | Size | Why |
|---|---|---|---|
| `afterledger-api` | at most 1, CPU always allocated, 60-minute requests | 2 vCPU, 2 GiB | case locks and write-through caches live in memory (ADR 0006); background work and event streams run between requests |
| `afterledger-web` | 0 to 3 | 1 vCPU, 1 GiB | serves pages only; it holds no state |
