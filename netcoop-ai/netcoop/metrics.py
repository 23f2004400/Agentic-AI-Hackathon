"""Canonical metric registry: every metric name the system understands, its
canonical unit (if any), and its kind. Validation/conversion logic itself
lives in units.py (normalize / normalize_count / normalize_categorical);
this module is the registry that tells callers which one to use.

kind:
  gauge       - unit-bearing point-in-time measurement, convertible via units.normalize
  count       - cumulative count with no time window; unitless, reported as-is
  categorical - an enum/string state (e.g. device_status), not numeric
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from . import units

Kind = Literal["gauge", "count", "categorical"]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: Kind
    unit: str | None = None  # canonical unit for gauge metrics; None otherwise


class MetricError(ValueError):
    pass


REGISTRY: dict[str, MetricSpec] = {}
for _name, _unit in units.CANONICAL.items():
    REGISTRY[_name] = MetricSpec(name=_name, kind="gauge", unit=_unit)
for _name in units.COUNT_METRICS:
    REGISTRY[_name] = MetricSpec(name=_name, kind="count")
for _name in units.CATEGORICAL_METRICS:
    REGISTRY[_name] = MetricSpec(name=_name, kind="categorical")


def known(name: str) -> bool:
    return name in REGISTRY


def normalize_metric(name: str, value: Any, unit: str | None) -> tuple[Any, str | None]:
    """Validate + normalize a raw (name, value, unit) triple per the metric's kind.

    Raises MetricError (unknown metric) or units.UnitError (bad value/unit);
    never guesses a missing or ambiguous unit.
    """
    spec = REGISTRY.get(name)
    if spec is None:
        raise MetricError(f"unknown metric {name!r}")
    if spec.kind == "gauge":
        return units.normalize(name, value, unit)
    if spec.kind == "count":
        return units.normalize_count(name, value, unit)
    if spec.kind == "categorical":
        return units.normalize_categorical(name, value)
    raise MetricError(f"{name}: unhandled kind {spec.kind!r}")
