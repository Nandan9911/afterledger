"""Expands a family's paper specs (data/families/<key>/papers.yaml) and renders them.

The specs are written for the story day the designs use (14 October 2026). Seeding on another
story day shifts every date by the same number of days, so every interval in the story (days
late, days to a deadline) stays the same. Dates inside text use placeholders such as
{d:2026-09-02|d_month_y}, so they move too.
"""
from __future__ import annotations

import calendar
import copy
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from . import degrade
from .formats import fmt_amount, fmt_date
from .templates import TEMPLATES, Rendered

STORY_DAY_0 = date(2026, 10, 14)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLACEHOLDER = re.compile(r"\{d:(\d{4}-\d{2}-\d{2})\|([a-z_0-9]+)\}")


def shift_date(d: date | str, shift: int) -> date:
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return d + timedelta(days=shift)


def shift_tree(obj: Any, shift: int) -> Any:
    """Shift every ISO date and every {d:...|style} placeholder in a spec tree."""
    if isinstance(obj, dict):
        return {k: shift_tree(v, shift) for k, v in obj.items()}
    if isinstance(obj, list):
        return [shift_tree(v, shift) for v in obj]
    if isinstance(obj, date):
        return obj + timedelta(days=shift)
    if isinstance(obj, str):
        if _ISO.match(obj):
            return (date.fromisoformat(obj) + timedelta(days=shift)).isoformat()
        if "{d:" in obj:
            return _PLACEHOLDER.sub(lambda m: fmt_date(date.fromisoformat(m.group(1)) + timedelta(days=shift),
                                                       m.group(2)), obj)
    return obj


# ---------------------------------------------------------------------------------------------
# Ledgers
# ---------------------------------------------------------------------------------------------

def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _day_in(y: int, m: int, day: int) -> date:
    return date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _as_date(v: Any) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


def ledger_entries(ledger: dict) -> list[dict]:
    """Dated entries (not yet balanced) from recurring rules and explicit lines, in posting order."""
    start, end = _as_date(ledger["start"]), _as_date(ledger["end"])
    entries: list[dict] = []
    seq = 0
    months = _months(start, end)
    for rule in ledger.get("recurring", []):
        frm = _as_date(rule["from"]) if rule.get("from") else start
        until = _as_date(rule["until"]) if rule.get("until") else end
        if rule.get("every_days"):            # e.g. a pension paid every four weeks
            d, i = _as_date(rule["first"]), 0
            while d <= min(until, end):
                if d >= max(frm, start):
                    for direction in ("debit", "credit"):
                        if direction in rule:
                            amt = rule[direction]
                            amt = amt[i % len(amt)] if isinstance(amt, list) else amt
                            seq += 1
                            entries.append({"date": d, "narration": rule["narration"], "direction": direction,
                                            "amount": amt, "seq": rule.get("seq", 50), "n": seq,
                                            "meta": rule.get("meta", {})})
                    i += 1
                d += timedelta(days=int(rule["every_days"]))
            continue
        for i, (y, m) in enumerate(months):
            if rule.get("months") and m not in rule["months"]:
                continue
            d = _day_in(y, m, int(rule.get("day", 1)))
            if d < frm or d > until or d < start or d > end:
                continue
            for direction in ("debit", "credit"):
                if direction in rule:
                    amt = rule[direction]
                    if isinstance(amt, list):
                        amt = amt[i % len(amt)]
                    seq += 1
                    entries.append({"date": d, "narration": rule["narration"], "direction": direction,
                                    "amount": amt, "seq": rule.get("seq", 50), "n": seq, "meta": rule.get("meta", {})})
    for line in ledger.get("lines", []):
        d = _as_date(line["date"])
        for direction in ("debit", "credit"):
            if direction in line:
                seq += 1
                entries.append({"date": d, "narration": line["narration"], "direction": direction,
                                "amount": line[direction], "seq": line.get("seq", 60), "n": seq,
                                "meta": line.get("meta", {})})
    entries.sort(key=lambda e: (e["date"], e["seq"], e["n"]))
    return entries


def balance_ledger(ledger: dict) -> tuple[Decimal, list[dict]]:
    """Solve the opening balance and any 'solve' amounts from the anchors, then run the balance."""
    entries = ledger_entries(ledger)
    solve_idx = [i for i, e in enumerate(entries) if e["amount"] == "solve"]

    def run(opening: Decimal, solved: Decimal) -> list[Decimal]:
        bal, out = opening, []
        for e in entries:
            amt = solved if e["amount"] == "solve" else Decimal(str(e["amount"]))
            bal = bal + amt if e["direction"] == "credit" else bal - amt
            out.append(bal)
        return out

    opening = Decimal(str(ledger.get("opening", 0))) if ledger.get("opening") not in (None, "solve") else Decimal(0)
    solved = Decimal(0)
    for anchor in ledger.get("anchors", []):
        bals = run(opening, solved)
        if "closing" in anchor:
            target = Decimal(str(anchor["closing"]))
            diff = bals[-1] - target                 # positive: the solved debit must take this much
            if not solve_idx:
                raise ValueError("closing anchor without a 'solve' line")
            e = entries[solve_idx[0]]
            solved = solved + diff if e["direction"] == "debit" else solved - diff
        else:
            d = _as_date(anchor["date"])
            idx = next(i for i, e in enumerate(entries) if e["date"] == d and
                       (not anchor.get("narration") or anchor["narration"] in e["narration"]))
            opening = opening + Decimal(str(anchor["balance"])) - bals[idx]
    if solved < 0:
        raise ValueError(f"a solved amount came out negative: {solved}")
    bals = run(opening, solved)
    for e, b in zip(entries, bals, strict=True):
        e["amount"] = solved if e["amount"] == "solve" else Decimal(str(e["amount"]))
        e["balance"] = b
    return opening, entries


def ledger_rows(ledger: dict, *, date_style: str, amount_style: str, group_by: str = "month",
                rows_per_page: int = 6, page_breaks: list[str] | None = None,
                bf_label: str = "BALANCE B/F", cf_label: str | None = None) -> tuple[list[list[dict]], dict]:
    opening, entries = balance_ledger(ledger)
    groups: list[list[dict]] = []
    cur: list[dict] = []
    key_prev = None
    bal_prev = opening
    breaks = {date.fromisoformat(b) if isinstance(b, str) else b for b in (page_breaks or [])}
    for row_no, e in enumerate(entries):
        if group_by == "month":
            key = (e["date"].year, e["month"] if "month" in e else e["date"].month)
            new_group = key_prev is not None and key != key_prev
            key_prev = key
        elif group_by == "breaks":
            new_group = e["date"] in breaks and bool(cur) and not any(r.get("_iso") == e["date"].isoformat() for r in cur)
        else:
            new_group = len([r for r in cur if r.get("_kind") == "txn"]) >= rows_per_page
        if new_group and cur:
            if cf_label:
                cur.append({"narration": cf_label, "balance": fmt_amount(bal_prev, amount_style), "_kind": "cf"})
            groups.append(cur)
            cur = []
        if not cur:
            cur.append({"date": fmt_date(e["date"], date_style), "narration": bf_label,
                        "balance": fmt_amount(bal_prev, amount_style), "_kind": "bf"})
        amount = fmt_amount(e["amount"], amount_style)
        cur.append({"date": fmt_date(e["date"], date_style), "narration": e["narration"],
                    "debit": amount if e["direction"] == "debit" else "",
                    "credit": amount if e["direction"] == "credit" else "",
                    "balance": fmt_amount(e["balance"], amount_style), "_kind": "txn", "_row": row_no,
                    "_iso": e["date"].isoformat(), "_amount": str(e["amount"]), "_dir": e["direction"],
                    "_balance": str(e["balance"]), "_meta": e["meta"]})
        bal_prev = e["balance"]
    if cur:
        groups.append(cur)
    summary = {"opening": str(opening), "closing": str(bal_prev),
               "first": entries[0]["date"].isoformat() if entries else None,
               "last": entries[-1]["date"].isoformat() if entries else None, "count": len(entries)}
    return groups, summary


# ---------------------------------------------------------------------------------------------
# Papers
# ---------------------------------------------------------------------------------------------

@dataclass
class BuiltPaper:
    spec: dict
    rendered: Rendered
    images: list[degrade.PageImage]
    ledger_summary: dict | None = None


def expand(spec: dict) -> tuple[dict, dict | None]:
    """Turn a ledger DSL into row groups; leave other templates as they are."""
    spec = copy.deepcopy(spec)
    summary = None
    if spec.get("ledger"):
        lg = spec["ledger"]
        groups, summary = ledger_rows(lg, date_style=lg.get("date_style", "dmy2"),
                                      amount_style=lg.get("amount_style", "in"),
                                      group_by=lg.get("group_by", "month"), rows_per_page=lg.get("rows_per_page", 6),
                                      page_breaks=lg.get("page_breaks"), bf_label=lg.get("bf_label", "BALANCE B/F"),
                                      cf_label=lg.get("cf_label"))
        spec["row_groups"] = groups
        amount_style = lg.get("amount_style", "in")
        for name, val in (spec.get("summary_fields") or {}).items():
            if val == "closing":
                spec.setdefault("fields", {})[name] = fmt_amount(summary["closing"], amount_style)
        if spec.get("closing_note"):
            spec["closing_note"] = spec["closing_note"].replace("{closing}", fmt_amount(summary["closing"], amount_style))
    return spec, summary


def build_paper(spec: dict, *, seed: int = 7) -> BuiltPaper:
    expanded, summary = expand(spec)
    rendered = TEMPLATES[expanded["template"]](expanded)
    images: list[degrade.PageImage] = []
    photo = expanded.get("photo")
    for i in range(rendered.pages):
        boxes = {ln.id: ln.box for ln in rendered.lines if ln.page == i + 1}
        if photo and (photo.get("pages") is None or (i + 1) in photo["pages"]):
            images.append(degrade.photo(rendered.pdf, i, boxes, seed=seed + i * 13,
                                        angle=photo.get("angle"), blur=photo.get("blur", 0.6),
                                        crop=tuple(photo["crop"]) if photo.get("crop") else None,
                                        paper_tint=photo.get("tint", "#F4EEDC"), table=photo.get("table", "#6B5644")))
        else:
            images.append(degrade.scan(rendered.pdf, i, boxes))
    return BuiltPaper(spec=expanded, rendered=rendered, images=images, ledger_summary=summary)
