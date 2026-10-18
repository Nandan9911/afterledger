"""Settings, read from the environment (and a .env file at the repository root)."""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "data" / "knowledge").exists() or (parent / "pnpm-workspace.yaml").exists():
            return parent
    return Path.cwd()


REPO_ROOT = _repo_root()


class Settings(BaseSettings):
    # An empty value (AL_STORY_DAY= in .env) means "not set", so every setting keeps its default.
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), ".env"), env_file_encoding="utf-8", extra="ignore",
        populate_by_name=True, env_ignore_empty=True)

    mode: Literal["offline", "live"] = Field("offline", alias="AL_MODE")
    data_dir: Path = Field(REPO_ROOT / "data", alias="AL_DATA_DIR")
    state_dir: Path = Field(REPO_ROOT / ".data", alias="AL_STATE_DIR")
    store: Literal["local", "gcp"] = Field("local", alias="AL_STORE")
    llm: Literal["scripted", "gemini", "replay"] = Field("scripted", alias="AL_LLM")
    llm_record_dir: Path | None = Field(None, alias="AL_LLM_RECORD_DIR")
    llm_replay_dir: Path | None = Field(None, alias="AL_LLM_REPLAY_DIR")
    # A pure replay: a call with no cassette is an error instead of a scripted answer (tests of a recorded run)
    llm_replay_strict: bool = Field(False, alias="AL_LLM_REPLAY_STRICT")
    # A recording run that was cut off is run again: requests it already recorded are answered from their cassettes
    llm_record_resume: bool = Field(False, alias="AL_LLM_RECORD_RESUME")
    max_tool_calls: int = Field(16, alias="AL_MAX_TOOL_CALLS")      # one agent's tool calls in one turn; 0: no limit
    max_llm_calls: int = Field(400, alias="AL_MAX_LLM_CALLS")
    story_day: date | None = Field(None, alias="AL_STORY_DAY")
    seed_on_start: bool = Field(True, alias="AL_SEED_ON_START")
    # The sample families this copy shows, by key ("verma,nair,sato"); empty: all of them. The evaluation always
    # uses every family.
    demo_families: str = Field("", alias="AL_DEMO_FAMILIES")
    # "product": the hosted product, with no evaluation runs and no replays from the first paper; anything else: the
    # working copy, with everything.
    site: str = Field("", alias="AL_SITE")
    demo_bundles: Path | None = Field(None, alias="AL_DEMO_BUNDLES")   # demo cases built offline (afterledger build-demos)
    clock: str = Field("system", alias="AL_CLOCK")
    timezone: str | None = Field(None, alias="AL_TIMEZONE")   # the clock new activity is written on (IANA name)
    cors_origins: str = Field("http://localhost:3000", alias="AL_CORS_ORIGINS")
    log_level: str = Field("INFO", alias="AL_LOG_LEVEL")
    public_web_url: str = Field("http://localhost:3000", alias="AL_PUBLIC_WEB_URL")

    project: str | None = Field(None, alias="GOOGLE_CLOUD_PROJECT")
    gcs_bucket: str | None = Field(None, alias="AL_GCS_BUCKET")
    firestore_database: str = Field("(default)", alias="AL_FIRESTORE_DATABASE")
    bq_dataset: str = Field("afterledger", alias="AL_BQ_DATASET")
    tasks_queue: str | None = Field(None, alias="AL_TASKS_QUEUE")
    docai_location: str = Field("us", alias="AL_DOCAI_LOCATION")
    docai_ocr_processor: str | None = Field(None, alias="AL_DOCAI_OCR_PROCESSOR")   # the processor's id
    search_location: str = Field("global", alias="AL_SEARCH_LOCATION")
    search_datastore: str | None = Field(None, alias="AL_SEARCH_DATASTORE")
    translate_location: str = Field("global", alias="AL_TRANSLATE_LOCATION")
    live_location: str = Field("us-central1", alias="AL_LIVE_LOCATION")   # the Live model is in us/eu regions only
    internal_token: str | None = Field(None, alias="AL_INTERNAL_TOKEN")
    # Accounts. "auto": sign-in is required on the public site (AL_SITE=product) and off on a working copy;
    # "required" or "off" say so outright. Where it is required, each case belongs to the account that started it.
    auth: Literal["auto", "required", "off"] = Field("auto", alias="AL_AUTH")
    auth_secret: str | None = Field(None, alias="AL_AUTH_SECRET")     # signs sign-ins; keep it across restarts
    admin_email: str | None = Field(None, alias="AL_ADMIN_EMAIL")     # the one admin account, made at start
    admin_password: str | None = Field(None, alias="AL_ADMIN_PASSWORD")
    admin_name: str = Field("Admin", alias="AL_ADMIN_NAME")
    api_url: str | None = Field(None, alias="AL_API_URL")   # this service's own address, for Cloud Tasks

    model_flash: str = Field("gemini-3.8-flash", alias="AL_MODEL_FLASH")
    model_pro: str = Field("gemini-3.1-pro-preview", alias="AL_MODEL_PRO")
    model_lite: str = Field("gemini-3.5-flash-lite", alias="AL_MODEL_LITE")
    model_live: str = Field("gemini-3.8-live", alias="AL_MODEL_LIVE")
    # Models that answer the flash agents as well as AL_MODEL_FLASH, tried (after its backup endpoint) when it is slow
    # or overloaded, before any smaller model; comma-separated, empty for none. gemini-3.6-flash costs the same as
    # gemini-3.8-flash and is retired on the Agent Platform on 19 November 2026.
    model_flash_peers: str = Field("gemini-3.6-flash", alias="AL_MODEL_FLASH_PEERS")

    use_document_ai: bool = Field(True, alias="AL_USE_DOCUMENT_AI")
    use_translation: bool = Field(True, alias="AL_USE_TRANSLATION")
    use_check_grounding: bool = Field(True, alias="AL_USE_CHECK_GROUNDING")
    use_agent_search: bool = Field(False, alias="AL_USE_AGENT_SEARCH")
    use_live_web: bool = Field(True, alias="AL_USE_LIVE_WEB")
    use_live_voice: bool = Field(True, alias="AL_USE_LIVE_VOICE")

    # ---- the agent harness (each one value away from the behaviour before 15 October) ----
    # A live Google Cloud service that fails stops the run (strict) instead of quietly falling back offline.
    strict_live: bool = Field(False, alias="AL_STRICT_LIVE")
    reask_malformed: int = Field(1, alias="AL_REASK_MALFORMED")     # 1: ask once more after an unusable answer
    fallback: bool = Field(True, alias="AL_FALLBACK")                 # pro to flash to lite on quota or overload
    # A safety net for a stalled call, not a budget: reading a long statement from its image takes minutes on a live
    # model. Pro, tool-using and image-reading agents get twice this.
    agent_timeout_s: float = Field(300, alias="AL_AGENT_TIMEOUT_S")
    # Seconds one live model request may wait for its answer (twice this for pro and tool-using agents) before it
    # is given up and tried again; below the agent's timeout, so a hung connection costs one retry, not the step.
    llm_http_timeout_s: float = Field(90, alias="AL_LLM_HTTP_TIMEOUT_S")
    # A model that is slow or overloaded on its main endpoint (GOOGLE_CLOUD_LOCATION, normally global) is tried on
    # this multi-region one (us or eu; empty: none) before anything else. It is the same model, so the answer is no
    # worse; non-global endpoints cost about a tenth more per token. Preview models served only on global skip it.
    llm_backup_location: str = Field("us", alias="AL_LLM_BACKUP_LOCATION")
    # Where each tier's model is asked first, when not the main endpoint: "lite=asia-south1,flash=us". Measured from
    # India on 18 October 2026: Flash-Lite answered in 0.4-1 s in asia-south1 (0.9-1.3 s on global), Gemini 3.8 Flash
    # in 2.6-3 s on the us endpoint and 5-66 s on global. The main endpoint stays the backup. Empty: main endpoint.
    llm_tier_locations: str = Field("", alias="AL_LLM_TIER_LOCATIONS")
    # an agent's model tier, over its spec's: "gap_planner=flash,situation_extractor=flash" (lite, flash or pro)
    agent_tiers: str = Field("", alias="AL_AGENT_TIERS")
    # How long a slow endpoint is passed over before it is tried again: twice as long each further time in a row.
    llm_slow_cooldown_s: float = Field(300, alias="AL_LLM_SLOW_COOLDOWN_S")
    # Seconds without an answer before the same request also goes to the backup endpoint (twice this for pro,
    # tool-using and image-reading agents); the first answer wins and the other request is cancelled. 0: off.
    llm_hedge_after_s: float = Field(20, alias="AL_LLM_HEDGE_AFTER_S")
    # The same for the agents a person waits on as they type or speak (intake and answering questions): sooner, as
    # a spoken answer that takes a minute is no answer. 0: as AL_LLM_HEDGE_AFTER_S.
    llm_hedge_interactive_s: float = Field(8, alias="AL_LLM_HEDGE_INTERACTIVE_S")
    # Seconds to wait before trying an overloaded model once more, before a smaller one (0: move down at once)
    llm_overload_pause_s: float = Field(8, alias="AL_LLM_OVERLOAD_PAUSE_S")
    case_budget_usd: float | None = Field(None, alias="AL_CASE_BUDGET_USD")   # empty: no limit
    trace_to_cloud: bool = Field(False, alias="AL_TRACE_TO_CLOUD")     # the agent kit's spans to Cloud Trace
    # Screening of text from outside (pages, papers, replies): auto picks Model Armor live when a template is set.
    screen: Literal["auto", "patterns", "model_armor", "off"] = Field("auto", alias="AL_SCREEN")
    model_armor_template: str | None = Field(None, alias="AL_MODEL_ARMOR_TEMPLATE")  # projects/P/locations/L/templates/T
    repair_rounds: int = Field(2, alias="AL_REPAIR_ROUNDS")           # check, fix, check again; 0 turns it off
    decisions: Literal["controller", "rules"] = Field("controller", alias="AL_DECISIONS")
    research_mode: Literal["adaptive", "fixed"] = Field("adaptive", alias="AL_RESEARCH_MODE")
    research_max_rounds: int = Field(3, alias="AL_RESEARCH_MAX_ROUNDS")
    research_parallel: int = Field(4, alias="AL_RESEARCH_PARALLEL")

    @property
    def live(self) -> bool:
        return self.mode == "live"

    @property
    def live_voice(self) -> bool:
        """Spoken conversation through the Gemini Live API: a live run with Gemini only."""
        return self.live and self.llm == "gemini" and self.use_live_voice

    @property
    def product(self) -> bool:
        return (self.site or "").strip().lower() == "product"

    @property
    def auth_required(self) -> bool:
        """Each case belongs to an account, and starting one needs a sign-in (see AL_AUTH)."""
        return self.auth == "required" or (self.auth == "auto" and self.product)

    def shown_families(self, keys: list[str]) -> list[str]:
        """The sample families to show, in their own order (AL_DEMO_FAMILIES), else every family."""
        wanted = [k.strip() for k in (self.demo_families or "").split(",") if k.strip()]
        return [k for k in wanted if k in keys] if wanted else keys

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge"

    @property
    def families_dir(self) -> Path:
        return self.data_dir / "families"

    def agent_tier(self, agent: str, default: str) -> str:
        """The tier an agent runs on: its spec's, unless AL_AGENT_TIERS names another for it."""
        for part in (self.agent_tiers or "").split(","):
            name, _, tier = part.partition("=")
            if name.strip() == agent and tier.strip() in ("lite", "flash", "pro"):
                return tier.strip()
        return default

    def model_for(self, tier: str) -> str:
        return {"flash": self.model_flash, "pro": self.model_pro, "lite": self.model_lite}.get(
            tier, self.model_flash)

    def fallback_chain(self, tier: str) -> list[str]:
        """The models a call may fall back to, in order, when its own is out of quota or overloaded."""
        order = {"pro": [self.model_pro, self.model_flash, self.model_lite],
                 "flash": [self.model_flash, self.model_lite], "lite": [self.model_lite]}.get(tier, [self.model_flash])
        return list(dict.fromkeys(order)) if self.fallback else order[:1]

    @property
    def main_location(self) -> str:
        import os
        return os.environ.get("GOOGLE_CLOUD_LOCATION") or "global"

    def route_groups(self, tier: str) -> list[list[str]]:
        """For each model of the fallback chain, the routes that answer as well as it: the model on the main endpoint,
        on the backup one, then its peers ("gemini-3.8-flash", "gemini-3.8-flash@us", "gemini-3.6-flash"). A route
        without "@" is the main endpoint. Without fallback, only the agent's own model on its main endpoint."""
        backup = (self.llm_backup_location or "").strip()
        peers = [m.strip() for m in (self.model_flash_peers or "").split(",") if m.strip()]
        main = self.main_location
        groups: list[list[str]] = []
        for model in self.fallback_chain(tier):
            first = self.tier_location(model)
            if first and first != main:        # its fastest endpoint first, the main one behind it
                group = [f"{model}@{first}"] + ([model] if self.fallback else [])
            else:
                group = [model]
                if self.fallback and backup and backup != main:
                    group.append(f"{model}@{backup}")
            if self.fallback and model == self.model_flash:
                for p in peers:
                    if p != model:
                        group.append(f"{p}@{first}" if first and first != main else p)
            groups.append(group)
        return groups

    def tier_location(self, model: str) -> str | None:
        """The endpoint a model is asked on first (AL_LLM_TIER_LOCATIONS), by the tier it serves."""
        pairs = dict(kv.split("=", 1) for kv in (self.llm_tier_locations or "").replace(" ", "").split(",")
                     if "=" in kv)
        if not pairs:
            return None
        peers = [m.strip() for m in (self.model_flash_peers or "").split(",") if m.strip()]
        tier = ("lite" if model == self.model_lite else "pro" if model == self.model_pro
                else "flash" if model == self.model_flash or model in peers else None)
        return (pairs.get(tier or "") or "").strip() or None

    def routes(self, tier: str) -> list[str]:
        return [r for g in self.route_groups(tier) for r in g]

    @property
    def screen_engine(self) -> str:
        if self.screen != "auto":
            return self.screen
        return "model_armor" if (self.live and self.model_armor_template) else "patterns"


# Read by the Google libraries themselves from the process environment, so a value in .env is passed on to them.
GOOGLE_ENV = ("GOOGLE_GENAI_USE_ENTERPRISE", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
              "GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")


def _export_google_env() -> None:
    import os

    from dotenv import dotenv_values
    for f in (REPO_ROOT / ".env", Path(".env")):
        if f.exists():
            for k, v in dotenv_values(f, encoding="utf-8").items():
                if k in GOOGLE_ENV and v and not os.environ.get(k):
                    if k == "GOOGLE_APPLICATION_CREDENTIALS" and not Path(v).is_absolute():
                        v = str((REPO_ROOT / v).resolve())   # a key path in .env is relative to the repository
                    os.environ[k] = v
    if os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE", "").lower() in ("1", "true", "yes") or \
            os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true", "yes"):
        # Gemini goes through Agent Platform under the project's credentials; an AI Studio key in the same .env would
        # otherwise compete with them, so it is left out of this process.
        for k in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
            os.environ.pop(k, None)


def resolve_paths(s: Settings) -> Settings:
    """Relative folders in .env are relative to the repository, wherever the service or a command was started from
    (tools/dev.py starts the API in services/api)."""
    for name in ("data_dir", "state_dir", "demo_bundles", "llm_record_dir", "llm_replay_dir"):
        path = getattr(s, name)
        if path is not None and not path.is_absolute():
            setattr(s, name, (REPO_ROOT / path).resolve())
    return s


@lru_cache
def get_settings() -> Settings:
    _export_google_env()
    return resolve_paths(Settings())
