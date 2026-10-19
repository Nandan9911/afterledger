"""How dates and amounts are printed on each country's papers."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from ...domain.calendars import to_japanese_era

_JP_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def fmt_date(d: date, style: str) -> str:
    """Styles: dmy2 (05-07-26), dmy4 (05-07-2026), dmy_slash (05/07/2026), mdy_slash (07/05/2026),
    dmy_dot (05.07.2026), ymd (2026-07-05), ymd_slash (2026/07/05), jp_era (令和8年7月5日),
    jp_era_short (R8.7.5), d_mon_y (05 Jul 2026), d_month_y (5 July 2026), mon_d_y (Jul 5, 2026)."""
    if style == "dmy2":
        return d.strftime("%d-%m-%y")
    if style == "dmy4":
        return d.strftime("%d-%m-%Y")
    if style == "dmy_slash":
        return d.strftime("%d/%m/%Y")
    if style == "mdy_slash":
        return d.strftime("%m/%d/%Y")
    if style == "dmy_dot":
        return d.strftime("%d.%m.%Y")
    if style == "ymd":
        return d.isoformat()
    if style == "ymd_slash":
        return d.strftime("%Y/%m/%d")
    if style == "jp_era":
        return to_japanese_era(d)
    if style == "jp_era_short":
        era = to_japanese_era(d)            # 令和8年7月5日
        y = era[2:era.index("年")]
        return f"R{y}.{d.month}.{d.day}"
    if style == "jp_era_numeric":           # 08-07-05 as printed in some passbooks (era year)
        era = to_japanese_era(d)
        y = int(era[2:era.index("年")]) if era[2:era.index("年")] != "元" else 1
        return f"{y:02d}-{d.month:02d}-{d.day:02d}"
    if style == "d_mon_y":
        return d.strftime("%d %b %Y")
    if style == "d_mon_y2":
        return f"{d.day} {d.strftime('%b')} {d.strftime('%y')}"
    if style == "d_month_y":
        return f"{d.day} {d.strftime('%B %Y')}"
    if style == "mon_d_y":
        return f"{d.strftime('%b')} {d.day}, {d.year}"
    if style == "de_long":
        months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                  "November", "Dezember"]
        return f"{d.day}. {months[d.month - 1]} {d.year}"
    if style == "hi_long":
        months = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
        return f"{d.day} {months[d.month - 1]} {d.year}"
    raise ValueError(f"unknown date style {style}")


def _group_indian(int_part: str) -> str:
    if len(int_part) <= 3:
        return int_part
    head, tail = int_part[:-3], int_part[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def fmt_amount(value: Decimal | float | int | str, style: str) -> str:
    """Styles: in (1,62,318.40), en (162,318.40), jp (162,318), de (162.318,40), plain (162318.40),
    en0 (162,318)."""
    v = Decimal(str(value)).quantize(Decimal("0.01"))
    neg = v < 0
    v = abs(v)
    int_part, frac = f"{v:.2f}".split(".")
    if style == "in":
        s = f"{_group_indian(int_part)}.{frac}"
    elif style == "en":
        s = f"{int(int_part):,}.{frac}"
    elif style == "en0":
        s = f"{int(Decimal(int_part)):,}"
    elif style == "jp":
        s = f"{int(int_part):,}"
    elif style == "de":
        s = f"{int(int_part):,}".replace(",", ".") + "," + frac
    elif style == "plain":
        s = f"{int_part}.{frac}"
    else:
        raise ValueError(f"unknown amount style {style}")
    return f"-{s}" if neg else s


