"""Targeted diagnostic checks: additional, on-demand observations the
orchestrator can request when the top two hypotheses are too close to call.

There are no live devices here -- a "check" is a pre-authored file
(<checks_dir>/<device>__<metric>.json, same Schema A format as regular
input) representing what a human or script would obtain by proactively
re-polling that device right now. If no matching file exists, the
orchestrator records that plainly and moves on; a check never fabricates
data, and it never lowers a threshold to force a cause.
"""
from __future__ import annotations

from pathlib import Path

from . import ingest
from .evidence import EvidenceStore


def check_path(checks_dir: Path | str, device: str, metric: str) -> Path:
    return Path(checks_dir) / f"{device}__{metric}.json"


def perform(store: EvidenceStore, checks_dir: Path | str, device: str, metric: str) -> tuple[bool, str]:
    """Loads the check file for (device, metric) into `store`, if one exists.

    Returns (applied, result_text). `applied` is False if there was no matching
    check file, or the file existed but every record in it was rejected.
    """
    path = check_path(checks_dir, device, metric)
    if not path.exists():
        return False, f"no check available for {metric} on {device}"
    before = len(store.observations)
    rejected = ingest.load_file(store, path)
    added = len(store.observations) - before
    if added == 0:
        reason = rejected[0]["reason"] if rejected else "produced no usable observations"
        return False, f"check file for {metric} on {device} was rejected: {reason}"
    return True, f"applied a fresh reading from {path.name} ({added} observation(s))"
