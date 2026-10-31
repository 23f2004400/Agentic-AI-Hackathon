"""Recommendation and Reporting agent: turns the correlation agent's ranked
hypotheses plus the EvidenceStore into the final Report (JSON + Markdown-
renderable). Every supporting_evidence item is traced back through the store
to a raw_ref, never taken on an agent's word.
"""
from __future__ import annotations

from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding
from ..report import HypothesisScore, RejectedInput, Report, SupportingEvidence
from ..rules import load_rules
from .correlation import confidence_category, is_insufficient

RULES = load_rules()
HYPOTHESES = RULES.get("hypotheses", {})

CONFIDENCE_METHOD = (
    "score = clamp((support_weight - contradiction_weight) / possible_support_weight, 0, 1) "
    "* coverage, where weights come from netcoop/rules.yaml signals and coverage is the "
    "fraction of a hypothesis's required metrics present and fresh. Heuristic; not a "
    "calibrated probability."
)


def _agent_finding_for(agent_findings: list[Finding], device: str, claim: str) -> Finding | None:
    for f in agent_findings:
        if f.device == device and f.claim == claim:
            return f
    return None


class ReportingAgent:
    name = "reporting_agent"

    def build_report(self, *, incident_id: str, store: EvidenceStore,
                      correlation_envelope: Envelope, agent_findings: list[Finding],
                      evidence_sources: list[str], rejected_inputs: list[dict],
                      log_path: str, had_agent_errors: bool = False) -> Report:
        devices_payload: dict = correlation_envelope.payload.get("devices", {})

        if not devices_payload:
            return Report(
                incident_id=incident_id, status="invalid_input", confidence=0.0,
                confidence_category="low", confidence_method=CONFIDENCE_METHOD,
                evidence_sources=evidence_sources,
                limitations=["No valid observations were ingested for this incident."],
                rejected_inputs=[RejectedInput(**r) for r in rejected_inputs],
                message_log=log_path,
            )

        best_device = max(devices_payload, key=lambda d: devices_payload[d]["ranking"][0]["score"])
        ranking = devices_payload[best_device]["ranking"]
        margin = devices_payload[best_device]["margin"]
        top = ranking[0]

        insufficient = is_insufficient(top["score"], top["coverage"], margin)
        if insufficient:
            status = "insufficient_evidence"
        elif had_agent_errors:
            status = "partial"
        else:
            status = "diagnosed"
        category = "low" if status == "insufficient_evidence" else confidence_category(top["score"], margin)

        suspected_root_cause = top["label"] if status in ("diagnosed", "partial") else None
        alternative_causes = [r["label"] for r in ranking[1:3] if r["score"] > 0]

        hyp_spec = HYPOTHESES.get(top["hypothesis"], {})
        if status in ("diagnosed", "partial"):
            recommended_actions = list(hyp_spec.get("recommended_actions", []))
        else:
            recommended_actions = [
                "Gather additional telemetry for the missing or borderline metrics before diagnosing",
                "Re-run diagnosis once more observations are available",
            ]

        supporting_evidence: list[SupportingEvidence] = []
        seen_obs_ids: set[str] = set()
        for signal_id in top["support"]:
            finding = _agent_finding_for(agent_findings, best_device, signal_id)
            if finding is None or not finding.evidence_refs:
                continue
            obs_id = finding.evidence_refs[0]
            if obs_id in seen_obs_ids:          # two signals can fire on the same observation
                continue
            obs = store.observations.get(obs_id)
            if obs is None:
                continue
            seen_obs_ids.add(obs_id)
            traced = store.trace(obs_id)
            raw_ref = traced[0] if traced else obs.raw_ref
            supporting_evidence.append(SupportingEvidence(
                metric=obs.metric, value=obs.value, unit=obs.unit,
                interpretation=finding.detail, obs_id=obs_id, raw_ref=raw_ref,
            ))

        limitations: list[str] = []
        if top["missing"]:
            limitations.append(
                f"Missing metrics for {top['hypothesis']}: {', '.join(top['missing'])}"
            )
        if insufficient:
            limitations.append(
                "Evidence is insufficient to confidently diagnose a single root cause; "
                "see hypotheses for ranked candidates."
            )
        if had_agent_errors:
            limitations.append("One or more analysis agents failed; this report is based on partial findings.")
        if len(devices_payload) > 1:
            limitations.append(
                f"{len(devices_payload)} devices reported; this report focuses on {best_device}, "
                "the strongest signal. Cross-device correlation is not yet implemented."
            )
        limitations.append("Confidence is a heuristic rule-based score, not a calibrated probability.")

        return Report(
            incident_id=incident_id,
            status=status,
            suspected_root_cause=suspected_root_cause,
            confidence=round(top["score"], 3),
            confidence_category=category,
            confidence_method=CONFIDENCE_METHOD,
            affected_devices=[best_device],
            supporting_evidence=supporting_evidence,
            alternative_causes=alternative_causes,
            recommended_actions=recommended_actions,
            evidence_sources=evidence_sources,
            limitations=limitations,
            hypotheses=[HypothesisScore(**r) for r in ranking],
            checks_performed=[],  # checks.py (targeted diagnostic checks) is not implemented yet
            rejected_inputs=[RejectedInput(**r) for r in rejected_inputs],
            message_log=log_path,
        )
