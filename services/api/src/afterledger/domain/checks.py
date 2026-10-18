"""Exact checks used by the verification harness."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal

_SPACE = re.compile(r"\s+")
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−ー"), "-")
_QUOTES = dict.fromkeys(map(ord, "‘’‚‛"), "'") | dict.fromkeys(
    map(ord, "“”„‟"), '"')


def ascii_digits(text: str) -> str:
    """Devanagari, Arabic-Indic, full-width and other decimal digits become 0-9."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdecimal() else ch for ch in text)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES)
    return _SPACE.sub(" ", ascii_digits(text)).strip().casefold()


def quote_in_page(quote: str, page_text: str) -> bool:
    """A cited quote must appear word for word in the fetched page, after normalising."""
    return len(normalise(quote)) >= 12 and normalise(quote) in normalise(page_text)


_ERAS = {"明治": 1868, "大正": 1912, "昭和": 1926, "平成": 1989, "令和": 2019}
_ERA_DATE = re.compile(r"(明治|大正|昭和|平成|令和)(元|\d{1,2})年(\d{1,2})月(\d{1,2})日")


def japanese_era_to_iso(text: str) -> str | None:
    m = _ERA_DATE.search(ascii_digits(unicodedata.normalize("NFKC", text)))
    if not m:
        return None
    era, year, month, day = m.groups()
    year_number = 1 if year == "元" else int(year)
    return date(_ERAS[era] + year_number - 1, int(month), int(day)).isoformat()


_GROUPED = re.compile(r"(\d{1,3}(?:[.,]\d{3})+)[.,](\d{1,2})")


def parse_amount(text: str, decimal_sep: str = ".") -> Decimal:
    """'₹6,00,000.00', '1.234,56' (decimal_sep=','), '１２，０００円' -> Decimal. Thousands in groups of three with two
    decimals after the last separator read the same whichever separators are used: a live reader copied 2,915.40
    as 2.915.40."""
    s = re.sub(r"[^\d.,]", "", ascii_digits(unicodedata.normalize("NFKC", text)))
    m = _GROUPED.fullmatch(s)
    if m:
        return Decimal(re.sub(r"\D", "", m.group(1)) + "." + m.group(2))
    s = s.replace(".", "").replace(",", ".") if decimal_sep == "," else s.replace(",", "")
    return Decimal(s)


def reconcile(kind: str, a: str | None, b: str | None,
              decimal_sep: str = ".") -> tuple[str, str | None]:
    """Compare two independent readings. Returns (status, agreed value)."""
    if a is None or b is None:
        return ("one_path_only", a or b)
    if kind == "amount":
        a2, b2 = parse_amount(a, decimal_sep), parse_amount(b, decimal_sep)
    elif kind == "identifier":
        a2, b2 = (re.sub(r"[\s\-./]", "", normalise(x)) for x in (a, b))
    else:
        a2, b2 = normalise(a), normalise(b)
    return ("agree", a) if a2 == b2 else ("disagree", None)


_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?!\d)(?:[.,]\d+)?|\d[\d,.]*\d|\d")


def _canonical_number(raw: str) -> str:
    """'6,41,870.00' -> '641870'; '167.06' -> '167.06'; '5.50' -> '5.5'; '1.234,56' -> '1234.56'."""
    s = re.sub(r"[\s\u00a0\u202f\u2009]", "", raw)
    m = re.match(r"^(.*\d)[.,](\d{1,2})$", s)
    if m and (len(m.group(2)) == 2 or re.search(r"[.,]", m.group(1)) is None):
        whole = re.sub(r"\D", "", m.group(1))
        frac = m.group(2).rstrip("0")
        return (whole.lstrip("0") or "0") + ("." + frac if frac else "")
    digits = re.sub(r"\D", "", s)
    return digits.lstrip("0") or "0"


def _numbers(text: str) -> set[str]:
    return {_canonical_number(raw) for raw in _NUMBER.findall(ascii_digits(text))}


def fidelity_violations(letter: str, facts_block: str) -> set[str]:
    """Numbers in a drafted letter that are not in the facts block it was written from.

    Dates written in words, era years and numerals in words are handled by a separate
    agent check; this function catches the commonest error, a changed or invented figure.
    """
    return _numbers(letter) - _numbers(facts_block)
