"""Loads netcoop/rules.yaml and evaluates its threshold conditions. Shared by
the analysis agents (per-metric Findings) and the correlation agent
(hypothesis scoring) so there is exactly one place that reads a threshold.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

RULES_PATH = Path(__file__).resolve().parent / "rules.yaml"

_OPS = {
    ">": lambda v, t: v > t,
    ">=": lambda v, t: v >= t,
    "<": lambda v, t: v < t,
    "<=": lambda v, t: v <= t,
    "==": lambda v, t: v == t,
    "!=": lambda v, t: v != t,
}


def load_rules(path: Path | str = RULES_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def evaluate_condition(value: Any, op: str, threshold: Any) -> bool:
    fn = _OPS.get(op)
    if fn is None:
        raise ValueError(f"unknown comparison operator {op!r}")
    try:
        return bool(fn(value, threshold))
    except TypeError:
        # e.g. a categorical value compared with a numeric operator: doesn't fire.
        return False
