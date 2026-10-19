"""Ports: what the application needs from the outside world. Adapters implement them for local
files (offline), for Google Cloud (live) and for recorded responses (replay)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from ..domain.case import CaseFile, OcrLine


class Clock(Protocol):
    def today(self) -> date: ...
    def now(self) -> datetime: ...


@runtime_checkable
class CaseStore(Protocol):
    async def put_case(self, case: CaseFile) -> None: ...
    async def get_case(self, case_id: str) -> CaseFile | None: ...
    async def list_cases(self) -> list[CaseFile]: ...
    async def delete_case(self, case_id: str) -> None: ...
    async def put(self, case_id: str, collection: str, record: BaseModel) -> None: ...
    async def put_many(self, case_id: str, collection: str, records: list[BaseModel]) -> None: ...
    async def get(self, case_id: str, collection: str, record_id: str) -> Any | None: ...
    async def list(self, case_id: str, collection: str) -> list[Any]: ...
    async def delete(self, case_id: str, collection: str, record_id: str) -> None: ...
    async def delete_many(self, case_id: str, collection: str, record_ids: list[str]) -> None: ...


class KnowledgeStore(Protocol):
    """Shared knowledge for all families: assertions, plans, institutions, forms, pages, profiles."""
    async def get(self, collection: str, record_id: str) -> dict | None: ...
    async def list(self, collection: str) -> list[dict]: ...
    async def put(self, collection: str, record_id: str, record: dict) -> None: ...


class BlobStore(Protocol):
    async def put(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    async def get(self, ref: str) -> bytes: ...
    async def exists(self, ref: str) -> bool: ...
    def content_type(self, ref: str) -> str: ...


Handler = Callable[[dict], Awaitable[None]]


class TaskQueue(Protocol):
    async def enqueue(self, kind: str, payload: dict, *, delay_seconds: int = 0) -> str: ...
    def register(self, kind: str, handler: Handler) -> None: ...
    async def drain(self) -> None: ...


class Ocr(Protocol):
    async def read_page(self, *, case_id: str, paper_id: str, page_id: str, image: bytes, mime: str,
                        pdf_page_text: list[dict] | None = None,
                        sidecar: list[dict] | None = None) -> list[OcrLine]: ...


class Translator(Protocol):
    async def translate(self, texts: list[str], target: str, source: str | None = None) -> list[str]: ...


class GroundingResult(BaseModel):
    support_score: float
    supported: bool
    claims: list[dict] = []
    method: str = "lexical"


class Grounding(Protocol):
    async def check(self, claim: str, facts: list[tuple[str, str]], threshold: float = 0.6) -> GroundingResult: ...


class SearchHit(BaseModel):
    url: str
    title: str = ""
    snippet: str = ""
    lang: str | None = None
    tier: int | None = None
    updated: str | None = None


class Search(Protocol):
    async def search(self, query: str, *, lang: str | None = None, sites: list[str] | None = None,
                     k: int = 8, country: str | None = None) -> list[SearchHit]: ...
    """country: ISO code of the country the research is about; a region hint for a live search, and a
    filter offline, where the page snapshots carry their jurisdiction."""


class PageSnapshot(BaseModel):
    url: str
    title: str = ""
    text: str = ""
    lang: str | None = None
    fetched_at: datetime | None = None
    content_hash: str = ""
    status: int = 200
    updated: str | None = None
    fictional: bool = False
    tier: int | None = None
    publisher: str = ""
    jurisdiction: str | None = None


class Pages(Protocol):
    async def fetch(self, url: str) -> PageSnapshot | None: ...


class Trace(Protocol):
    async def record(self, event: dict) -> None: ...


class ScreenResult(BaseModel):
    """Whether a piece of outside text tries to instruct the agents reading it."""
    flagged: bool = False
    reasons: list[str] = []
    engine: str = "patterns"          # patterns (offline) or model_armor
    skipped: bool = False             # the screen could not judge this text (too long, a language it skips)


class Screen(Protocol):
    """Screens text that came from outside (a web page, an uploaded paper, an institution's reply) for planted
    instructions before agents read it. A flagged page is never evidence; a flagged paper or reply is still read,
    with a warning, because the family needs it."""
    async def screen(self, text: str, *, kind: str, source: str = "") -> ScreenResult: ...
