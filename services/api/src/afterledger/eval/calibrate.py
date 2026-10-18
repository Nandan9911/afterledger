"""Refits the calibration tables from what a live model prepared (ADR 0005).

For every demo case a live model prepared, each value the readers settled is compared with the truth of the paper
it was read from, and each lead the agents raised with the family's hidden items. Every item falls into the most
specific bin of the table that its signals match (the same lookup the app makes), and each bin's measured accuracy
replaces the hand-set starting value once the bin holds enough items; a smaller bin keeps its starting value, and
the report says so. Identity matches (people) keep their starting values: six families have too few names to
measure them.

    afterledger calibrate            print what a refit would change
    afterledger calibrate --write    and write data/knowledge/calibration.json
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..seed.families import family_keys, load_family
from ..seed.seeder import demo_case_id
from .runner import live_cases, narration_key, right_value

MIN_ITEMS = 20


def _bin_of(table: dict, signals: dict) -> int | None:
    """The index of the most specific bin whose signals all match, as Calibrator.probability chooses."""
    best, best_len = None, -1
    for i, b in enumerate(table.get("bins", [])):
        keys = b["signals"]
        if all(str(signals.get(k)) == str(v) for k, v in keys.items()) and len(keys) > best_len:
            best, best_len = i, len(keys)
    return best


async def reading_items(deps: Any, cases: dict[str, str]) -> list[tuple[dict, bool]]:
    out: list[tuple[dict, bool]] = []
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        papers = {p.id: p for p in await deps.cases.list(case_id, "papers")}
        for v in await deps.cases.list(case_id, "values"):
            paper = papers.get(v.paper_id)
            fixture = (paper.fixture_key or "").split("/", 1)[-1] if paper else ""
            if not fixture or fixture not in fam.papers or v.value is None or v.status == "family_confirmed":
                continue
            truth = fam.built(fixture).rendered.fields.get(v.field)
            if not truth or truth.get("as_printed") is None:
                continue
            signals = {"kind": v.kind, "status": v.status, "photo": paper.origin == "photo", "tier": v.tier,
                       "check": "balance" if "balance" in v.checks else "none"}
            out.append((signals, right_value(v.kind, v.value, truth, paper.country)))
    return out


async def discovery_items(deps: Any, cases: dict[str, str]) -> list[tuple[dict, bool]]:
    from ..oracles.discovery import truth_groups
    out: list[tuple[dict, bool]] = []
    for key, case_id in cases.items():
        fam = load_family(key, deps.settings.story_day)
        tg = truth_groups(fam)
        groups = {g.id: tg.get(narration_key(g.label)) for g in await deps.cases.list(case_id, "groups")}
        truth = fam.get("leads", [])
        for lead in await deps.cases.list(case_id, "leads"):
            if lead.category.startswith("__") or not lead.signals:
                continue
            mapped = {groups.get(g) for g in lead.group_ids} - {None}
            real = any((t.get("group") and t["group"] in mapped) or
                       (not t.get("group") and t.get("category") == lead.category) for t in truth)
            out.append((dict(lead.signals), real))
    return out


def _measured(right: int, n: int) -> float:
    return round((right + 1) / (n + 2), 3)          # Laplace: no bin claims certainty from a sample


def refit(table: dict, items: list[tuple[dict, bool]]) -> tuple[dict, list[dict]]:
    """A new table with measured probabilities where a bin has enough items, and a row per bin for the report.
    Items that match no bin are looked up at the module's base rate, so they refit the base rate (the last row,
    with no signals)."""
    counts = [[0, 0] for _ in table.get("bins", [])]           # [items, right]
    rest = [0, 0]
    for signals, right in items:
        i = _bin_of(table, signals)
        c = counts[i] if i is not None else rest
        c[0] += 1
        c[1] += int(right)
    new = json.loads(json.dumps(table))
    rows = []
    for b, (n, right) in zip(new.get("bins", []), counts, strict=True):
        start, fitted = b["p"], n >= MIN_ITEMS
        if fitted:
            b["p"] = _measured(right, n)
        b["n"] = n
        rows.append({"signals": b["signals"], "start": start, "items": n, "right": right, "fitted": fitted,
                     "p": b["p"]})
    start, fitted = new.get("base_rate", 0.5), rest[0] >= MIN_ITEMS
    if fitted:
        new["base_rate"] = _measured(rest[1], rest[0])
    rows.append({"signals": {}, "start": start, "items": rest[0], "right": rest[1], "fitted": fitted,
                 "p": new.get("base_rate", 0.5)})
    return new, rows


async def calibrate(deps: Any, *, write: bool = False, path: Path | None = None) -> dict:
    """Measures every bin on the demo cases a live model prepared; with write, saves the refitted tables (the
    service loads them when it starts)."""
    cases = await live_cases(deps, {k: demo_case_id(k) for k in family_keys()})
    path = path or Path(deps.settings.knowledge_dir) / "calibration.json"
    tables = json.loads(path.read_text(encoding="utf-8"))
    report: dict[str, Any] = {"live_cases": sorted(cases), "modules": {}}
    if not cases:
        report["note"] = ("No demo case was prepared by a live model, so nothing can be measured. Replay a family's "
                          "story with Gemini first (“Watch it from the first paper” in the case, or `afterledger "
                          "seed` with AL_LLM=gemini).")
        return report
    fitted_any = False
    for module, items in (("reading", await reading_items(deps, cases)),
                          ("discovery", await discovery_items(deps, cases))):
        tables[module], rows = refit(tables[module], items)
        fitted_any = fitted_any or any(r["fitted"] for r in rows)
        report["modules"][module] = {"items": len(items), "bins": rows}
    if write and fitted_any:
        tables["_status"] = "fitted"
        tables["_note"] = (f"Refitted on {date.today().isoformat()} from the demo cases a live model prepared "
                           f"({', '.join(sorted(cases))}). A bin with at least {MIN_ITEMS} items holds its measured "
                           "accuracy, (right + 1) / (items + 2); a smaller bin keeps the value set by hand, and n says "
                           "how many items it had. `afterledger calibrate` shows what a new refit would change.")
        tables["_fitted"] = {"on": date.today().isoformat(), "live_cases": sorted(cases),
                             "items": {m: r["items"] for m, r in report["modules"].items()}, "min_items": MIN_ITEMS}
        path.write_text(json.dumps(tables, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        report["written"] = str(path)
    return report


def print_report(report: dict) -> None:
    if report.get("note"):
        print(report["note"])
        return
    print(f"Live cases: {', '.join(report['live_cases'])}")
    for module, m in report["modules"].items():
        print(f"\n{module}: {m['items']} items")
        for r in m["bins"]:
            sig = ", ".join(f"{k}={v}" for k, v in r["signals"].items()) or "anything else (the base rate)"
            measured = f"{r['right']}/{r['items']}" if r["items"] else "no items"
            change = f"{r['start']} -> {r['p']}" if r["fitted"] else f"{r['start']} kept (needs {MIN_ITEMS} items)"
            print(f"  {sig:55s} {measured:>10s}  {change}")
    if report.get("written"):
        print(f"\nWrote {report['written']}")
