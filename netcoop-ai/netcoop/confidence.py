"""Confidence scoring: the documented formula, applied consistently everywhere
a confidence number or category is produced, so there is exactly one place
that implements it.

score    = clamp((support_weight - contradiction_weight) / possible_support_weight, 0, 1) * coverage
coverage = fraction of a hypothesis's required metrics that are present and fresh
margin   = top score - second-best score

category: High if score >= 0.75 and margin >= 0.2
          Medium if score >= 0.5 and margin >= 0.1
          Low otherwise

insufficient_evidence when coverage < 0.5, or score < 0.3, or margin < 0.1
(the margin check stands in for "...after checks are exhausted": checks.py,
the targeted-diagnostic-check loop, is not implemented, so the check budget
is always already at zero).

This is a heuristic, documented rule, not a calibrated probability.
"""
from __future__ import annotations

from dataclasses import dataclass

from .rules import load_rules

RULES = load_rules()
CONFIDENCE_RULES = RULES.get("confidence", {})

METHOD = (
    "score = clamp((support_weight - contradiction_weight) / possible_support_weight, 0, 1) "
    "* coverage, where weights come from netcoop/rules.yaml signals and coverage is the "
    "fraction of a hypothesis's required metrics present and fresh. Heuristic; not a "
    "calibrated probability."
)


@dataclass(frozen=True)
class Confidence:
    score: float
    category: str  # "high" | "medium" | "low"
    method: str = METHOD


def category_for(score: float, margin: float) -> str:
    high = CONFIDENCE_RULES.get("high", {"score": 0.75, "margin": 0.2})
    medium = CONFIDENCE_RULES.get("medium", {"score": 0.5, "margin": 0.1})
    if score >= high["score"] and margin >= high["margin"]:
        return "high"
    if score >= medium["score"] and margin >= medium["margin"]:
        return "medium"
    return "low"


def is_insufficient(score: float, coverage: float, margin: float) -> bool:
    cfg = CONFIDENCE_RULES.get("insufficient_evidence", {"coverage": 0.5, "score": 0.3, "margin": 0.1})
    return coverage < cfg["coverage"] or score < cfg["score"] or margin < cfg["margin"]


def evaluate(score: float, margin: float) -> Confidence:
    return Confidence(score=round(score, 3), category=category_for(score, margin))
