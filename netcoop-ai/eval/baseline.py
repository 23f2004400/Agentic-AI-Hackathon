"""Naive baseline for comparison in eval/run_eval.py: a single-pass threshold
classifier over a file's raw, most-recent Schema A record.

Deliberately has none of NetCoop's machinery:
  - no EvidenceStore / grounding -- reads metric values straight off the JSON
  - no topology -- no scope, no shared-upstream reasoning
  - no targeted checks -- one look, one answer
  - no abstention -- always commits to a specific cause (defaulting to "No
    fault detected" if nothing crosses a threshold), even when the evidence
    is thin or genuinely ambiguous

That last point is the interesting one: it reproduces the overconfidence
failure mode NetCoop's insufficient_evidence status exists to avoid, so
eval/run_eval.py's "false confident diagnoses" metric should be large for
this baseline and ~zero for the real pipeline.

This module is intentionally standalone (stdlib only) -- it does not import
netcoop -- to keep it a genuinely independent comparison point, not a
simplified copy of the real adapters.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

# (label, predicate) -- first match wins. Thresholds mirror CLAUDE.md's own
# diagnosis-rules table (link_util > 85%, CPU > 90%, RSSI < -70 dBm, ...),
# not anything reverse-engineered from ground_truth.json.
Metrics = dict[str, Any]
Rule = tuple[str, Callable[[Metrics], bool]]

RULES: list[Rule] = [
    ("Hardware failure",
     lambda m: m.get("interface_errors", 0) > 0 and m.get("cpu_utilization_pct", 0) > 90),
    ("Interface or cabling fault on a single link",
     lambda m: m.get("interface_errors", 0) > 0),
    ("Link congestion",
     lambda m: m.get("link_utilization_pct", 0) > 85),
    ("Device CPU overload",
     lambda m: m.get("cpu_utilization_pct", 0) > 90),
    ("Weak wireless signal",
     lambda m: m.get("rssi_dbm", 0) < -70),
    ("DNS or DHCP service failure",
     lambda m: m.get("dns_failure_pct", 0) > 5 or m.get("dhcp_failures", 0) > 0),
    ("Interface or cabling fault on a single link",
     lambda m: m.get("packet_loss_pct", 0) > 5),  # generic loss, naive catch-all guess
]
DEFAULT_LABEL = "No fault detected"


def _schema_a_records(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else [payload]
    return [r for r in records if isinstance(r, dict) and "device_id" in r and "metrics" in r]


def _latest(records: list[dict]) -> dict | None:
    if not records:
        return None
    return max(records, key=lambda r: r.get("timestamp", ""))  # ISO8601 sorts lexicographically


def diagnose(path: Path) -> dict:
    """Returns {"status", "suspected_root_cause", "affected_devices"}. Always
    "diagnosed" -- this classifier has no concept of insufficient evidence."""
    records = _schema_a_records(path)
    latest = _latest(records)
    if latest is None:
        return {"status": "diagnosed", "suspected_root_cause": DEFAULT_LABEL, "affected_devices": []}

    metrics = latest.get("metrics", {})
    device = latest.get("device_id")
    for label, predicate in RULES:
        if predicate(metrics):
            return {"status": "diagnosed", "suspected_root_cause": label, "affected_devices": [device]}
    return {"status": "diagnosed", "suspected_root_cause": DEFAULT_LABEL, "affected_devices": [device]}
