"""Fault Correlation agent: builds one Hypothesis per rule in rules.yaml,
scored directly against the raw observations in the EvidenceStore -- never
against what another agent merely claims -- so the diagnosis never depends
on an agent correctly self-reporting.

Only Findings the store has already graded "observed" or "inferred" count as
evidence; a Finding that grounds as "unsupported" (a rumor -- a claim citing
no real obs_id) is listed (as an ignored-claim assumption) but never allowed
to move a score. A hypothesis-level Finding cites the agent Findings whose
signal fired, so it grounds as "inferred" -- an inference built on observed
evidence, never evidence itself.

This is a deliberately simple, transparent scorer (weighted rule support
minus contradiction, scaled by metric coverage) plus one explicit special
case -- the hardware_failure co-occurrence rule from CLAUDE.md -- rather than
a general hypothesis-vs-hypothesis conflict graph. Targeted diagnostic
checks (checks.py) are not implemented; `next_check` is a heuristic
suggestion, not an executed check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import confidence
from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding, MsgType
from ..rules import evaluate_condition, load_rules
from .base import is_only_stale, latest_observation

RULES = load_rules()
SIGNALS = RULES.get("signals", [])
HYPOTHESES = RULES.get("hypotheses", {})

_HW = HYPOTHESES.get("hardware_failure", {})
CO_OCCURRENCE_MIN = _HW.get("co_occurrence_min", 3)
CO_OCCURRENCE_DEMOTE = set(_HW.get("co_occurrence_demote", []))
CO_OCCURRENCE_DEMOTE_FACTOR = _HW.get("co_occurrence_demote_factor", 0.5)


@dataclass
class Hypothesis:
    hypothesis: str
    label: str
    score: float
    coverage: float
    support: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)          # subset of missing: had data, too old to use
    affected_devices: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    next_check: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


def _matching_findings(agent_findings: list[Finding], device: str, claim: str) -> list[Finding]:
    return [f for f in agent_findings if f.device == device and f.claim == claim]


def _grounded_finding_for(agent_findings: list[Finding], device: str, claim: str) -> Finding | None:
    """Only a Finding the store has already graded observed/inferred counts as evidence."""
    for f in _matching_findings(agent_findings, device, claim):
        if f.grounding in ("observed", "inferred"):
            return f
    return None


def _next_check_for(label: str, required: list[str], missing: list[str], device: str, score: float) -> str | None:
    if missing:
        return f"Obtain a fresh reading of {missing[0]} for {device} to confirm or rule out {label}"
    if required and 0.0 < score < 1.0:
        return (f"Re-poll {', '.join(required)} for {device} after a short interval to see whether "
                f"the trend confirms or rules out {label}")
    return None


def score_device(store: EvidenceStore, device: str, agent_findings: list[Finding]) -> tuple[list[Hypothesis], list[str]]:
    """Returns (ranked hypotheses, ignored-claim assumptions for this device)."""
    fired_supports: dict[str, list[tuple[str, float]]] = {}
    fired_contradicts: dict[str, list[tuple[str, float]]] = {}
    possible_supports: dict[str, float] = {}
    evidence_refs: dict[str, set[str]] = {}
    ignored_claims: list[str] = []

    for signal in SIGNALS:
        for hyp, role_info in signal.get("hypotheses", {}).items():
            if role_info["role"] == "supports":
                possible_supports[hyp] = possible_supports.get(hyp, 0) + role_info.get("weight", 1)

        for f in _matching_findings(agent_findings, device, signal["id"]):
            if f.grounding == "unsupported":
                ignored_claims.append(
                    f"ignored unsupported claim {signal['id']!r} for {device} (cites no verifiable evidence)"
                )

        obs = latest_observation(store, device, signal["metric"])
        if obs is None or not evaluate_condition(obs.value, signal["op"], signal["threshold"]):
            continue

        af = _grounded_finding_for(agent_findings, device, signal["id"])
        for hyp, role_info in signal.get("hypotheses", {}).items():
            role, weight = role_info["role"], role_info.get("weight", 1)
            bucket = fired_supports if role == "supports" else fired_contradicts
            bucket.setdefault(hyp, []).append((signal["id"], weight))
            if af is not None:
                evidence_refs.setdefault(hyp, set()).add(af.finding_id)

    ignored_claims = sorted(set(ignored_claims))

    any_breach = bool(fired_supports)
    hw_fired = fired_supports.get("hardware_failure", [])
    hw_subsystems = {s["subsystem"] for s in SIGNALS
                      if s["id"] in {sid for sid, _ in hw_fired} and "subsystem" in s}
    hw_interface_fired = any(sid == "interface_errors_present" for sid, _ in hw_fired)
    co_occurrence = len(hw_subsystems) >= CO_OCCURRENCE_MIN and hw_interface_fired

    results: list[Hypothesis] = []
    for hyp, spec in HYPOTHESES.items():
        label = spec.get("label", hyp)
        required = spec.get("required_metrics", [])
        present = [m for m in required if latest_observation(store, device, m) is not None]
        coverage = (len(present) / len(required)) if required else 1.0
        missing = [m for m in required if m not in present]
        stale = [m for m in missing if is_only_stale(store, device, m)]

        if hyp == "healthy":
            # "Everything's fine" is only as credible as how much we actually measured --
            # zero evidence must not score the same as a fully-covered clean bill of health.
            score = (0.0 if any_breach else 1.0) * coverage
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

        results.append(Hypothesis(
            hypothesis=hyp, label=label, score=score, coverage=coverage,
            support=[s for s, _ in fired_supports.get(hyp, [])],
            contradictions=[s for s, _ in fired_contradicts.get(hyp, [])],
            missing=missing, stale=stale, affected_devices=[device],
            assumptions=list(ignored_claims),
            next_check=_next_check_for(label, required, missing, device, score),
            evidence_refs=sorted(evidence_refs.get(hyp, set())),
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results, ignored_claims


class CorrelationAgent:
    name = "correlation_agent"

    def handle(self, task: Envelope, store: EvidenceStore, agent_findings: list[Finding]) -> Envelope:
        devices: list[str] = task.payload.get("devices", [])
        findings: list[Finding] = []
        devices_payload: dict[str, dict] = {}
        all_assumptions: list[str] = []

        for device in devices:
            results, ignored_claims = score_device(store, device, agent_findings)
            all_assumptions.extend(ignored_claims)
            top = results[0]
            second_score = results[1].score if len(results) > 1 else 0.0
            margin = top.score - second_score
            conf = confidence.evaluate(top.score, margin)
            devices_payload[device] = {
                "ranking": [
                    {"hypothesis": r.hypothesis, "label": r.label, "score": round(r.score, 3),
                     "coverage": round(r.coverage, 3), "support": r.support,
                     "contradictions": r.contradictions, "missing": r.missing, "stale": r.stale,
                     "next_check": r.next_check}
                    for r in results
                ],
                "top": top.hypothesis,
                "margin": round(margin, 3),
                "confidence": conf.score,
                "confidence_category": conf.category,
                "insufficient": confidence.is_insufficient(top.score, top.coverage, margin),
            }
            findings.append(store.ground(Finding(
                claim=f"root_cause:{top.hypothesis}", device=device,
                evidence_refs=top.evidence_refs,
                detail=f"{top.label} (score={top.score:.2f}, margin={margin:.2f})",
            )))

        return Envelope(
            incident_id=task.incident_id, sender=self.name, recipient=task.sender,
            type=MsgType.FINDINGS, findings=findings, payload={"devices": devices_payload},
            assumptions=sorted(set(all_assumptions)),
        )
