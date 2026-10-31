"""Fault Correlation agent: scores competing hypotheses from rules.yaml
directly against the raw observations in the EvidenceStore (not the other
agents' own claims), so the diagnosis never depends on an agent correctly
self-reporting. A hypothesis-level Finding cites the agent Findings whose
signal fired, so it grounds as "inferred" rather than "observed" -- an
inference built on observed evidence, never evidence itself.

This is a deliberately simple, transparent scorer (weighted rule support
minus contradiction, scaled by metric coverage) plus one explicit special
case -- the hardware_failure co-occurrence rule from CLAUDE.md -- rather than
a general hypothesis-vs-hypothesis conflict graph or targeted diagnostic
checks (checks.py), which are not built yet.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding, MsgType
from ..rules import evaluate_condition, load_rules
from .base import latest_observation

RULES = load_rules()
SIGNALS = RULES.get("signals", [])
HYPOTHESES = RULES.get("hypotheses", {})
CONFIDENCE_RULES = RULES.get("confidence", {})

_HW = HYPOTHESES.get("hardware_failure", {})
CO_OCCURRENCE_MIN = _HW.get("co_occurrence_min", 3)
CO_OCCURRENCE_DEMOTE = set(_HW.get("co_occurrence_demote", []))
CO_OCCURRENCE_DEMOTE_FACTOR = _HW.get("co_occurrence_demote_factor", 0.5)


@dataclass
class HypothesisResult:
    hypothesis: str
    label: str
    score: float
    coverage: float
    support: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)


def _agent_finding_for(agent_findings: list[Finding], device: str, claim: str) -> Finding | None:
    for f in agent_findings:
        if f.device == device and f.claim == claim:
            return f
    return None


def score_device(store: EvidenceStore, device: str, agent_findings: list[Finding]) -> list[HypothesisResult]:
    fired_supports: dict[str, list[tuple[str, float]]] = {}
    fired_contradicts: dict[str, list[tuple[str, float]]] = {}
    possible_supports: dict[str, float] = {}
    evidence_refs: dict[str, set[str]] = {}

    for signal in SIGNALS:
        for hyp, role_info in signal.get("hypotheses", {}).items():
            if role_info["role"] == "supports":
                possible_supports[hyp] = possible_supports.get(hyp, 0) + role_info.get("weight", 1)

        obs = latest_observation(store, device, signal["metric"])
        if obs is None or not evaluate_condition(obs.value, signal["op"], signal["threshold"]):
            continue

        af = _agent_finding_for(agent_findings, device, signal["id"])
        for hyp, role_info in signal.get("hypotheses", {}).items():
            role, weight = role_info["role"], role_info.get("weight", 1)
            bucket = fired_supports if role == "supports" else fired_contradicts
            bucket.setdefault(hyp, []).append((signal["id"], weight))
            if af is not None:
                evidence_refs.setdefault(hyp, set()).add(af.finding_id)

    any_breach = bool(fired_supports)
    hw_fired = fired_supports.get("hardware_failure", [])
    hw_subsystems = {s["subsystem"] for s in SIGNALS
                      if s["id"] in {sid for sid, _ in hw_fired} and "subsystem" in s}
    hw_interface_fired = any(sid == "interface_errors_present" for sid, _ in hw_fired)
    co_occurrence = len(hw_subsystems) >= CO_OCCURRENCE_MIN and hw_interface_fired

    results: list[HypothesisResult] = []
    for hyp, spec in HYPOTHESES.items():
        label = spec.get("label", hyp)
        required = spec.get("required_metrics", [])
        present = sum(1 for m in required if latest_observation(store, device, m) is not None)
        coverage = (present / len(required)) if required else 1.0
        missing = [m for m in required if latest_observation(store, device, m) is None]

        if hyp == "healthy":
            score = 0.0 if any_breach else 1.0
        else:
            support_list = fired_supports.get(hyp, [])
            contra_list = fired_contradicts.get(hyp, [])
            support_w = sum(w for _, w in support_list)
            contra_w = sum(w for _, w in contra_list)
            possible = possible_supports.get(hyp, 0)
            raw = (support_w - contra_w) / possible if possible > 0 else 0.0
            score = max(0.0, min(1.0, raw)) * coverage
            if co_occurrence and hyp in CO_OCCURRENCE_DEMOTE:
                score *= CO_OCCURRENCE_DEMOTE_FACTOR

        results.append(HypothesisResult(
            hypothesis=hyp, label=label, score=score, coverage=coverage,
            support=[s for s, _ in fired_supports.get(hyp, [])],
            contradictions=[s for s, _ in fired_contradicts.get(hyp, [])],
            missing=missing, evidence_refs=sorted(evidence_refs.get(hyp, set())),
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def confidence_category(top_score: float, margin: float) -> str:
    high = CONFIDENCE_RULES.get("high", {"score": 0.75, "margin": 0.2})
    medium = CONFIDENCE_RULES.get("medium", {"score": 0.5, "margin": 0.1})
    if top_score >= high["score"] and margin >= high["margin"]:
        return "high"
    if top_score >= medium["score"] and margin >= medium["margin"]:
        return "medium"
    return "low"


def is_insufficient(top_score: float, coverage: float, margin: float) -> bool:
    cfg = CONFIDENCE_RULES.get("insufficient_evidence", {"coverage": 0.5, "score": 0.3, "margin": 0.1})
    return coverage < cfg["coverage"] or top_score < cfg["score"] or margin < cfg["margin"]


class CorrelationAgent:
    name = "correlation_agent"

    def handle(self, task: Envelope, store: EvidenceStore, agent_findings: list[Finding]) -> Envelope:
        devices: list[str] = task.payload.get("devices", [])
        findings: list[Finding] = []
        devices_payload: dict[str, dict] = {}

        for device in devices:
            results = score_device(store, device, agent_findings)
            top = results[0]
            second_score = results[1].score if len(results) > 1 else 0.0
            margin = top.score - second_score
            devices_payload[device] = {
                "ranking": [
                    {"hypothesis": r.hypothesis, "label": r.label, "score": round(r.score, 3),
                     "coverage": round(r.coverage, 3), "support": r.support,
                     "contradictions": r.contradictions, "missing": r.missing}
                    for r in results
                ],
                "top": top.hypothesis,
                "margin": round(margin, 3),
            }
            findings.append(store.ground(Finding(
                claim=f"root_cause:{top.hypothesis}", device=device,
                evidence_refs=top.evidence_refs,
                detail=f"{top.label} (score={top.score:.2f}, margin={margin:.2f})",
            )))

        return Envelope(
            incident_id=task.incident_id, sender=self.name, recipient=task.sender,
            type=MsgType.FINDINGS, findings=findings, payload={"devices": devices_payload},
        )
