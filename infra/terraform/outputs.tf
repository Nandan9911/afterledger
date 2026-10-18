output "web_url" {
  description = "Open this: the web app's deterministic URL (the one the API accepts requests from)."
  value       = local.web_url
}

output "api_url" {
  description = "The API (health: /v1/health; what is live: /v1/meta)."
  value       = local.api_url
}

output "web_url_also" {
  description = "The web app's other run.app address, which the API accepts as well."
  value       = google_cloud_run_v2_service.web.uri
}

output "bucket" {
  value = google_storage_bucket.files.name
}

output "docai_processor" {
  value = google_document_ai_processor.ocr.name
}

output "model_armor_template" {
  value = google_model_armor_template.screen.id
}
