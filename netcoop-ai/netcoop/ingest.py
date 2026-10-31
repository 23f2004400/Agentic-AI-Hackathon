"""Format-detecting ingestion: identify which adapter a raw payload belongs to,
normalize it into canonical Observations, and load those into an EvidenceStore.

A file with some bad fields still yields whatever observations are good;
nothing is dropped silently, every rejection carries a reason.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import mapped, schema_a
from .evidence import EvidenceStore
from .protocol import Observation


def detect_and_parse(payload: Any, source: str) -> tuple[list[Observation], list[dict[str, str]]]:
    """Detect the schema of `payload` and parse it into canonical Observations.

    Returns (observations, rejected) where each rejected item is
    {"raw_ref": ..., "reason": ...}.
    """
    records = payload if isinstance(payload, list) else [payload]
    if not records:
        return [], []

    sample = records[0]
    if not isinstance(sample, dict):
        return [], [{"raw_ref": f"{source}#0", "reason": "record is not a JSON object"}]

    if "device_id" in sample and "metrics" in sample:
        obs, rejected = schema_a.parse(payload, source=source)
    elif "schema" in sample:
        tag = sample.get("schema")
        mapping = mapped.find_mapping_for_schema(tag) if isinstance(tag, str) else None
        if mapping is None:
            return [], [{"raw_ref": f"{source}#0", "reason": f"unknown schema {tag!r}"}]
        obs, rejected = mapped.parse(payload, mapping, source=source)
    else:
        return [], [{"raw_ref": f"{source}#0",
                      "reason": "unrecognized input format: no device_id/metrics and no schema tag"}]

    return obs, [{"raw_ref": r, "reason": reason} for r, reason in rejected]


def load_into_store(store: EvidenceStore, payload: Any, source: str) -> list[dict[str, str]]:
    """Parse `payload` and add the resulting observations to `store`.

    Returns the combined rejection list: schema/field-level rejections from
    parsing, plus store-level rejections (unknown device, duplicate) from
    this call only.
    """
    obs, rejected = detect_and_parse(payload, source)
    before = len(store.rejected)
    for o in obs:
        store.add_observation(o)
    rejected += [{"raw_ref": r, "reason": reason} for r, reason in store.rejected[before:]]
    return rejected


def load_file(store: EvidenceStore, path: str | Path) -> list[dict[str, str]]:
    """Read a JSON file and load it into `store`. Malformed JSON is reported, not raised."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return [{"raw_ref": str(path), "reason": f"cannot read file: {e}"}]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        return [{"raw_ref": str(path), "reason": f"malformed JSON: {e}"}]
    return load_into_store(store, payload, source=path.name)
