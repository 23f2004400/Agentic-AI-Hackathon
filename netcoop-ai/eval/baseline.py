"""Naive baseline for comparison in eval/run_eval.py: diagnoses purely from
the most recent device_status observation in a file, with no correlation, no
evidence weighting, no coverage check, and no staleness handling.

This is exactly the shortcut CLAUDE.md says the real pipeline must never
take -- "the generator's status field is a device-reported observation, NOT
ground truth and NOT a shortcut to the diagnosis." It exists only so
eval/run_eval.py can show how much the rules-based correlation pipeline
adds over trusting a single self-reported field.
"""
from __future__ import annotations

from pathlib import Path

from netcoop import ingest
from netcoop.evidence import EvidenceStore

STATUS_TO_CAUSE = {
    "Critical": "Hardware failure",
    "Warning": "Unspecified fault (status=Warning)",
    "Healthy": "No fault detected",
}


def diagnose(path: Path) -> dict:
    """Returns {"status", "suspected_root_cause"} using only whichever
    device_status reading happens to be newest in the file -- no per-device
    scoping, no grounding, no confidence formula."""
    store = EvidenceStore()
    ingest.load_file(store, path)
    status_obs = [o for o in store.observations.values() if o.metric == "device_status"]
    if not status_obs:
        return {"status": "insufficient_evidence", "suspected_root_cause": None}
    latest = max(status_obs, key=lambda o: o.timestamp)
    if latest.value not in STATUS_TO_CAUSE:
        return {"status": "insufficient_evidence", "suspected_root_cause": None}
    return {"status": "diagnosed", "suspected_root_cause": STATUS_TO_CAUSE[latest.value]}
