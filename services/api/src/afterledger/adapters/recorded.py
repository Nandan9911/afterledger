"""What the open web returned to a recording run, kept beside its cassettes (only when the run can be resumed,
AL_LLM_RECORD_RESUME). Google Search answers differently from one minute to the next, and a page can change: a run
made in pieces (a computer that allows a few minutes per command) would otherwise see different results each time,
and every model call after the first search would be a new one. With these, each piece sees what the first saw."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.ids import sha256_text
from ..ports import PageSnapshot, SearchHit


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


class RecordedSearch:
    def __init__(self, inner: Any, root: Path):
        self.inner = inner
        self.root = Path(root) / "_web" / "search"

    async def search(self, query: str, *, lang: str | None = None, sites: list[str] | None = None,
                     k: int = 8, country: str | None = None) -> list[SearchHit]:
        ask = {"query": query, "lang": lang, "sites": sites, "k": k, "country": country}
        path = self.root / f"{sha256_text(json.dumps(ask, ensure_ascii=False, sort_keys=True))[:20]}.json"
        if path.exists():
            return [SearchHit(**h) for h in json.loads(path.read_text(encoding="utf-8"))["hits"]]
        hits = await self.inner.search(query, lang=lang, sites=sites, k=k, country=country)
        if hits:                                  # an empty answer may be a passing failure: asked again next time
            _write(path, {"ask": ask, "hits": [h.model_dump(mode="json") for h in hits]})
        return hits

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


class RecordedPages:
    def __init__(self, inner: Any, root: Path):
        self.inner = inner
        self.root = Path(root) / "_web" / "pages"

    async def fetch(self, url: str) -> PageSnapshot | None:
        snap = await self.inner.snapshots.fetch(url) if hasattr(self.inner, "snapshots") else None
        return snap if snap is not None else await self.fetch_live(url)

    async def fetch_live(self, url: str) -> PageSnapshot | None:
        path = self.root / f"{sha256_text(url)[:20]}.json"
        if path.exists():
            return PageSnapshot(**json.loads(path.read_text(encoding="utf-8"))["page"])
        page = await self.inner.fetch_live(url)
        if page is not None:
            _write(path, {"url": url, "page": page.model_dump(mode="json")})
        return page

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)
