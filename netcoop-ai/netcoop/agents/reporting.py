"""Recommendation and Reporting agent: turns the correlation agent's ranked
hypotheses plus the EvidenceStore into the final Report (JSON + Markdown-
renderable). Every supporting_evidence item is traced back through the store
to a raw_ref, never taken on an agent's word.

When a topology is available and the reporting devices fall into more than
one non-overlapping scope (different distribution-switch branches), this
reports more than one diagnosis: a primary one plus additional_diagnoses for
the other scopes, instead of silently picking a single "strongest" device
across unrelated faults.
"""
from __future__ import annotations

from .. import confidence
from ..evidence import EvidenceStore
from ..protocol import Envelope, Finding
from ..report import AdditionalDiagnosis, CheckPerformed, HypothesisScore, RejectedInput, Report, SupportingEvidence
from ..rules import load_rules
from ..topology import Topology

RULES = load_rules()
HYPOTHESES = RULES.get("hypotheses", {})


def _agent_finding_for(agent_findings: list[Finding], device: str, claim: str) -> Finding | None:
    for f in agent_findings:
        if f.device == device and f.claim == claim:
            return f
    return None


def _cluster_by_scope(devices: list[str], topology: Topology | None) -> list[list[str]]:
    """Groups devices that share a topology scope (same immediate parent) together;
    devices in different branches -- or when there's no topology at all -- get their
    own scope, so faults in unrelated segments aren't conflated into one diagnosis."""
    if topology is None:
        return [[d] for d in devices]
    clusters: dict[str, list[str]] = {}
    for d in devices:
        key = topology.parent_of(d) if d in topology.known_devices() else d
        clusters.setdefault(key or d, []).append(d)
    return list(clusters.values())


def _supporting_evidence(top: dict, device: str, agent_findings: list[Finding],
                          store: EvidenceStore) -> list[SupportingEvidence]:
    out: list[SupportingEvidence] = []
    seen_obs_ids: set[str] = set()
    for signal_id in top["support"]:
        finding = _agent_finding_for(agent_findings, device, signal_id)
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
        out.append(SupportingEvidence(
            metric=obs.metric, value=obs.value, unit=obs.unit,
            interpretation=finding.detail, obs_id=obs_id, raw_ref=raw_ref,
        ))
    return out


class ReportingAgent:
    name = "reporting_agent"

    def build_report(self, *, incident_id: str, store: EvidenceStore,
                      correlation_envelope: Envelope, agent_findings: list[Finding],
                      evidence_sources: list[str], rejected_inputs: list[dict],
                      log_path: str, had_agent_errors: bool = False,
                      agent_assumptions: list[str] = (),
                      topology: Topology | None = None,
                      checks_performed: list[dict] = ()) -> Report:
        devices_payload: dict = correlation_envelope.payload.get("devices", {})

        if not devices_payload:
            return Report(
                incident_id=incident_id, status="invalid_input", confidence=0.0,
                confidence_category="low", confidence_method=confidence.METHOD,
                evidence_sources=evidence_sources,
                limitations=["No valid observations were ingested for this incident."],
                rejected_inputs=[RejectedInput(**r) for r in rejected_inputs],
                message_log=log_path,
            )

        clusters = _cluster_by_scope(list(devices_payload.keys()), topology)
        best_device = max(devices_payload, key=lambda d: devices_payload[d]["ranking"][0]["score"])
        primary_cluster = next(c for c in clusters if best_device in c)
        other_clusters = [c for c in clusters if c is not primary_cluster]

        ranking = devices_payload[best_device]["ranking"]
        margin = devices_payload[best_device]["margin"]
        top = ranking[0]
        insufficient = devices_payload[best_device]["insufficient"]

        if insufficient:
            status = "insufficient_evidence"
        elif had_agent_errors:
            status = "partial"
        else:
            status = "diagnosed"
        category = "low" if insufficient else devices_payload[best_device]["confidence_category"]

        suspected_root_cause = top["label"] if status in ("diagnosed", "partial") else None
        # When nothing is confirmed, the top-ranked hypothesis is itself just a candidate.
        candidate_slice = ranking[0:3] if insufficient else ranking[1:3]
        alternative_causes = [r["label"] for r in candidate_slice if r["score"] > 0]

        hyp_spec = HYPOTHESES.get(top["hypothesis"], {})
        if status in ("diagnosed", "partial"):
            recommended_actions = list(hyp_spec.get("recommended_actions", []))
        else:
            recommended_actions = [
                "Gather additional telemetry for the missing or borderline metrics before diagnosing",
                "Re-run diagnosis once more observations are available",
            ]

        supporting_evidence = _supporting_evidence(top, best_device, agent_findings, store)

        additional_diagnoses: list[AdditionalDiagnosis] = []
        for cluster in other_clusters:
            cluster_best = max(cluster, key=lambda d: devices_payload[d]["ranking"][0]["score"])
            c_top = devices_payload[cluster_best]["ranking"][0]
            c_insufficient = devices_payload[cluster_best]["insufficient"]
            additional_diagnoses.append(AdditionalDiagnosis(
                scope_devices=cluster,
                suspected_root_cause=None if c_insufficient else c_top["label"],
                confidence=devices_payload[cluster_best]["confidence"],
                confidence_category="low" if c_insufficient else devices_payload[cluster_best]["confidence_category"],
                supporting_evidence=_supporting_evidence(c_top, cluster_best, agent_findings, store),
            ))

        scope_notes = [f.detail for f in agent_findings if f.claim == "shared_upstream_scope"]

        limitations: list[str] = []
        if top["missing"]:
            limitations.append(f"Missing metrics for {top['hypothesis']}: {', '.join(top['missing'])}")
        if top["stale"]:
            limitations.append(
                f"Stale readings excluded from {top['hypothesis']} coverage: {', '.join(top['stale'])}"
            )
        if insufficient:
            limitations.append(
                "Evidence is insufficient to confidently diagnose a single root cause; "
                "see hypotheses for ranked candidates."
            )
        if had_agent_errors:
            limitations.append("One or more analysis agents failed; this report is based on partial findings.")
        if additional_diagnoses:
            limitations.append(
                f"{len(clusters)} independent topology scopes detected among {len(devices_payload)} "
                "reporting devices; see additional_diagnoses for the others."
            )
        elif len(devices_payload) > 1:
            limitations.append(
                f"{len(devices_payload)} devices reported, all sharing scope; this report focuses on "
                f"{best_device}, the strongest signal."
            )
        for note in scope_notes:
            limitations.append(note)
        for a in dict.fromkeys(agent_assumptions):  # dedupe, preserve order
            limitations.append(a)
        limitations.append("Confidence is a heuristic rule-based score, not a calibrated probability.")

        return Report(
            incident_id=incident_id,
            status=status,
            suspected_root_cause=suspected_root_cause,
            confidence=devices_payload[best_device]["confidence"],
            confidence_category=category,
            confidence_method=confidence.METHOD,
            affected_devices=[best_device],
            supporting_evidence=supporting_evidence,
            alternative_causes=alternative_causes,
            recommended_actions=recommended_actions,
            evidence_sources=evidence_sources,
            limitations=limitations,
            hypotheses=[HypothesisScore(**r) for r in ranking],
            next_check=top["next_check"],
            additional_diagnoses=additional_diagnoses,
            checks_performed=[CheckPerformed(**c) for c in checks_performed],
            rejected_inputs=[RejectedInput(**r) for r in rejected_inputs],
            message_log=log_path,
        )
