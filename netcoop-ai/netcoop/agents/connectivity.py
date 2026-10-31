"""Connectivity Analysis agent: topology scope (common upstream of the
affected devices), wired vs wireless segment mix, and DNS/DHCP service
failure detection (reusing the same signal mechanism as Performance and
Infrastructure for the metric-threshold part).

Without a topology, scope analysis can't go beyond "this one device" -- that
limitation is recorded as an Envelope assumption, not silently skipped.
"""
from __future__ import annotations

from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding
from ..rules import evaluate_condition, load_rules
from ..topology import Topology
from .base import Agent, latest_observation

_RULES = load_rules()
_SIGNALS = [s for s in _RULES.get("signals", []) if s.get("agent") == "connectivity_agent"]


class ConnectivityAgent(Agent):
    name = "connectivity_agent"
    required_metrics = ()  # DNS/DHCP signals are optional; scope analysis isn't metric-threshold-based

    def __init__(self, topology: Topology | None = None):
        self.topology = topology

    def analyze(self, device: str, store: EvidenceStore) -> list[Finding]:
        findings: list[Finding] = []
        for signal in _SIGNALS:
            obs = latest_observation(store, device, signal["metric"])
            if obs is None:
                continue
            if evaluate_condition(obs.value, signal["op"], signal["threshold"]):
                findings.append(Finding(
                    claim=signal["id"], device=device, evidence_refs=[obs.obs_id],
                    detail=signal.get("interpretation", signal["id"]),
                ))
        return findings

    def handle(self, task: Envelope, store: EvidenceStore) -> Envelope:
        envelope = super().handle(task, store)
        devices: list[str] = task.payload.get("devices", [])
        assumptions = list(envelope.assumptions)
        scope_findings: list[Finding] = []
        scope_payload: dict = {}

        if self.topology is None:
            assumptions.append("scope limited to reporting device: no topology was provided")
        elif not set(devices) <= self.topology.known_devices():
            unknown = sorted(set(devices) - self.topology.known_devices())
            assumptions.append(
                f"scope limited to reporting device: {unknown} not in the known topology"
            )
        else:
            wireless = [d for d in devices if self.topology.is_wireless(d)]
            wired = [d for d in devices if d not in wireless]
            if wireless and wired:
                scope_payload["segment_mix"] = "mixed"
            elif wireless:
                scope_payload["segment_mix"] = "wireless_only"
            elif wired:
                scope_payload["segment_mix"] = "wired_only"

            if len(devices) > 1:
                upstream = self.topology.common_upstream(devices)
                scope_payload["common_upstream"] = upstream
                if upstream is not None:
                    evidence_refs = [
                        obs.obs_id for d in devices
                        if (obs := latest_observation(store, d, "device_status")) is not None
                    ]
                    # A shared ancestor with no parent of its own is the root of the whole
                    # topology -- true of any two devices on campus, so it's not a
                    # meaningful localization, just "these are somewhere on the same network".
                    is_trivial_root = self.topology.parent_of(upstream) is None
                    if is_trivial_root:
                        detail = (f"{len(devices)} affected devices only share the network core "
                                  f"({upstream}); no narrower common segment was found, consistent "
                                  f"with unrelated faults rather than one shared cause")
                    else:
                        detail = (f"{len(devices)} affected devices share upstream node {upstream}; "
                                  f"consider {upstream} itself as a candidate root cause")
                    scope_findings.append(Finding(
                        claim="shared_upstream_scope", device=upstream,
                        evidence_refs=evidence_refs, detail=detail,
                    ))

        grounded_scope = [store.ground(f) for f in scope_findings]
        return envelope.model_copy(update={
            "findings": envelope.findings + grounded_scope,
            "assumptions": assumptions,
            "payload": {**envelope.payload, **scope_payload},
        })
