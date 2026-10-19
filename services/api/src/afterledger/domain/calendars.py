"""Dates in the calendars families use, always kept beside the international date."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from babel.dates import format_date as _babel_format_date

from .checks import ascii_digits, japanese_era_to_iso

_ERAS = [("令和", date(2019, 5, 1)), ("平成", date(1989, 1, 8)), ("昭和", date(1926, 12, 25)),
         ("大正", date(1912, 7, 30)), ("明治", date(1868, 1, 25))]
_ERA_ROMAJI = {"令和": "Reiwa", "平成": "Heisei", "昭和": "Showa", "大正": "Taisho", "明治": "Meiji"}

_BABEL_LOCALE = {"en": "en_GB", "en-IN": "en_IN", "en-US": "en_US", "en-GB": "en_GB", "hi": "hi_IN",
                 "ja": "ja_JP", "es": "es_US", "ml": "ml_IN", "ar": "ar_EG", "de": "de_DE", "fr": "fr_FR"}


def to_japanese_era(d: date, *, with_romaji: bool = False) -> str:
    for name, start in _ERAS:
        if d >= start:
            year = d.year - start.year + 1
            y = "元" if year == 1 else str(year)
            text = f"{name}{y}年{d.month}月{d.day}日"
            if with_romaji:
                text += f" ({_ERA_ROMAJI[name]} {year})"
            return text
    return d.isoformat()


def format_date(d: date, lang: str = "en", style: str = "long") -> str:
    """'2 September 2026', '2026年9月2日', '2 de septiembre de 2026', '२ सितंबर २०२६' uses Latin digits."""
    locale = _BABEL_LOCALE.get(lang, _BABEL_LOCALE.get(lang.split("-")[0], "en_GB"))
    text = _babel_format_date(d, format=style, locale=locale)
    return ascii_digits(text) if lang.startswith("hi") else text


_DMY = re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b")
_YMD = re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_DMONY = re.compile(r"\b(\d{1,2})\s*([A-Za-z]{3})[a-z]*\.?\s*,?\s*(\d{2,4})\b")


_MONDY = re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{2,4})\b")


def parse_date(text: str, order: str = "DMY", century: int = 2000) -> date | None:
    """Parse a printed date in the given field order; Japanese era dates are recognised anywhere."""
    if not text:
        return None
    era = japanese_era_to_iso(text)
    if era:
        return date.fromisoformat(era)
    t = ascii_digits(unicodedata.normalize("NFKC", text))
    if order == "JP_ERA":                 # passbooks print '08-09-07': Reiwa 8, 7 September
        m = re.fullmatch(r"\s*(\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*", t)
        if m:
            yy, mo, d = (int(x) for x in m.groups())
            year = 2018 + yy if 2018 + yy <= date.today().year + 1 else 1988 + yy
            return _safe(year, mo, d)
    m = _YMD.search(t)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _safe(y, mo, d)
    m = _DMONY.search(t)
    if m and m.group(2).lower()[:3] in _MONTHS:
        d, mon, y = int(m.group(1)), _MONTHS[m.group(2).lower()[:3]], int(m.group(3))
        return _safe(y + century if y < 100 else y, mon, d)
    m = _MONDY.search(t)                  # "Mar 4, 1955", "September 9, 2026"
    if m and m.group(1).lower()[:3] in _MONTHS:
        mon, d, y = _MONTHS[m.group(1).lower()[:3]], int(m.group(2)), int(m.group(3))
        return _safe(y + century if y < 100 else y, mon, d)
    m = _DMY.search(t)
    if m:
        a, b, y = (int(x) for x in m.groups())
        y = y + century if y < 100 else y
        d, mo = (a, b) if order == "DMY" else (b, a)
        return _safe(y, mo, d)
    return None


def _safe(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None
