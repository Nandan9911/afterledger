# AfterLedger on Google Cloud: the API and the web app on Cloud Run, Firestore, Cloud Storage, BigQuery,
# Cloud Tasks, Document AI and a Model Armor template, with one service account for the API.
# See docs/runbooks/deploy-gcp.md.

data "google_project" "this" {
  project_id = var.project_id
}

locals {
  services = [
    "run.googleapis.com", "artifactregistry.googleapis.com", "cloudbuild.googleapis.com",
    "firestore.googleapis.com", "storage.googleapis.com", "bigquery.googleapis.com",
    "cloudtasks.googleapis.com", "documentai.googleapis.com", "translate.googleapis.com",
    "discoveryengine.googleapis.com", "aiplatform.googleapis.com", "iam.googleapis.com",
    "modelarmor.googleapis.com", "cloudtrace.googleapis.com", "telemetry.googleapis.com",
  ]
  registry = "${var.region}-docker.pkg.dev/${var.project_id}/afterledger"
  # Cloud Run's deterministic service URLs, known before the services exist
  api_url = "https://afterledger-api-${data.google_project.this.number}.${var.region}.run.app"
  web_url = "https://afterledger-web-${data.google_project.this.number}.${var.region}.run.app"
  live    = var.mode == "live"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "afterledger"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_firestore_database" "default" {
  count           = var.create_firestore_database ? 1 : 0
  name            = "(default)"
  location_id     = var.region
  type            = "FIRESTORE_NATIVE"
  deletion_policy = "DELETE" # terraform destroy removes it (the provider's default only forgets it)
  depends_on      = [google_project_service.apis]
}

resource "random_id" "bucket" {
  byte_length = 4
}

resource "google_storage_bucket" "files" {
  name                        = "afterledger-${var.project_id}-${random_id.bucket.hex}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true
  depends_on                  = [google_project_service.apis]
}

resource "google_bigquery_dataset" "traces" {
  dataset_id                 = "afterledger"
  location                   = var.region
  delete_contents_on_destroy = true
  depends_on                 = [google_project_service.apis]
}

resource "google_bigquery_table" "traces" {
  dataset_id          = google_bigquery_dataset.traces.dataset_id
  table_id            = "traces"
  deletion_protection = false
  time_partitioning {
    type  = "DAY"
    field = "at"
  }
  schema = jsonencode([
    { name = "at", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "type", type = "STRING", mode = "NULLABLE" },
    { name = "agent", type = "STRING", mode = "NULLABLE" },
    { name = "case_id", type = "STRING", mode = "NULLABLE" },
    { name = "ms", type = "INT64", mode = "NULLABLE" },
    { name = "model", type = "STRING", mode = "NULLABLE" },
    { name = "payload", type = "JSON", mode = "NULLABLE" },
    # model calls: tokens and what they cost (prices in data/knowledge/prices.yaml)
    { name = "tokens_in", type = "INT64", mode = "NULLABLE" },
    { name = "tokens_out", type = "INT64", mode = "NULLABLE" },
    { name = "tokens_thinking", type = "INT64", mode = "NULLABLE" },
    { name = "cost_usd", type = "FLOAT64", mode = "NULLABLE" },
  ])
}

resource "google_cloud_tasks_queue" "work" {
  name     = "afterledger-work"
  location = var.region
  rate_limits {
    max_concurrent_dispatches = 32   # a task waits only for its own case's work (infra/scripts/deploy.sh)
    max_dispatches_per_second = 10
  }
  retry_config {
    max_attempts  = 3
    min_backoff   = "10s"
    max_backoff   = "120s"
    max_doublings = 3
  }
  depends_on = [google_project_service.apis]
}

resource "google_document_ai_processor" "ocr" {
  location     = var.docai_location
  display_name = "afterledger-ocr"
  type         = "OCR_PROCESSOR"
  depends_on   = [google_project_service.apis]
}

# Model Armor screens outside text (fetched pages, uploaded papers, institutions' replies) for planted instructions
# and malicious links. Its region must offer both filters with multi-language detection: us-central1 does;
# asia-south1 offers neither (October 2026).
resource "google_model_armor_template" "screen" {
  location    = var.model_armor_location
  template_id = "afterledger-screen"
  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
  }
  template_metadata {
    multi_language_detection {
      enable_multi_language_detection = true
    }
  }
  depends_on = [google_project_service.apis]
}

resource "random_password" "internal_token" {
  length  = 40
  special = false
}

# ---- the API's identity --------------------------------------------------------------------------------
resource "google_service_account" "api" {
  account_id   = "afterledger-api"
  display_name = "AfterLedger API"
}

locals {
  api_roles = [
    "roles/datastore.user",      # Firestore
    "roles/bigquery.dataEditor", # trace rows
    "roles/bigquery.jobUser",
    "roles/cloudtasks.enqueuer",    # background work
    "roles/documentai.apiUser",     # OCR
    "roles/cloudtranslate.user",    # translation
    "roles/discoveryengine.user",   # Agent Search
    "roles/discoveryengine.viewer", # check grounding (groundingConfigs.check is in viewer, not user)
    "roles/aiplatform.user",        # Gemini on Agent Platform
    "roles/modelarmor.user",        # screening outside text
    "roles/cloudtrace.agent",       # spans to Cloud Trace (Telemetry API)
    "roles/logging.logWriter",
  ]
}

resource "google_project_iam_member" "api" {
  for_each = toset(local.api_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.api.email}"
}

resource "google_storage_bucket_iam_member" "api_files" {
  bucket = google_storage_bucket.files.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.api.email}"
}

# ---- Cloud Run ------------------------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name                = "afterledger-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.api.email
    timeout                          = "3600s" # event streams, voice and long background work
    # every open case page holds one live update stream (AL_SSE_MAX_STREAMS, 300 by default), so plain requests
    # keep at least 200 slots; with 80, a visitor's open pages once filled the one instance
    max_instance_request_concurrency = 500
    session_affinity                 = true
    scaling {
      min_instance_count = var.keep_one_instance_warm ? 1 : 0
      max_instance_count = 1 # write-through caches and case locks live in memory (ADR 0006)
    }
    containers {
      image = "${local.registry}/api:${var.image_tag}"
      resources {
        limits            = { cpu = "2", memory = "2Gi" }
        cpu_idle          = false # CPU always allocated: background work continues between requests
        startup_cpu_boost = true
      }
      startup_probe {
        http_get {
          path = "/v1/health"
        }
        initial_delay_seconds = 2
        period_seconds        = 5
        failure_threshold     = 24
      }
      dynamic "env" {
        for_each = merge({
          AL_MODE                     = var.mode
          AL_LLM                      = local.live ? "gemini" : "scripted"
          AL_STORE                    = "gcp"
          GOOGLE_GENAI_USE_ENTERPRISE = "true"
          GOOGLE_CLOUD_PROJECT        = var.project_id
          GOOGLE_CLOUD_LOCATION       = "global"
          AL_LIVE_LOCATION            = var.live_location
          AL_GCS_BUCKET               = google_storage_bucket.files.name
          AL_BQ_DATASET               = google_bigquery_dataset.traces.dataset_id
          AL_TASKS_QUEUE              = google_cloud_tasks_queue.work.id
          AL_API_URL                  = local.api_url
          AL_INTERNAL_TOKEN           = random_password.internal_token.result
          AL_DOCAI_LOCATION           = var.docai_location
          AL_DOCAI_OCR_PROCESSOR      = google_document_ai_processor.ocr.name
          AL_CORS_ORIGINS             = "${local.web_url},${google_cloud_run_v2_service.web.uri}"
          AL_PUBLIC_WEB_URL           = local.web_url
          AL_TIMEZONE                 = var.timezone
          AL_MODEL_ARMOR_TEMPLATE     = "projects/${var.project_id}/locations/${var.model_armor_location}/templates/${google_model_armor_template.screen.template_id}"
          AL_TRACE_TO_CLOUD           = local.live ? "true" : "false"
          AL_STRICT_LIVE              = "false" # a service that fails is reported on /v1/meta, and the work goes on
        }, var.case_budget_usd == null ? {} : { AL_CASE_BUDGET_USD = tostring(var.case_budget_usd) })
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
  depends_on = [google_project_iam_member.api, google_storage_bucket_iam_member.api_files, google_firestore_database.default]
}

resource "google_cloud_run_v2_service" "web" {
  name                = "afterledger-web"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    scaling {
      min_instance_count = 0
      max_instance_count = 3
    }
    containers {
      image = "${local.registry}/web:${var.image_tag}"
      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }
      env {
        name  = "AL_API_BASE"
        value = local.api_url
      }
    }
  }
  depends_on = [google_project_service.apis]
}

# Both services are public: the prototype has no sign-in (security is out of its scope).
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "web_public" {
  name     = google_cloud_run_v2_service.web.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
