"""The words the server writes, in the language of the page that asked for them.

Views are written in English. For a page in another language, the short sentences the server composes (a
deadline's title, a doubt, a plan's clock or step) are translated together, in one batch, through the
translation port: offline that is the reviewed table in data/knowledge/translations.yaml, live it is Cloud
Translation. A sentence with no translation stays in English rather than being guessed, and a translation
outage leaves the page in English rather than breaking it.

Never translated here: names (of people and institutions), amounts, dates, and quotes. A quote keeps its
source's own words; the Why panel carries a translation beside it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import Request

log = logging.getLogger(__name__)


def page_language(request: Request) -> str:
    """The page's language from the Accept-Language header: 'hi' from 'hi-IN,hi;q=0.9,en;q=0.8'."""
    raw = request.headers.get("accept-language") or "en"
    first = raw.split(",")[0].split(";")[0].strip().lower()
    code = first.split("-")[0]
    return code if code.isalpha() and 2 <= len(code) <= 3 else "en"


_SCRIPTS = [(re.compile(r"[\u0900-\u097f]"), "hi"), (re.compile(r"[\u0d00-\u0d7f]"), "ml"),
            (re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]"), "ja"), (re.compile(r"[\u0600-\u06ff]"), "ar"),
            (re.compile(r"[\u0b80-\u0bff]"), "ta"), (re.compile(r"[\u0980-\u09ff]"), "bn")]


async def localize(deps: Any, lang: str, targets: list[tuple[Any, str]]) -> None:
    """Translates, in place, the named text fields of the given objects (a field may hold a string or a list of
    strings). Pydantic models and plain dicts both work. On an English page, a sentence written in another script (a
    plan question a researcher wrote in Hindi) is put into English too."""
    if not lang or lang == "en":
        await _into_english(deps, targets)
        return

    def get(obj: Any, field: str) -> Any:
        return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)

    def put(obj: Any, field: str, value: Any) -> None:
        if isinstance(obj, dict):
            obj[field] = value
        else:
            setattr(obj, field, value)

    texts: list[str] = []
    for obj, field in targets:
        v = get(obj, field)
        if isinstance(v, str) and v.strip():
            texts.append(v)
        elif isinstance(v, list):
            texts.extend(x for x in v if isinstance(x, str) and x.strip())
    unique = list(dict.fromkeys(texts))
    if not unique:
        return
    try:
        out = await deps.translator.translate(unique, lang, "en")
    except Exception as e:                # the page stays in English rather than failing
        log.warning("translation to %s failed: %s", lang, e)
        return
    done = dict(zip(unique, out, strict=True))
    for obj, field in targets:
        v = get(obj, field)
        if isinstance(v, str) and done.get(v):
            put(obj, field, done[v])
        elif isinstance(v, list):
            put(obj, field, [done.get(x, x) if isinstance(x, str) else x for x in v])


async def _into_english(deps: Any, targets: list[tuple[Any, str]]) -> None:
    def get(obj: Any, field: str) -> Any:
        return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)

    by_lang: dict[str, list[tuple[Any, str]]] = {}
    for obj, field in targets:
        v = get(obj, field)
        if isinstance(v, str) and v.strip():
            src = next((code for rx, code in _SCRIPTS if rx.search(v)), None)
            if src:
                by_lang.setdefault(src, []).append((obj, field))
    for src, items in by_lang.items():
        texts = list(dict.fromkeys(get(o, f) for o, f in items))
        try:
            out = await deps.translator.translate(texts, "en", src)
        except Exception as e:            # the page keeps the words as written rather than failing
            log.warning("translation from %s failed: %s", src, e)
            continue
        done = dict(zip(texts, out, strict=True))
        for obj, field in items:
            new = done.get(get(obj, field))
            if new and new != get(obj, field):
                if isinstance(obj, dict):
                    obj[field] = new
                else:
                    setattr(obj, field, new)
