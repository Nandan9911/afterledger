variable "project_id" {
  description = "The Google Cloud project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Firestore, Cloud Storage, BigQuery and Cloud Tasks."
  type        = string
  default     = "asia-south1"
}

variable "image_tag" {
  description = "Tag of the API and web images in Artifact Registry (built by infra/cloudbuild.yaml)."
  type        = string
  default     = "latest"
}

variable "create_firestore_database" {
  description = "Create the (default) Firestore database. Set false if the project already has one."
  type        = bool
  default     = true
}

variable "docai_location" {
  description = "Document AI location for the OCR processor (us or eu)."
  type        = string
  default     = "us"
}

variable "live_location" {
  description = "Region for the Gemini Live (voice) model on Agent Platform: us or eu regions only."
  type        = string
  default     = "us-central1"
}

variable "keep_one_instance_warm" {
  description = "Keep one API instance running (no cold starts during a demonstration; costs more)."
  type        = bool
  default     = false
}

variable "mode" {
  description = "live (Gemini and Google Cloud services) or offline (scripted responses, for a smoke test)."
  type        = string
  default     = "live"
}

variable "timezone" {
  description = "The clock new activity is written on (an IANA name); a demo family's story keeps its own."
  type        = string
  default     = "Asia/Kolkata"
}

variable "model_armor_location" {
  description = "Region of the Model Armor template: one that offers the prompt-injection and malicious-URI filters with multi-language detection (us-central1 does; asia-south1 does not)."
  type        = string
  default     = "us-central1"
}

variable "case_budget_usd" {
  description = "Most a case may spend on optional model work (extra research rounds and repairs), in US dollars. null: no limit. Required work is never stopped by it."
  type        = number
  default     = null
}
