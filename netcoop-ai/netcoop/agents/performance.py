"""Telemetry and Performance agent: latency, jitter, packet loss, throughput,
bandwidth, and link utilization. Flags a Finding for each rules.yaml signal
it owns that fires against the device's latest observations.
"""
from __future__ import annotations

from ..evidence import EvidenceStore
from ..protocol import Finding
from ..rules import evaluate_condition, load_rules
from .base import Agent, latest_observation

_RULES = load_rules()
_SIGNALS = [s for s in _RULES.get("signals", []) if s.get("agent") == "performance_agent"]


class PerformanceAgent(Agent):
    name = "performance_agent"
    required_metrics = ("latency", "packet_loss", "link_utilization")

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
