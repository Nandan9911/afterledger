"""Local adapters for events, background tasks, OCR, translation, grounding, search and pages.

Offline they stand in for Pub/Sub, Cloud Tasks, Document AI, Cloud Translation, the
check-grounding API, Google Search grounding and live page fetches. Search and pages read the
knowledge fixtures in data/knowledge/pages: snapshots of real official pages (quotes verified
when the snapshot was taken) and clearly fictional pages for the demo's invented institutions.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
import unicodedata
from collections import Counter, deque
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ...domain.case import OcrLine
from ...domain.checks import ascii_digits, normalise
from ...domain.ids import sha256_text
from ...ports import GroundingResult, Handler, PageSnapshot, ScreenResult, SearchHit

log = logging.getLogger(__name__)


class InProcessTaskQueue:
    """Background work as asyncio tasks. drain() waits for everything, which tests and the demo
    seeder use to run a case to a steady state."""

    def __init__(self, attempts: int = 3, backoff: float = 2.0) -> None:
        self._handlers: dict[str, Handler] = {}
        self._running: set[asyncio.Task] = set()
        self._counter = 0
        self.attempts = attempts          # tries per task, as Cloud Tasks retries a deployed service's tasks
        self.backoff = backoff
        self.on_failure: Any = None       # async (kind, payload, error): told when a task gives up

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    async def enqueue(self, kind: str, payload: dict, *, delay_seconds: int = 0) -> str:
        handler = self._handlers.get(kind)
        if handler is None:
            raise KeyError(f"no handler registered for task kind '{kind}'")
        self._counter += 1
        task_id = f"t{self._counter}"

        async def runner() -> None:
            # a task starts with no writer of its own: it does not inherit the one of the request that queued it
            from ...concurrency import WRITER
            WRITER.set(None)
            if delay_seconds:
                await asyncio.sleep(delay_seconds)
            for attempt in range(1, self.attempts + 1):
                try:
                    await handler(payload)
                    return
                except Exception as e:
                    if attempt < self.attempts and _retryable(e):
                        log.warning("background task %s (%s) failed, retrying (%d of %d): %s", task_id, kind,
                                    attempt, self.attempts, e)
                        await asyncio.sleep(self.backoff * attempt)
                        continue
                    log.exception("background task %s (%s) failed", task_id, kind)
                    if self.on_failure is not None:
                        try:
                            await self.on_failure(kind, payload, e)
                        except Exception:
                            log.exception("could not report the failed task %s", task_id)
                    return

        task = asyncio.create_task(runner(), name=f"{kind}:{task_id}")
        self._running.add(task)
        task.add_done_callback(self._running.discard)
        return task_id

    async def drain(self) -> None:
        while self._running:
            await asyncio.gather(*list(self._running), return_exceptions=True)
            # gather returns at once when every task is already done, so yield: the finished tasks' callbacks (which
            # take them out of _running) run, instead of this loop spinning on them
            await asyncio.sleep(0)


def _retryable(e: BaseException) -> bool:
    """A missing case or paper, a stopped live service, a spent budget and a request the model refuses as it stands
    (400: too large, or invalid) will not change on a second try."""
    if type(e).__name__ == "ClientError" and "INVALID_ARGUMENT" in str(e):
        return False
    return type(e).__name__ not in ("NotFound", "KeyError", "ServiceFailure", "BudgetExceeded", "ConfigError")


class LocalOcr:
    """Text lines with positions. Digital PDFs give their own text lines; photos of synthetic
    papers come with a sidecar that plays the part of Document AI's output (including the
    gaps Document AI documents, such as handwriting in Malayalam, which the sidecar leaves out)."""

    async def read_page(self, *, case_id: str, paper_id: str, page_id: str, image: bytes, mime: str,
                        pdf_page_text: list[dict] | None = None,
                        sidecar: list[dict] | None = None) -> list[OcrLine]:
        rows = sidecar if sidecar is not None else (pdf_page_text or [])
        lines = []
        for i, row in enumerate(rows):
            if row.get("ocr_visible", True) is False:
                continue
            lines.append(OcrLine(
                id=row.get("id") or f"{page_id}-l{i + 1:02d}", case_id=case_id, paper_id=paper_id,
                page_id=page_id, text=row.get("ocr_text", row["text"]), box=row["box"],
                confidence=float(row.get("confidence", 0.98)),
                source="sidecar" if sidecar is not None else "pdf_text"))
        return lines


_LIST_ITEM = re.compile(r"^(\s*(?:[-•・]|\d+[.)])\s+)(\S.*)$", re.S)
_SENTENCE_END = re.compile(r"[.!?]\s+(?=[A-Z0-9₹$€£¥\"“'(])")
_NOT_AN_END = {"mr", "mrs", "ms", "dr", "st", "no", "ltd", "co", "inc", "vs", "jr", "sr", "smt", "shri"}


def sentences(text: str) -> list[str]:
    """An English paragraph cut into sentences, keeping initials ("R. P. Shukla") and abbreviations whole."""
    out, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        word = re.findall(r"[\w.]+$", text[start:m.start()])
        last = word[0].split(".")[-1] if word else ""
        if (len(last) == 1 and last.isupper()) or last.lower() in _NOT_AN_END:
            continue
        out.append(text[start:m.start() + 1].strip())
        start = m.end()
    out.append(text[start:].strip())
    return [x for x in out if x]


class GlossaryTranslator:
    """Offline translation from a reviewed table (data/knowledge/translations.yaml). A text is looked up
    whole, then paragraph by paragraph, then line by line; a list item keeps its marker, and "form - name"
    is looked up part by part. A text is translated only when every piece is in the table: otherwise it is
    returned unchanged and noted as a miss, never half-translated and never invented."""

    def __init__(self, knowledge_dir: Path):
        self.table: dict[str, dict[str, str]] = {}
        f = knowledge_dir / "translations.yaml"
        if f.exists():
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            for item in data.get("entries", []):
                self.table.setdefault(normalise(item["en"]), {}).update(item)   # one text, many languages
        self.misses: deque[tuple[str, str]] = deque(maxlen=2000)   # the latest texts it could not translate

    async def translate(self, texts: list[str], target: str, source: str | None = None) -> list[str]:
        out = []
        lang = target.split("-")[0]
        for t in texts:
            got = self._piece(t, lang) if lang != (source or "").split("-")[0] else t
            if got is not None:
                out.append(got)
            elif lang == "en" or not t.strip():
                out.append(t)
            else:
                self.misses.append((lang, t))
                out.append(t)
        return out

    def _piece(self, text: str, lang: str) -> str | None:
        if not text.strip():
            return text
        hit = self.table.get(normalise(text))
        if hit and lang in hit:
            return str(hit[lang])
        for sep in ("\n\n", "\n"):
            if sep in text.strip("\n"):
                parts = [self._piece(p, lang) for p in text.split(sep)]
                return sep.join(p for p in parts if p is not None) if all(p is not None for p in parts) else None
        m = _LIST_ITEM.match(text)
        if m:
            rest = self._piece(m.group(2), lang)
            return None if rest is None else m.group(1) + rest
        if " - " in text:
            parts = [self._piece(p, lang) for p in text.split(" - ")]
            return " - ".join(p for p in parts if p is not None) if all(p is not None for p in parts) else None
        cut = sentences(text)
        if len(cut) > 1:                  # a paragraph: sentence by sentence
            parts = [self._piece(p, lang) for p in cut]
            if all(p is not None for p in parts):
                return ("" if lang in ("ja", "zh") else " ").join(p for p in parts if p is not None)
        return None


_WORD = re.compile(r"[\wऀ-ॿഀ-ൿ぀-ヿ一-鿿]+", re.UNICODE)
_STOP = {"the", "a", "an", "of", "to", "in", "and", "or", "for", "on", "by", "is", "are", "be", "with",
         "that", "this", "it", "as", "at", "from", "has", "have", "was", "were", "will", "shall", "may",
         "its", "their", "any", "all", "not", "no", "if", "than", "which", "who", "can", "must"}


def _terms(text: str) -> list[str]:
    t = ascii_digits(unicodedata.normalize("NFKC", text)).casefold()
    words = _WORD.findall(t)
    out = []
    for w in words:
        if w in _STOP:
            continue
        if re.fullmatch(r"[぀-ヿ一-鿿]+", w):
            out.extend(w[i:i + 2] for i in range(max(1, len(w) - 1)))   # CJK bigrams
        else:
            out.append(w[:-1] if len(w) > 4 and w.endswith("s") else w)
    return out


class LexicalGrounding:
    """Offline stand-in for the check-grounding API: the share of the claim's content words found
    in the cited text, with every number required to match exactly."""

    async def check(self, claim: str, facts: list[tuple[str, str]], threshold: float = 0.6) -> GroundingResult:
        source = " ".join(text for _, text in facts)
        claim_terms = set(_terms(claim))
        source_terms = set(_terms(source))
        if not claim_terms:
            return GroundingResult(support_score=0.0, supported=False, method="lexical")
        nums_claim = {t for t in claim_terms if t.isdigit()}
        nums_missing = nums_claim - source_terms
        score = len(claim_terms & source_terms) / len(claim_terms)
        if nums_missing:
            score = min(score, 0.4)
        return GroundingResult(support_score=round(score, 3), supported=score >= threshold,
                               claims=[{"claim_text": claim, "score": round(score, 3),
                                        "missing_numbers": sorted(nums_missing)}], method="lexical")


class FixturePages:
    """Page snapshots stored in data/knowledge/pages/*.yaml: real official pages (text excerpts
    taken on the date shown) and fictional institutions' pages for the demo."""

    def __init__(self, knowledge_dir: Path):
        self.pages: dict[str, PageSnapshot] = {}
        folder = knowledge_dir / "pages"
        if folder.is_dir():
            for f in sorted(folder.rglob("*.y*ml")):
                for item in _load_list(f):
                    snap = PageSnapshot(
                        url=item["url"], title=item.get("title", ""), text=item.get("text", ""),
                        lang=item.get("lang"), updated=str(item["updated"]) if item.get("updated") else None,
                        fetched_at=_dt(item.get("retrieved")), fictional=bool(item.get("fictional", False)),
                        tier=int(item["tier"]) if item.get("tier") else None, publisher=item.get("publisher", ""),
                        jurisdiction=item.get("jurisdiction"))
                    snap.content_hash = sha256_text(normalise(snap.text))
                    self.pages[_norm_url(snap.url)] = snap

    async def fetch(self, url: str) -> PageSnapshot | None:
        return self.pages.get(_norm_url(url))

    def all(self) -> list[PageSnapshot]:
        return list(self.pages.values())


class FixtureSearch:
    """BM25 over the page fixtures, so offline research behaves like a search engine over a small
    web: only pages that exist can be found, and anything else returns nothing."""

    def __init__(self, pages: FixturePages):
        self.docs = pages.all()
        self.tf = [Counter(_terms(d.title + " " + d.text)) for d in self.docs]
        self.df: Counter = Counter()
        for tf in self.tf:
            self.df.update(tf.keys())
        self.avgdl = sum(sum(tf.values()) for tf in self.tf) / max(1, len(self.tf))

    async def search(self, query: str, *, lang: str | None = None, sites: list[str] | None = None,
                     k: int = 8, country: str | None = None) -> list[SearchHit]:
        q = _terms(query)
        n = len(self.docs)
        scored = []
        for doc, tf in zip(self.docs, self.tf, strict=True):
            if sites and not any(s in doc.url for s in sites):
                continue
            if country and doc.jurisdiction and not _same_country(doc.jurisdiction, country):
                continue              # a search about one country does not return another country's rules
            dl = sum(tf.values())
            score = 0.0
            for term in q:
                if term not in tf:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += idf * tf[term] * 2.2 / (tf[term] + 1.2 * (0.25 + 0.75 * dl / self.avgdl))
            if score > 0.5:
                scored.append((score, doc))
        scored.sort(key=lambda x: -x[0])
        return [SearchHit(url=d.url, title=d.title, snippet=d.text[:240], lang=d.lang, tier=d.tier, updated=d.updated)
                for _, d in scored[:k]]


EU_MEMBERS = {"AT", "BE", "BG", "HR", "CY", "CZ", "DE", "EE", "ES", "FI", "FR", "GR", "HU", "IT", "LT", "LU",
              "LV", "MT", "NL", "PL", "PT", "RO", "SE", "SI", "SK"}


def _same_country(jurisdiction: str, country: str) -> bool:
    j, c = jurisdiction.split("-")[0].upper(), country.split("-")[0].upper()
    return j == c or (j == "EU" and c in EU_MEMBERS)


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").replace("http://", "https://").lower()


def _dt(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value)) if "T" in str(value) else datetime.fromisoformat(f"{value}T00:00:00")


def _load_list(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "pages" in data:
        return data["pages"]
    if isinstance(data, list):
        return data
    return [data] if data else []


# ---- screening outside text for planted instructions ------------------------------------------------------------

# Phrases that address the reader of a page as if it were a model, in the six app languages and French. Pages,
# papers and letters never need to tell their reader to ignore its rules, so a match is worth flagging.
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    ("ignore_instructions", r"(?<!not )(?<!never )(?<!n't )\b(ignore|disregard|forget|override)\b.{0,40}"
                            r"\b(previous|prior|above|earlier|preceding|"
                            r"all|your|system)\b.{0,20}\b(instructions?|rules|prompts?|guidelines|directions)\b"),
    ("new_instructions", r"\b(new|updated|real|actual)\s+(system\s+)?instructions?\s*:"),
    ("mode_switch", r"\b(you are|you're|act as|switch to)\b.{0,30}\b(developer|dan|jailbreak|admin|god|"
                    r"unrestricted|debug) mode\b"),
    ("reveal_prompt", r"\b(reveal|print|show|repeat|output|leak)\b.{0,30}\b(system prompt|hidden prompt|your "
                      r"instructions|your rules)\b"),
    ("address_the_model", r"\b(ai|assistant|language model|llm|chatbot)s?\b[,:]?\s+(you\s+)?(must now|are (now )?"
                          r"instructed to)\b"),
    ("hide_from_user", r"\bdo not (tell|inform|show|mention (this )?to) the (user|family|reader|human)\b"),
    ("role_tags", r"<\s*/?\s*(system|assistant|instructions?|admin)\s*>|\[\s*(system|inst)\s*\]|\bbegin (system|admin) "
                  r"(prompt|message)\b"),
    ("hi_ignore", r"(पिछले|पूर्व|ऊपर\s+के|सभी)\s+(सभी\s+)?निर्देश\S*\s+(को\s+)?(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल)"),
    ("ja_ignore", r"(以前|これまで|上記|前|全て|すべて)の(すべての|全ての)?(指示|命令|ルール)を(無視|忘れ)"),
    ("es_ignore", r"\b(ignora|ignore|olvida|omite)\b.{0,30}\b(instrucciones|reglas)\b.{0,20}\b(anteriores|previas)?"),
    ("fr_ignore", r"\b(ignore[zr]?|oublie[zr]?)\b.{0,30}\b(instructions|consignes|règles)\b.{0,20}"
                  r"\b(précédentes|antérieures)\b"),
    ("ml_ignore", r"(മുൻപത്തെ|മുമ്പത്തെ|മുൻ|എല്ലാ)\s*(എല്ലാ\s*)?നിർദ്ദേശ\S*\s*(അവഗണിക്ക|മറക്ക)"),
    ("ar_ignore", r"(تجاهل|انس|أهمل)\s+(جميع\s+|كل\s+)?(التعليمات|الأوامر|القواعد)\s*(السابقة|الأولى)?"),
    ("system_prompt_words", r"(system prompt|सिस्टम प्रॉम्प्ट|システムプロンプト|prompt del sistema|موجه النظام)"),
]
_INJECTION_RE = [(name, re.compile(rx, re.IGNORECASE | re.DOTALL)) for name, rx in _INJECTION_PATTERNS]


class PatternScreen:
    """The offline screen: phrases that try to instruct the reader. Live runs add Model Armor in front of it."""
    engine = "patterns"

    async def screen(self, text: str, *, kind: str, source: str = "") -> ScreenResult:
        t = unicodedata.normalize("NFKC", text or "")
        reasons = [name for name, rx in _INJECTION_RE if rx.search(t)]
        return ScreenResult(flagged=bool(reasons), reasons=reasons, engine="patterns")


class NoScreen:
    engine = "off"

    async def screen(self, text: str, *, kind: str, source: str = "") -> ScreenResult:
        return ScreenResult(flagged=False, engine="off", skipped=True)

