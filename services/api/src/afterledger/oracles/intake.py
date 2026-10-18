"""Offline answers for the intake agents."""
from __future__ import annotations

import re
from collections import Counter

from ..adapters.llm.models import OracleRequest
from ..domain.calendars import parse_date
from ..domain.checks import normalise
from . import Ctx, oracle

_SCRIPT_LANG = [(r"[ऀ-ॿ]", "hi"), (r"[ഀ-ൿ]", "ml"), (r"[぀-ヿ一-鿿]", "ja"),
                (r"[؀-ۿ]", "ar")]
_ES = re.compile(r"\b(mi|murió|falleció|esposo|esposa|padre|madre|banco|seguro|que|el|la|de)\b", re.I)
_DE = re.compile(r"\b(mein|meine|vater|mutter|gestorben|bank|und|der|die|das)\b", re.I)
_FR = re.compile(r"\b(mon|ma|père|mère|décédé|banque|assurance|est|le|la|de)\b", re.I)


def detect_lang(text: str) -> str:
    for rx, lang in _SCRIPT_LANG:
        if re.search(rx, text):
            return lang
    scores = {"es": len(_ES.findall(text)), "de": len(_DE.findall(text)), "fr": len(_FR.findall(text))}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else "en"


def heard_lang(text: str, hint: str | None) -> str:
    """The language a family member wrote in: the page's language (the hint) unless the text says otherwise, as
    English typed on the Hindi page does (no Devanagari), or Spanish typed on the English page."""
    guess = detect_lang(text)
    if not hint:
        return guess
    script = dict((lang, rx) for rx, lang in _SCRIPT_LANG).get(hint)
    if script and not re.search(script, text):
        return guess
    return guess if guess not in ("en", hint) else hint


def _turns(fam) -> list[dict]:
    return (fam.get("intake") or {}).get("turns", [])


@oracle("listener")
async def listener(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    text = p.get("text", "")
    fam = ctx.family(req)
    if fam:
        for t in _turns(fam):
            if t["role"] == "family" and normalise(t["text"]) == normalise(text):
                return {"lang": t["lang"], "text": t["text"], "translation": t.get("translation", t["text"]),
                        "intent": "story"}
    lang = heard_lang(text, p.get("lang_hint"))
    translation = text
    if lang != "en":
        translation = p.get("translation_hint") or (await ctx.deps.translator.translate([text], "en", lang))[0]
    intent = "question" if text.strip().endswith(("?", "？", "؟")) else "story"
    return {"lang": lang, "text": text, "translation": translation, "intent": intent}


def _merge(base: dict, delta: dict) -> dict:
    out = dict(base)
    for k, v in delta.items():
        if isinstance(v, list) and isinstance(out.get(k), list):
            seen = {str(x) for x in out[k]}
            out[k] = out[k] + [x for x in v if str(x) not in seen]
        else:
            out[k] = v
    return out


@oracle("situation_extractor")
def situation_extractor(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    heard = [u for u in p.get("utterances", []) if u.get("role") == "family"]
    if fam and (fam.get("intake") or {}).get("situation"):
        sit: dict = {}
        steps = fam["intake"]["situation"]
        for k in sorted(steps, key=int):
            if int(k) <= len(heard):
                sit = _merge(sit, steps[k])
        ids = [u["id"] for u in heard]
        for m in sit.get("family", []):
            m.setdefault("source_utterances", ids[-1:])
        return sit
    return _baseline_situation(p.get("situation") or {}, heard, p.get("utterances", []))


_REL = r"((?i:husband|wife|father|mother|son|daughter|brother|sister|grandfather|grandmother|uncle|aunt))"

SPEAKER_IS = {"husband": "wife", "wife": "husband", "father": "child", "mother": "child", "son": "parent",
              "daughter": "parent", "brother": "sibling", "sister": "sibling", "grandfather": "grandchild",
              "grandmother": "grandchild", "uncle": "nephew or niece", "aunt": "nephew or niece"}
"""What the speaker is to the person who died, from how they name them ('my father' -> a child)."""

_COUNTRY_ALIASES = {"england": "GB", "scotland": "GB", "wales": "GB", "britain": "GB", "uk": "GB", "u.k.": "GB",
                    "usa": "US", "u.s.": "US", "america": "US", "united states": "US", "texas": "US", "california": "US",
                    "new york": "US", "bombay": "IN", "mumbai": "IN", "delhi": "IN", "kerala": "IN", "kochi": "IN",
                    "bengaluru": "IN", "chennai": "IN", "kolkata": "IN", "tokyo": "JP", "osaka": "JP", "kyoto": "JP",
                    "bavaria": "DE", "munich": "DE", "berlin": "DE", "hamburg": "DE", "london": "GB",
                    "manchester": "GB", "leeds": "GB", "paris": "FR", "lyon": "FR", "marseille": "FR", "cairo": "EG",
                    "alexandria": "EG", "dubai": "AE", "abu dhabi": "AE", "riyadh": "SA", "madrid": "ES",
                    "barcelona": "ES", "mexico city": "MX", "sydney": "AU", "melbourne": "AU", "toronto": "CA",
                    "jakarta": "ID", "kuala lumpur": "MY", "manila": "PH", "seoul": "KR", "bangkok": "TH",
                    "são paulo": "BR", "sao paulo": "BR", "rio de janeiro": "BR"}

_HIS = {"father", "husband", "son", "brother", "grandfather", "uncle"}


def _relation_named(text: str) -> str | None:
    """'my father' -> 'father': who died, as the speaker named them, when no name came with it."""
    m = re.search(r"\b(?i:my|our) " + _REL + r"\b", text)
    return m.group(1).lower() if m else None


def _last_topic(utterances: list[dict]) -> tuple[str | None, bool]:
    """The topic of the last question asked, and whether the family has replied since."""
    for i in range(len(utterances) - 1, -1, -1):
        if utterances[i].get("role") == "assistant":
            replied = any(u.get("role") == "family" for u in utterances[i + 1:])
            return utterances[i].get("topic"), replied
    return None, False


def _looks_like_a_name(text: str) -> str | None:
    """A short reply that is only a name ('Ahmed Mahmoud Hassan', 'It was Ahmed Hassan.')."""
    t = re.sub(r"^(?i:it was|his name was|her name was|he was|she was|name:?)\s+", "", text.strip()).strip(" .!")
    words = t.split()
    if 1 < len(words) <= 6 and all(w[:1].isupper() or w.lower() in ("bin", "binti", "de", "van", "von", "al", "el")
                                   for w in words):
        return t
    return None


def _countries(text: str) -> list[str]:
    """Countries named in the text, by their English names (from the CLDR data babel ships) and common aliases."""
    from babel import Locale
    names = {v.lower(): k for k, v in Locale("en").territories.items() if len(k) == 2 and len(v) >= 4}
    low = text.lower()
    found: list[tuple[int, str]] = []
    for name, code in [*names.items(), *_COUNTRY_ALIASES.items()]:
        for mm in re.finditer(rf"\b{re.escape(name)}\b", low):
            found.append((mm.start(), code))
    return list(dict.fromkeys(code for _, code in sorted(found)))


def _baseline_situation(current: dict, heard: list[dict], turns: list[dict] | None = None) -> dict:
    """A rule-based reading of English (or translated) text: relationships, a date, a place."""
    sit = dict(current)
    sit.setdefault("family", [])
    text = " ".join((u.get("translation") or u.get("text") or "") for u in heard)
    m = re.search(r"\b(?i:my) " + _REL + r",?\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,3})", text)
    named_rel = None
    if m and not sit.get("deceased_name"):
        sit["deceased_name"] = m.group(2).strip()
        named_rel = m.group(1).lower()
    topic, replied = _last_topic(turns or [])
    if not sit.get("deceased_name") and topic in ("who", "who_name") and replied and heard:
        last = heard[-1].get("translation") or heard[-1].get("text") or ""
        name = _looks_like_a_name(last)
        if name:
            sit["deceased_name"] = name
    d = None
    for cand in re.findall(r"\b(\d{1,2}\s+[A-Z][a-z]+\s*\d{0,4}|[A-Z][a-z]+\s+\d{1,2},?\s*\d{4}|\d{4}-\d{2}-\d{2})\b", text):
        d = parse_date(cand if re.search(r"\d{4}", cand) else cand + " 2026")
        if d:
            break
    if d and not sit.get("died_on"):
        sit["died_on"] = d.isoformat()
    pm = re.search(r"\bin ([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", text)
    if pm and not sit.get("died_in"):
        sit["died_in"] = pm.group(1)
    spk = named_rel or _relation_named(text)          # what the deceased was to the speaker: the first named
    speaker_is = "spouse" if spk in ("husband", "wife") else "child" if spk in ("mother", "father") else None
    if spk and not sit.get("speaker_relationship"):
        sit["speaker_relationship"] = SPEAKER_IS.get(spk)
    as_deceased = {"spouse": {"son": "son", "daughter": "daughter", "father": "father", "mother": "mother"},
                   "child": {"brother": "son", "sister": "daughter", "father": "husband", "mother": "wife",
                             "son": "grandson", "daughter": "granddaughter"}}
    for who, rel, name in re.findall(r"\b((?i:our|my|his|her)) " + _REL + r",?\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)",
                                     text):
        rel = rel.lower()
        if who.lower() in ("his", "her"):
            to_deceased = rel
        else:                             # 'my son' is the deceased's son only if the speaker is the spouse
            to_deceased = as_deceased.get(speaker_is or "", {}).get(rel)
        if to_deceased is None or name == sit.get("deceased_name") or any(f["name"] == name for f in sit["family"]):
            continue
        heir = to_deceased in ("husband", "wife", "son", "daughter", "father", "mother")
        city = re.search(rf"\b{re.escape(name)}\b[^.]*?\b(?:lives|is|stays|works) in ([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", text)
        sit["family"].append({"name": name, "relationship": to_deceased, "languages": [], "roles": ["heir"] if heir else [],
                              "city": city.group(1) if city else None})
    low = text.lower()
    if sit.get("death_cause", "unknown") == "unknown":
        if re.search(r"\b(accident|crash|collision|drowned|fell)\b", low):
            sit["death_cause"] = "accident"
        elif re.search(r"\b(ill|illness|cancer|heart attack|stroke|disease|unwell|sick|covid)\b", low):
            sit["death_cause"] = "illness"
    if sit.get("will", "unknown") == "unknown":
        if re.search(r"\b(no will|didn't leave a will|did not leave a will|without a will)\b", low):
            sit["will"] = "no"
        elif re.search(r"\b(left a will|his will|her will|the will)\b", low):
            sit["will"] = "yes"
    banks = re.findall(r"\b((?:[A-Z][\w&'-]+\s){1,3}(?:Bank|Building Society|Savings|Life|Insurance|Assurance|"
                       r"Credit Union|Securities))\b", text)
    pensions = [f"a pension from {x}" for x in
                re.findall(r"\bpension (?:from|with|at) ([A-Z][\w&'-]+(?:\s[A-Z][\w&'-]+){0,3})", text)]
    sit["holdings_mentioned"] = sorted(set(sit.get("holdings_mentioned", [])) | {b.strip() for b in banks} | set(pensions))
    codes = _countries(text)
    if codes:
        sit["country_codes"] = list(dict.fromkeys([*(sit.get("country_codes") or []), *codes]))
        if not sit.get("death_country"):
            sit["death_country"] = codes[0]
    sit.setdefault("will", "unknown")
    sit.setdefault("mode", "family")
    sit["unknowns"] = [u for u in ["date of death" if not sit.get("died_on") else None,
                                   "who the family are" if not sit["family"] else None] if u]
    return sit


@oracle("gap_planner")
def gap_planner(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    fam = ctx.family(req)
    heard = [u for u in p.get("utterances", []) if u.get("role") == "family"]
    if fam and _turns(fam):
        asks = [t for t in _turns(fam) if t["role"] == "assistant"]
        k = len(heard)
        if k < len(asks):
            t = asks[k]
            return {"done": False, "question": t.get("translation", t["text"]), "why": t.get("why", ""),
                    "cites": t.get("cites", []), "topic": t.get("topic", "")}
        return {"done": True, "question": "", "why": "", "cites": [], "topic": "summary"}
    sit = p.get("situation") or {}
    if len(heard) >= 6:
        return {"done": True}
    text = " ".join((u.get("translation") or u.get("text") or "") for u in heard)
    rel = _relation_named(text)
    their = "his" if rel in _HIS else "her" if rel else "their"
    last, replied = _last_topic(p.get("utterances", []))
    asks: list[tuple[str, str]] = []
    if not sit.get("deceased_name"):
        if not heard:
            asks.append(("who", "We are very sorry for your loss. Could you tell us who has passed away?"))
        asks.append(("who_name", f"What was your {rel}'s full name, as it is written on {their} papers?" if rel else
                     "What was their full name, as it is written on their papers?"))
    if not sit.get("died_on") or not sit.get("died_in"):
        asks.append(("when_where", "When and where did they pass away?" if not rel else
                     f"When and where did your {rel} pass away?"))
    if not sit.get("holdings_mentioned"):
        asks.append(("money", "Who looked after the money, and do you have any bank or insurance papers?"))
    if not sit.get("family"):
        asks.append(("family", "Who else is in the close family - a spouse, children or parents?"))
    if sit.get("will", "unknown") == "unknown":
        asks.append(("will", "Did they leave a will?" if not rel else f"Did your {rel} leave a will?"))
    times = Counter(("who" if u.get("topic") == "who_name" else u.get("topic")) for u in p.get("utterances", [])
                    if u.get("role") == "assistant")
    for topic, question in asks:
        if (topic == last and replied) or times["who" if topic == "who_name" else topic] >= 2:
            continue                      # asked already and answered without it: move on rather than repeat
        return {"done": False, "question": question, "topic": topic}
    return {"done": True}


@oracle("tone_critic")
async def tone_critic(ctx: Ctx, req: OracleRequest) -> dict:
    p = ctx.payload(req)
    q, lang = p.get("question", ""), p.get("lang", "en")
    fam = ctx.family(req)
    if fam:
        for t in _turns(fam):
            if t["role"] == "assistant" and normalise(t.get("translation", t["text"])) == normalise(q):
                return {"ok": True, "question": q, "question_local": t["text"], "lang": t["lang"], "changes": ""}
    local = q if lang == "en" else (await ctx.deps.translator.translate([q], lang, "en"))[0]
    shown_lang = lang if local != q else "en"
    return {"ok": True, "question": q, "question_local": local, "lang": shown_lang,
            "changes": "" if shown_lang == lang else "No reviewed translation offline; shown in English."}


@oracle("locale_detector")
def locale_detector(ctx: Ctx, req: OracleRequest) -> dict:
    fam = ctx.family(req)
    if fam:
        langs = fam.get("languages", ["en"])
        country = (fam.get("countries") or ["IN"])[0]
        return {"languages": langs, "locale": f"{langs[0]}-{country}", "scripts": [], "channel": "voice"}
    utterances = ctx.payload(req).get("utterances", [])
    heard = [u.get("lang") for u in utterances if u.get("lang") and u.get("lang") != "en"]
    # the language the listener heard (the page's language when the family chose one), else a guess from the text
    lang = heard[0] if heard else detect_lang(" ".join(u.get("text", "") for u in utterances))
    return {"languages": [lang] if lang == "en" else [lang, "en"], "locale": lang, "scripts": [], "channel": "text"}
