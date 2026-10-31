"""Generic adapter driven by declarative YAML mapping files (netcoop/mappings/*.yaml).

Adding a new vendor format means adding a mapping file, not hand-written
if-statements. See mappings/vendor_b.yaml for the mapping schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..metrics import MetricError, normalize_metric
from ..protocol import Observation
from ..units import UnitError

MAPPINGS_DIR = Path(__file__).resolve().parent.parent / "mappings"


def load_mapping(name: str) -> dict:
    """Load a mapping by name (e.g. "vendor_b") or explicit path to a .yaml file."""
    path = Path(name) if name.endswith(".yaml") else MAPPINGS_DIR / f"{name}.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_mapping_for_schema(tag: str) -> dict | None:
    """Scan netcoop/mappings/*.yaml for one whose schema name (ignoring version) matches `tag`."""
    name = tag.partition("/")[0]
    for path in sorted(MAPPINGS_DIR.glob("*.yaml")):
        candidate = load_mapping(str(path))
        cand_name = str(candidate.get("schema", "")).partition("/")[0]
        if cand_name == name:
            return candidate
    return None


def _check_schema(rec_schema: Any, expected: str) -> str | None:
    """Returns an error message, or None if the record's schema tag is acceptable."""
    if not isinstance(rec_schema, str):
        return f"missing or invalid schema tag {rec_schema!r}"
    exp_name, _, exp_version = expected.partition("/")
    name, _, version = rec_schema.partition("/")
    if name != exp_name:
        return f"unknown schema {rec_schema!r} (expected {exp_name!r})"
    if version != exp_version:
        return f"unsupported {name} version {version!r} (expected {exp_version!r})"
    return None


def _parse_timestamp(raw: Any, fmt: str) -> datetime:
    if fmt == "epoch_s":
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    if fmt == "iso8601":
        s = raw[:-1] + "+00:00" if isinstance(raw, str) and raw.endswith("Z") else raw
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            raise ValueError("naive timestamp (refusing to guess a timezone)")
        return dt.astimezone(timezone.utc)
    raise ValueError(f"unknown timestamp_format {fmt!r}")


def parse(payload: dict[str, Any] | list[dict[str, Any]], mapping: dict, source: str
          ) -> tuple[list[Observation], list[tuple[str, str]]]:
    """Returns (observations, rejected) where rejected is [(raw_ref, reason), ...].

    raw_ref format: "<source>#<record index>.<field>"
    """
    records = payload if isinstance(payload, list) else [payload]
    device_field = mapping["device_field"]
    ts_field = mapping["timestamp_field"]
    ts_format = mapping.get("timestamp_format", "iso8601")
    field_map: dict[str, dict] = mapping.get("fields", {})
    status_field = mapping.get("status_field")
    expected_schema = mapping.get("schema")

    observations: list[Observation] = []
    rejected: list[tuple[str, str]] = []

    for i, rec in enumerate(records):
        raw_ref_base = f"{source}#{i}"
        if expected_schema is not None:
            schema_error = _check_schema(rec.get("schema"), expected_schema)
            if schema_error is not None:
                rejected.append((raw_ref_base, schema_error))
                continue
        try:
            device = rec[device_field]
            ts = _parse_timestamp(rec[ts_field], ts_format)
        except Exception as e:
            rejected.append((raw_ref_base, f"malformed record: {e}"))
            continue

        for field, field_spec in field_map.items():
            if field not in rec:
                continue
            raw_ref = f"{raw_ref_base}.{field}"
            metric = field_spec["metric"]
            unit = field_spec.get("unit")
            try:
                value, out_unit = normalize_metric(metric, rec[field], unit)
            except (MetricError, UnitError) as e:
                rejected.append((raw_ref, str(e)))
                continue
            observations.append(Observation(
                device=device, metric=metric, value=value, unit=out_unit,
                timestamp=ts, source=source, raw_ref=raw_ref, original_unit=unit,
            ))

        if status_field and status_field in rec:
            raw_ref = f"{raw_ref_base}.{status_field}"
            try:
                value, out_unit = normalize_metric("device_status", rec[status_field], None)
            except MetricError as e:
                rejected.append((raw_ref, str(e)))
            else:
                observations.append(Observation(
                    device=device, metric="device_status", value=value, unit=out_unit,
                    timestamp=ts, source=source, raw_ref=raw_ref,
                ))

    return observations, rejected
