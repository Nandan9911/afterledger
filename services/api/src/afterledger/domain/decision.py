"""The decision controller and research-depth choice. Plain code with measured inputs:
the probabilities come from calibration tables, the costs from recent traces."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Move = Literal["act", "ask", "research", "wait", "hand_off"]
Depth = Literal["recall", "quick", "standard", "deep"]

BAR = {"A": 0.95, "B": 0.80, "C": 0.60}            # starting points, tuned on the test set
COST_ASK = 0.05                                      # in units of "stakes"; the family's effort
COST_PER_RESEARCH_MINUTE = 0.01
COST_PER_WAIT_DAY = 0.02


@dataclass(frozen=True)
class Fork:
    tier: Literal["A", "B", "C"]
    p_now: float                       # calibrated probability the best option is right today
    p_if_asked: float                  # expected after the family answers (if they can)
    p_if_researched: float             # expected after research, from similar past runs
    research_minutes: float            # expected, from recent traces
    stakes: float                      # 0..1: money, deadline and paper consequences, normalised
    family_can_answer: bool = False
    days_to_deadline: float | None = None
    event_due_days: float | None = None    # a reply or document already on its way
    p_if_waited: float = 0.0
    needs_professional: bool = False


def choose(f: Fork) -> tuple[Move, dict[str, float]]:
    """Pick act, ask, research, wait or hand off, and return the scores for the trace."""
    bar = BAR[f.tier]
    if f.needs_professional:
        return "hand_off", {}
    if f.p_now >= bar:
        return "act", {"act": f.p_now}
    urgency = 1.0 if f.days_to_deadline is None else min(4.0, 14 / max(f.days_to_deadline, 1))
    reach = {"ask": f.p_if_asked if f.family_can_answer else 0.0,
             "research": f.p_if_researched,
             "wait": f.p_if_waited if f.event_due_days is not None else 0.0}
    cost = {"ask": COST_ASK,
            "research": COST_PER_RESEARCH_MINUTE * f.research_minutes * urgency,
            "wait": COST_PER_WAIT_DAY * (f.event_due_days or 0) * urgency}
    candidates = {m: p for m, p in reach.items() if p > f.p_now}
    if f.tier == "A":                  # only moves that can make a tier A value safe count
        candidates = {m: p for m, p in candidates.items() if p >= bar}
    scores = {m: round((p - f.p_now) * f.stakes - cost[m], 4) for m, p in candidates.items()}
    scores = {m: s for m, s in scores.items() if s > 0}
    if not scores:
        return ("hand_off" if f.tier == "A" else "act"), scores
    return max(scores, key=scores.get), scores


def research_depth(tier: str, stored_level: str | None, fresh: bool, thin_coverage: bool) -> Depth:
    """How deep the research service goes for one question."""
    strong = {"dual_verified", "adjudicated", "outcome_confirmed", "gold"}
    if fresh and stored_level in strong:
        return "recall"
    if fresh and stored_level == "sourced" and tier != "A":
        return "recall"
    if tier == "A":
        return "deep" if thin_coverage else "standard"
    return "quick"
