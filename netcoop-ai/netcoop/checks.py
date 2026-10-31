"""Targeted diagnostic checks: named, specific diagnostic actions the
orchestrator can request when the top two hypotheses are too close to call.

Each check is declared in rules.yaml's `checks` section -- a name, the
metric it re-polls, and which hypothesis pairs it's able to discriminate
between (`discriminating_check`). There are no live devices here: a check is
a pre-authored file at <checks_dir>/<check_name>.json, ingested through the
exact same ingest/validation path as ordinary input, representing what a
human or script would obtain by actually running that check right now.

If no file exists for the requested check (or the file is rejected outright
by ingest), the outcome is `check_failed`: recorded plainly, and diagnosis
continues with whatever evidence is already on hand. A check never
fabricates data or lowers a threshold to manufacture a conclusion.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import ingest
from .evidence import EvidenceStore
from .rules import load_rules

RULES = load_rules()
CHECK_DEFINITIONS: dict[str, dict] = RULES.get("checks", {})


@dataclass(frozen=True)
class CheckOutcome:
    name: str
    status: str  # "applied" | "check_failed"
    result: str
    observations_added: int = 0


def check_path(checks_dir: Path | str, check_name: str) -> Path:
    return Path(checks_dir) / f"{check_name}.json"


def discriminating_check(hyp_a: str, hyp_b: str) -> str | None:
    """The first named check in rules.yaml whose `discriminates` list contains
    the unordered pair {hyp_a, hyp_b}, or None if none is declared for it."""
    pair = {hyp_a, hyp_b}
    for name, spec in CHECK_DEFINITIONS.items():
        for d_pair in spec.get("discriminates", []):
            if set(d_pair) == pair:
                return name
    return None


def perform(store: EvidenceStore, checks_dir: Path | str, check_name: str) -> CheckOutcome:
    """Loads <checks_dir>/<check_name>.json into `store` via the normal
    ingest/validation path, if it exists and yields usable observations."""
    path = check_path(checks_dir, check_name)
    if not path.exists():
        return CheckOutcome(check_name, "check_failed", f"no check file found for {check_name!r}")
    before = len(store.observations)
    rejected = ingest.load_file(store, path)
    added = len(store.observations) - before
    if added == 0:
        reason = rejected[0]["reason"] if rejected else "produced no usable observations"
        return CheckOutcome(check_name, "check_failed", f"check {check_name!r} was rejected: {reason}")
    return CheckOutcome(check_name, "applied", f"applied {added} observation(s) from {path.name}",
                         observations_added=added)
