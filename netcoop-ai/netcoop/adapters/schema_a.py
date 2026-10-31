"""Adapter for the official generator format ("Schema A"):

{"timestamp": "...Z", "device_id": "SW_CAMPUS_4",
 "metrics": {"cpu_utilization_pct": 99.9, ...}, "status": "Critical"}

Input may be a single record or a list of records. A metric field's unit is
derived from its name suffix via an explicit table, not hardcoded per field;
a field whose suffix or base name isn't recognized is rejected with a reason
instead of guessed. "status" becomes a device_status observation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..metrics import MetricError, normalize_metric
from ..protocol import Observation
from ..units import UnitError

# field-name suffix -> canonical unit
SUFFIX_UNITS: dict[str, str] = {
    "pct": "pct",
    "ms": "ms",
    "mbps": "Mbps",
    "dbm": "dBm",
}

# field name with its unit suffix stripped -> canonical metric name
METRIC_BASE_NAMES: dict[str, str] = {
    "cpu_utilization": "cpu",
    "link_utilization": "link_utilization",
    "packet_loss": "packet_loss",
    "latency": "latency",
    "jitter": "jitter",
    "throughput": "throughput",
    "bandwidth": "bandwidth",
    "memory_utilization": "memory",
    "rssi": "rssi",
    "retransmission": "retransmissions",
    "dns_failure": "dns_failure",
}

# fields with no unit suffix: bare counts, reported as absolute counts not rates
COUNT_FIELDS: dict[str, str] = {
    "queue_drops": "queue_drops",
    "interface_errors": "interface_errors",
    "dhcp_failures": "dhcp_failures",
}


def _parse_timestamp(raw: str) -> datetime:
    s = raw[:-1] + "+00:00" if isinstance(raw, str) and raw.endswith("Z") else raw
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        raise ValueError("naive timestamp (refusing to guess a timezone)")
    return dt.astimezone(timezone.utc)


def _resolve_field(field: str) -> tuple[str, str | None] | None:
    """Returns (canonical_metric, canonical_unit) for a field, or None if unrecognized."""
    if field in COUNT_FIELDS:
        return COUNT_FIELDS[field], None
    base, _, suffix = field.rpartition("_")
    unit = SUFFIX_UNITS.get(suffix)
    if unit is None:
        return None
    metric = METRIC_BASE_NAMES.get(base)
    if metric is None:
        return None
    return metric, unit


def parse(payload: dict[str, Any] | list[dict[str, Any]], source: str = "schema_a"
          ) -> tuple[list[Observation], list[tuple[str, str]]]:
    """Returns (observations, rejected) where rejected is [(raw_ref, reason), ...].

    raw_ref format: "<source>#<record index>.<field>"
    """
    records = payload if isinstance(payload, list) else [payload]
    observations: list[Observation] = []
    rejected: list[tuple[str, str]] = []

    for i, rec in enumerate(records):
        raw_ref_base = f"{source}#{i}"
        try:
            device = rec["device_id"]
            ts = _parse_timestamp(rec["timestamp"])
        except Exception as e:
            rejected.append((raw_ref_base, f"malformed record: {e}"))
            continue

        for field, raw_value in rec.get("metrics", {}).items():
            raw_ref = f"{raw_ref_base}.{field}"
            resolved = _resolve_field(field)
            if resolved is None:
                _, _, suffix = field.rpartition("_")
                if suffix not in SUFFIX_UNITS:
                    rejected.append((raw_ref, f"unrecognized unit suffix in field {field!r}"))
                else:
                    rejected.append((raw_ref, f"unrecognized field {field!r}"))
                continue
            metric, unit = resolved
            try:
                value, out_unit = normalize_metric(metric, raw_value, unit)
            except (MetricError, UnitError) as e:
                rejected.append((raw_ref, str(e)))
                continue
            observations.append(Observation(
                device=device, metric=metric, value=value, unit=out_unit,
                timestamp=ts, source=source, raw_ref=raw_ref, original_unit=unit,
            ))

        if "status" in rec:
            raw_ref = f"{raw_ref_base}.status"
            try:
                value, out_unit = normalize_metric("device_status", rec["status"], None)
            except MetricError as e:
                rejected.append((raw_ref, str(e)))
            else:
                observations.append(Observation(
                    device=device, metric="device_status", value=value, unit=out_unit,
                    timestamp=ts, source=source, raw_ref=raw_ref,
                ))

    return observations, rejected
