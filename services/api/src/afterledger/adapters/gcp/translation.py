"""Cloud Translation (v3) behind the reviewed table.

A reviewed translation (data/knowledge/translations.yaml) always wins: those were written and checked by hand.
Anything else is machine translated in batches and cached in memory for the life of the instance. A letter's
family copy is still translated back and compared by an agent, and its numbers are checked by code, so a
machine translation never reaches a family unchecked.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any

log = logging.getLogger(__name__)

_MAX_ITEMS = 100                              # well inside the API's 1,024 strings per request
_MAX_CHARS = 25_000                           # and its 30,000 code points per request
_CACHE = 5_000


class CloudTranslator:
    def __init__(self, project: str, location: str, reviewed: Any, client: Any = None, health: Any = None):
        self.health = health
        if client is None:
            from google.cloud import translate_v3
            client = translate_v3.TranslationServiceAsyncClient()
        self.client = client
        self.parent = f"projects/{project}/locations/{location}"
        self.reviewed = reviewed
        self.cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self.calls = 0

    @property
    def misses(self) -> Any:
        return self.reviewed.misses

    async def translate(self, texts: list[str], target: str, source: str | None = None) -> list[str]:
        lang = target.split("-")[0]
        src = (source or "").split("-")[0] or None
        out = list(await self.reviewed.translate(texts, target, source))
        if lang == src:
            return out
        need: list[int] = []
        for i, (text, got) in enumerate(zip(texts, out, strict=True)):
            if got != text or not text.strip():
                continue                  # reviewed, or nothing to translate
            hit = self.cache.get((lang, text))
            if hit is not None:
                self.cache.move_to_end((lang, text))
                out[i] = hit
            else:
                need.append(i)
        for chunk in _chunks(need, texts):
            request: dict[str, Any] = {"parent": self.parent, "contents": [texts[i] for i in chunk],
                                       "mime_type": "text/plain", "target_language_code": lang}
            if src:
                request["source_language_code"] = src
            try:
                response = await self.client.translate_text(request=request)
            except Exception as e:         # the caller shows the original rather than nothing
                log.warning("Cloud Translation to %s failed: %s", lang, e)
                if self.health is not None:
                    self.health.failed("translation", e)    # a strict live run stops here
                break
            self.calls += 1
            if self.health is not None:
                self.health.succeeded("translation")
            for i, tr in zip(chunk, response.translations, strict=False):
                out[i] = tr.translated_text
                self.cache[(lang, texts[i])] = tr.translated_text
            while len(self.cache) > _CACHE:
                self.cache.popitem(last=False)
        return out


def _chunks(indices: list[int], texts: list[str]) -> list[list[int]]:
    chunks: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i in indices:
        n = len(texts[i])
        if cur and (len(cur) >= _MAX_ITEMS or size + n > _MAX_CHARS):
            chunks.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        chunks.append(cur)
    return chunks
