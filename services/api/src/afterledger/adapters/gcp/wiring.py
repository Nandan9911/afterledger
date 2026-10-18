"""Swaps in the Google Cloud adapters the settings ask for, and records which services are live.

    AL_STORE=gcp      Firestore (cases, researched knowledge), Cloud Storage (blobs), BigQuery (traces) and,
                      with AL_TASKS_QUEUE, Cloud Tasks (background work)
    AL_MODE=live      Document AI (AL_USE_DOCUMENT_AI + AL_DOCAI_OCR_PROCESSOR), Cloud Translation
                      (AL_USE_TRANSLATION), check grounding (AL_USE_CHECK_GROUNDING), the live web
                      (AL_USE_LIVE_WEB; search through Gemini when AL_LLM=gemini) and Agent Search
                      (AL_USE_AGENT_SEARCH + AL_SEARCH_DATASTORE)

Anything not configured keeps its local adapter. deps.extras["services"] says which is which, and the app shows
it, so nothing offline is ever presented as live.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    pass


def institution_domains(knowledge_dir: Path) -> set[str]:
    """Institutions' own web domains (real ones only), so their pages count as the institution's own word."""
    f = knowledge_dir / "institutions.yaml"
    if not f.exists():
        return set()
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    rows = data.get("institutions", data) if isinstance(data, dict) else data
    out = set()
    for r in rows or []:
        d = str(r.get("domain") or "").lower().removeprefix("www.")
        if d and not d.endswith(".example") and r.get("fictional") is not True:
            out.add(d)
    return out


def apply_gcp_adapters(deps: Any) -> None:
    from ...health import health_of
    s = deps.settings
    health = health_of(deps)
    services: dict[str, str] = deps.extras.setdefault("services", {})
    domains = institution_domains(s.knowledge_dir)

    if s.store == "gcp":
        if not s.project or not s.gcs_bucket:
            raise ConfigError("AL_STORE=gcp needs GOOGLE_CLOUD_PROJECT and AL_GCS_BUCKET")
        from .firestore import FirestoreCaseStore, FirestoreKnowledgeStore
        from .gcs import GcsBlobStore
        deps.cases = FirestoreCaseStore(s.project, s.firestore_database)
        deps.knowledge = FirestoreKnowledgeStore(s.knowledge_dir, s.state_dir, s.project, s.firestore_database)
        deps.blobs = GcsBlobStore(s.gcs_bucket, s.data_dir)
        from .users import FirestoreUserStore
        deps.users = FirestoreUserStore(s.project, s.firestore_database)
        services.update(store="firestore", blobs="cloud_storage")
        if s.bq_dataset:
            from .bigquery import BigQueryTrace
            deps.trace = BigQueryTrace(s.project, s.bq_dataset, health=health)
            services["trace"] = "bigquery"
        if s.tasks_queue:
            if not (s.api_url and s.internal_token):
                raise ConfigError("AL_TASKS_QUEUE needs AL_API_URL and AL_INTERNAL_TOKEN")
            from .tasks import CloudTasksQueue
            deps.tasks = CloudTasksQueue(s.tasks_queue, s.api_url, s.internal_token)
            services["tasks"] = "cloud_tasks"

    if s.live:
        if s.use_document_ai and s.docai_ocr_processor and s.project:
            from .documentai import DocumentAiOcr
            deps.ocr = DocumentAiOcr(s.project, s.docai_location, s.docai_ocr_processor, fallback=deps.ocr, health=health)
            services["ocr"] = "document_ai"
        if s.use_translation and s.project:
            from .translation import CloudTranslator
            deps.translator = CloudTranslator(s.project, s.translate_location, reviewed=deps.translator, health=health)
            services["translation"] = "cloud_translation"
        if s.use_check_grounding and s.project:
            from .grounding import CheckGrounding
            deps.grounding = CheckGrounding(s.project, fallback=deps.grounding, health=health)
            services["grounding"] = "check_grounding"
        if s.use_live_web:
            from .web import GeminiSearch, LivePages
            deps.pages = LivePages(snapshots=deps.pages, institution_domains=domains)
            services["pages"] = "live_web"
            if s.llm == "gemini":
                deps.search = GeminiSearch(s.model_lite, institution_domains=domains, health=health)
                services["search"] = "google_search"
        if s.screen_engine == "model_armor" and s.model_armor_template:
            from .modelarmor import ModelArmorScreen
            deps.screen = ModelArmorScreen(s.model_armor_template, fallback=deps.screen, health=health)
            services["screen"] = "model_armor"
        if s.use_agent_search and s.search_datastore and s.project:
            from .web import AgentSearch
            deps.search = AgentSearch(s.project, s.search_location, s.search_datastore, fallback=deps.search,
                                      institution_domains=domains, health=health)
            services["search"] = "agent_search"
    if s.live and s.llm_record_resume and s.llm_record_dir:
        # a run made in pieces sees the same search results and pages in every piece
        from ..recorded import RecordedPages, RecordedSearch
        deps.search = RecordedSearch(deps.search, s.llm_record_dir)
        deps.pages = RecordedPages(deps.pages, s.llm_record_dir)
    log.info("services: %s", services)
