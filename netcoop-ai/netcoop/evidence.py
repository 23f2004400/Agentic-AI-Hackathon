"""EvidenceStore: single source of truth for observations and grounded findings."""
from __future__ import annotations

from datetime import datetime, timedelta

from .protocol import Finding, Observation


class EvidenceStore:
    def __init__(self, known_devices: set[str] | None = None, max_age: timedelta = timedelta(minutes=10)):
        self.observations: dict[str, Observation] = {}
        self.findings: dict[str, Finding] = {}
        self.known_devices = known_devices
        self.max_age = max_age
        self.rejected: list[tuple[str, str]] = []   # (raw_ref, reason)
        self._seen_keys: set[tuple] = set()

    # ---- observations -------------------------------------------------
    def add_observation(self, obs: Observation) -> bool:
        if self.known_devices is not None and obs.device not in self.known_devices:
            self.rejected.append((obs.raw_ref, f"unknown device {obs.device!r}"))
            return False
        key = (obs.device, obs.metric, obs.timestamp, obs.value)
        if key in self._seen_keys:
            self.rejected.append((obs.raw_ref, "duplicate observation"))
            return False
        self._seen_keys.add(key)
        self.observations[obs.obs_id] = obs
        return True

    def is_stale(self, obs_id: str, now: datetime) -> bool:
        return now - self.observations[obs_id].timestamp > self.max_age

    # ---- findings -----------------------------------------------------
    def ground(self, f: Finding) -> Finding:
        """Assign grounding from the store, ignoring whatever the sender claimed.

        observed   : cites >=1 stored observation directly
        inferred   : cites only findings that are themselves observed/inferred
        unsupported: cites nothing resolvable, or names a device this store
                     doesn't recognize at all (even if its evidence_refs resolve)
        """
        if self.known_devices is not None and f.device is not None and f.device not in self.known_devices:
            grounded = f.model_copy(update={"grounding": "unsupported"})
            self.findings[grounded.finding_id] = grounded
            return grounded
        obs_hits = [r for r in f.evidence_refs if r in self.observations]
        fnd_hits = [r for r in f.evidence_refs
                    if r in self.findings and self.findings[r].grounding != "unsupported"]
        if obs_hits:
            g = "observed"
        elif fnd_hits:
            g = "inferred"
        else:
            g = "unsupported"
        grounded = f.model_copy(update={"grounding": g})
        self.findings[grounded.finding_id] = grounded
        return grounded

    def trace(self, ref: str, _depth: int = 0) -> list[str]:
        """Resolve a finding/observation back to the raw records it rests on."""
        if _depth > 20:
            return []
        if ref in self.observations:
            return [self.observations[ref].raw_ref]
        if ref in self.findings:
            out: list[str] = []
            for r in self.findings[ref].evidence_refs:
                out.extend(self.trace(r, _depth + 1))
            return sorted(set(out))
        return []
