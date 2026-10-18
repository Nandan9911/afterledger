"""Loads the demo families' truth files (data/families/<key>/family.yaml and papers.yaml),
shifted to the chosen story day, and renders their papers on demand (cached per story day)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import get_settings
from .papers.build import STORY_DAY_0, BuiltPaper, build_paper, shift_tree


@dataclass
class Family:
    key: str
    story_day: date
    shift: int
    data: dict
    papers: dict[str, dict]
    _built: dict[str, BuiltPaper] = field(default_factory=dict)

    # ---- convenience ----------------------------------------------------------------------
    def __getitem__(self, k: str) -> Any:
        return self.data[k]

    def get(self, k: str, default: Any = None) -> Any:
        return self.data.get(k, default)

    @property
    def deceased(self) -> dict:
        return self.data["deceased"]

    @property
    def people(self) -> list[dict]:
        return self.data.get("people", [])

    def person(self, pid: str) -> dict | None:
        if pid == self.deceased["id"]:
            return self.deceased
        return next((p for p in self.people if p["id"] == pid), None)

    def holding(self, hid: str) -> dict | None:
        return next((h for h in self.data.get("holdings", []) if h["id"] == hid), None)

    def claim(self, cid: str) -> dict | None:
        return next((c for c in self.data.get("claims", []) if c["id"] == cid), None)

    def paper(self, pid: str) -> dict:
        return self.papers[pid]

    def built(self, paper_id: str) -> BuiltPaper:
        if paper_id not in self._built:
            seed = sum(ord(c) for c in f"{self.key}/{paper_id}") % 997
            self._built[paper_id] = build_paper(self.papers[paper_id], seed=seed)
        return self._built[paper_id]

    def lead(self, key: str) -> dict | None:
        return next((x for x in self.data.get("leads", []) if x["key"] == key), None)

    def answer(self, name: str) -> Any:
        return (self.data.get("answers") or {}).get(name)


def families_dir() -> Path:
    return get_settings().families_dir


def family_keys() -> list[str]:
    d = families_dir()
    keys = [p.name for p in sorted(d.iterdir()) if (p / "family.yaml").exists()] if d.is_dir() else []
    orders = {k: (yaml.safe_load((d / k / "family.yaml").read_text(encoding="utf-8")) or {}).get("order", 99)
              for k in keys}
    return sorted(keys, key=lambda k: (orders[k], k))


@lru_cache(maxsize=32)
def load_family(key: str, story_day: date | None = None) -> Family:
    d = families_dir() / key
    raw = yaml.safe_load((d / "family.yaml").read_text(encoding="utf-8"))
    papers_raw = {}
    if (d / "papers.yaml").exists():
        papers_raw = {p["id"]: p for p in (yaml.safe_load((d / "papers.yaml").read_text(encoding="utf-8")) or {}).get(
            "papers", [])}
    base_day = raw.get("story_day")
    base_day = base_day if isinstance(base_day, date) else date.fromisoformat(str(base_day or STORY_DAY_0))
    day = story_day or base_day
    shift = (day - base_day).days
    data = shift_tree(_dates_to_str(raw), shift)
    papers = {k: shift_tree(_dates_to_str(v), shift) for k, v in papers_raw.items()}
    data["story_day"] = day.isoformat()
    return Family(key=key, story_day=day, shift=shift, data=data, papers=papers)


def _dates_to_str(obj: Any) -> Any:
    if isinstance(obj, dict):
        # YAML 1.1 reads a bare 'on:' key as True; the timeline means the word
        return {("on" if k is True else k): _dates_to_str(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dates_to_str(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
