"""Confidence labels come from measured accuracy, never from a model's own say-so.

A calibration table per module and item type maps raw signals to a probability that was
measured on the test set. The starting tables live in data/knowledge/calibration.json and are
refitted by the evaluation harness (afterledger.eval.calibrate).
"""
from __future__ import annotations

import json
from pathlib import Path

from .case import Certainty

SURE_AT = 0.95       # "Sure" is shown at 95% measured accuracy or above
CHECK_AT = 0.80      # "Please check" from 80%; below that the system asks or abstains


def value_certainty(p: float) -> Certainty:
    if p >= SURE_AT:
        return "sure"
    if p >= CHECK_AT:
        return "check"
    return "unknown"


def lead_label(p: float) -> str:
    if p >= SURE_AT:
        return "sure"
    if p >= 0.70:
        return "likely"
    if p >= 0.35:
        return "possible"
    return "unlikely"


class Calibrator:
    """Looks up the measured probability for an item's signals; unknown signals fall back to the
    module's base rate, which is deliberately conservative."""

    def __init__(self, tables: dict[str, dict]):
        self.tables = tables

    @classmethod
    def load(cls, path: Path) -> Calibrator:
        if path.exists():
            return cls(json.loads(path.read_text(encoding="utf-8")))
        return cls({})

    def probability(self, module: str, signals: dict[str, str | bool | int]) -> float:
        table = self.tables.get(module, {})
        best, best_score = None, -1
        for b in table.get("bins", []):
            keys = b["signals"]
            if all(str(signals.get(k)) == str(v) for k, v in keys.items()) and len(keys) > best_score:
                best, best_score = b, len(keys)
        if best is not None:
            return float(best["p"])
        return float(table.get("base_rate", 0.5))
