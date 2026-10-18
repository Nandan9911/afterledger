"""Name handling across scripts and naming customs. Names are never translated; readings and
romanisations are added beside the original for matching only."""
from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

HONORIFICS = {
    "shri", "sri", "smt", "smt.", "shrimati", "kumari", "kum", "late", "mr", "mrs", "ms", "miss", "dr",
    "sr", "sra", "srta", "don", "dona", "herr", "frau", "m.", "mme", "様", "殿", "故", "さん", "श्री", "श्रीमती",
    "स्व.", "स्वर्गीय",
}

_KANA = {
    "キャ": "kya", "キュ": "kyu", "キョ": "kyo", "シャ": "sha", "シュ": "shu", "ショ": "sho", "チャ": "cha",
    "チュ": "chu", "チョ": "cho", "ニャ": "nya", "ニュ": "nyu", "ニョ": "nyo", "ヒャ": "hya", "ヒュ": "hyu",
    "ヒョ": "hyo", "ミャ": "mya", "ミュ": "myu", "ミョ": "myo", "リャ": "rya", "リュ": "ryu", "リョ": "ryo",
    "ギャ": "gya", "ギュ": "gyu", "ギョ": "gyo", "ジャ": "ja", "ジュ": "ju", "ジョ": "jo", "ビャ": "bya",
    "ビュ": "byu", "ビョ": "byo", "ピャ": "pya", "ピュ": "pyu", "ピョ": "pyo",
    "ア": "a", "イ": "i", "ウ": "u", "エ": "e", "オ": "o", "カ": "ka", "キ": "ki", "ク": "ku", "ケ": "ke",
    "コ": "ko", "サ": "sa", "シ": "shi", "ス": "su", "セ": "se", "ソ": "so", "タ": "ta", "チ": "chi",
    "ツ": "tsu", "テ": "te", "ト": "to", "ナ": "na", "ニ": "ni", "ヌ": "nu", "ネ": "ne", "ノ": "no",
    "ハ": "ha", "ヒ": "hi", "フ": "fu", "ヘ": "he", "ホ": "ho", "マ": "ma", "ミ": "mi", "ム": "mu",
    "メ": "me", "モ": "mo", "ヤ": "ya", "ユ": "yu", "ヨ": "yo", "ラ": "ra", "リ": "ri", "ル": "ru",
    "レ": "re", "ロ": "ro", "ワ": "wa", "ヲ": "o", "ン": "n", "ガ": "ga", "ギ": "gi", "グ": "gu", "ゲ": "ge",
    "ゴ": "go", "ザ": "za", "ジ": "ji", "ズ": "zu", "ゼ": "ze", "ゾ": "zo", "ダ": "da", "ヂ": "ji",
    "ヅ": "zu", "デ": "de", "ド": "do", "バ": "ba", "ビ": "bi", "ブ": "bu", "ベ": "be", "ボ": "bo",
    "パ": "pa", "ピ": "pi", "プ": "pu", "ペ": "pe", "ポ": "po", "ー": "", "ッ": "", "ヴ": "vu",
}


def script_of(text: str) -> str:
    for ch in text:
        if ch.isspace() or not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "CJK" in name:
            return "Hani"
        if "KATAKANA" in name or "HIRAGANA" in name:
            return "Kana"
        if "DEVANAGARI" in name:
            return "Deva"
        if "MALAYALAM" in name:
            return "Mlym"
        if "ARABIC" in name:
            return "Arab"
        if "HANGUL" in name:
            return "Hang"
        if "THAI" in name:
            return "Thai"
        return "Latn"
    return "Zyyy"


def katakana_to_romaji(text: str) -> str:
    t = unicodedata.normalize("NFKC", text)   # half-width ｻﾄｳ -> サトウ
    out, i = [], 0
    while i < len(t):
        two, one = t[i:i + 2], t[i]
        if two in _KANA:
            out.append(_KANA[two])
            i += 2
        elif one == "ッ" and i + 1 < len(t):
            nxt = _KANA.get(t[i + 1:i + 3]) or _KANA.get(t[i + 1], "")
            out.append(nxt[:1])
            i += 1
        elif one in _KANA:
            out.append(_KANA[one])
            i += 1
        else:
            out.append(one)
            i += 1
    romaji = "".join(out)
    return re.sub(r"ou\b|oo\b", "o", romaji)  # Sato rather than Satou for matching


def strip_honorifics(text: str) -> tuple[str, list[str]]:
    removed: list[str] = []
    words = []
    for w in unicodedata.normalize("NFKC", text).replace(",", " ").split():
        if w.casefold().strip(".") in HONORIFICS or w in HONORIFICS:
            removed.append(w)
        else:
            words.append(w)
    return " ".join(words), removed


def tokens(text: str) -> list[str]:
    base, _ = strip_honorifics(text)
    if script_of(base) == "Kana":
        base = katakana_to_romaji(base)
    base = unicodedata.normalize("NFKD", base)
    base = "".join(ch for ch in base if not unicodedata.combining(ch))
    return [t for t in re.sub(r"[^\w\s]", " ", base).casefold().split() if t]


def initials_compatible(short: str, full: str) -> bool:
    """'R K VERMA' fits 'Rajesh Kumar Verma'; 'NAIR SURESH N' fits 'Suresh Narayanan Nair'."""
    a, b = tokens(short), tokens(full)
    if not a or not b:
        return False
    remaining = list(b)
    for tok in a:
        match = next((x for x in remaining if x == tok or (len(tok) == 1 and x.startswith(tok))
                      or fuzz.ratio(x, tok) >= 85), None)
        if match is None:
            return False
        remaining.remove(match)
    return True


def name_similarity(a: str, b: str) -> float:
    """0..1, order-insensitive, honorifics removed, kana romanised."""
    ta, tb = " ".join(sorted(tokens(a))), " ".join(sorted(tokens(b)))
    if not ta or not tb:
        return 0.0
    return max(fuzz.token_sort_ratio(ta, tb), fuzz.token_set_ratio(ta, tb)) / 100.0
