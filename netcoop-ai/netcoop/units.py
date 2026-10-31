"""Canonical unit conversion. Ambiguous or unknown units are rejected, never guessed."""
from __future__ import annotations

CANONICAL = {"latency": "ms", "jitter": "ms", "throughput": "Mbps", "bandwidth": "Mbps",
             "packet_loss": "pct", "cpu": "pct", "memory": "pct", "rssi": "dBm",
             "link_utilization": "pct", "retransmissions": "pct", "dns_failure": "pct"}

# Cumulative counts with no time window: unitless, reported as an absolute count, never a rate.
COUNT_METRICS = {"queue_drops", "interface_errors", "dhcp_failures"}

# Enum/string state, not numeric.
CATEGORICAL_METRICS = {"device_status"}

_FACTORS = {
    "ms": {"ms": 1, "s": 1000, "us": 0.001},
    "Mbps": {"Mbps": 1, "Kbps": 0.001, "bps": 1e-6, "Gbps": 1000},
    "pct": {"pct": 1, "%": 1, "percent": 1},
    "dBm": {"dBm": 1},
}


class UnitError(ValueError):
    pass


def normalize(metric: str, value: float, unit: str | None) -> tuple[float, str]:
    if metric not in CANONICAL:
        raise UnitError(f"unknown metric {metric!r}")
    target = CANONICAL[metric]
    if unit is None:
        raise UnitError(f"{metric}: missing unit (refusing to assume {target})")
    if unit == "ratio":
        # 0.05 vs 5 is genuinely ambiguous across vendors only if unlabelled; labelled ratio is safe.
        if target != "pct" or not 0 <= value <= 1:
            raise UnitError(f"{metric}: ratio value {value} out of [0,1] or not a percentage metric")
        return value * 100, target
    factor = _FACTORS[target].get(unit)
    if factor is None:
        raise UnitError(f"{metric}: unit {unit!r} not convertible to {target}")
    return value * factor, target


def normalize_count(metric: str, value: float, unit: str | None = None) -> tuple[float, None]:
    """Validate a cumulative count: no unit, non-negative, numeric (not bool)."""
    if metric not in COUNT_METRICS:
        raise UnitError(f"{metric}: not a registered count metric")
    if unit not in (None, "count"):
        raise UnitError(f"{metric}: expected a bare count, got unit {unit!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnitError(f"{metric}: invalid count value {value!r}")
    if value < 0:
        raise UnitError(f"{metric}: count cannot be negative ({value!r})")
    return float(value), None


def normalize_categorical(metric: str, value: object) -> tuple[str, None]:
    """Validate an enum/string state value."""
    if metric not in CATEGORICAL_METRICS:
        raise UnitError(f"{metric}: not a registered categorical metric")
    if value is None or (isinstance(value, str) and not value.strip()):
        raise UnitError(f"{metric}: missing categorical value")
    return str(value), None
