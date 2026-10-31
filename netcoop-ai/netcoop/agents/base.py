"""Agent interface shared by the per-device analysis agents (Performance,
Infrastructure, ...). An agent inspects raw observations in the shared
EvidenceStore and proposes Findings; it never decides its own grounding --
handle() hands every Finding to the store, which grounds it from what it
actually has on record.

Correlation and Reporting are a different shape of agent (they consume other
agents' Findings, not just raw observations) and are defined in their own
modules rather than forced into this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding, MsgType, Observation


def latest_observation(store: EvidenceStore, device: str, metric: str) -> Observation | None:
    """The most recent observation for (device, metric), or None if there isn't one
    or the only one(s) available are stale.

    "Now" is the latest timestamp seen anywhere in the store, not wall-clock time:
    scenario files have fixed timestamps, and staleness should reflect how old a
    reading is relative to the rest of the incident's telemetry, not whenever
    someone happens to run the CLI.
    """
    candidates = [o for o in store.observations.values() if o.device == device and o.metric == metric]
    if not candidates:
        return None
    latest = max(candidates, key=lambda o: o.timestamp)
    if store.observations:
        reference = max(o.timestamp for o in store.observations.values())
        if reference - latest.timestamp > store.max_age:
            return None
    return latest


class Agent(ABC):
    name: str
    required_metrics: tuple[str, ...] = ()

    @abstractmethod
    def analyze(self, device: str, store: EvidenceStore) -> list[Finding]:
        """Return ungrounded Findings for one device. evidence_refs must cite obs_ids."""
        ...

    def handle(self, task: Envelope, store: EvidenceStore) -> Envelope:
        devices: list[str] = task.payload.get("devices", [])
        findings: list[Finding] = []
        missing: list[str] = []
        for device in devices:
            findings.extend(self.analyze(device, store))
            for metric in self.required_metrics:
                if latest_observation(store, device, metric) is None:
                    missing.append(f"{device}:{metric}")
        grounded = [store.ground(f) for f in findings]
        return Envelope(
            incident_id=task.incident_id, sender=self.name, recipient=task.sender,
            type=MsgType.FINDINGS, findings=grounded, missing=missing,
        )
