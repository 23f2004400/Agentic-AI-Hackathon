"""Coordinates a single incident end-to-end: ingest -> dispatch per-device
analysis tasks -> validate each reply -> correlate -> (optionally) request a
targeted check and re-correlate -> report. Every Envelope exchanged is
appended to logs/<incident_id>.jsonl so the run is reproducible and
auditable.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from . import checks, ingest
from .agents.base import Agent
from .agents.connectivity import ConnectivityAgent
from .agents.correlation import CorrelationAgent
from .agents.infrastructure import InfrastructureAgent
from .agents.performance import PerformanceAgent
from .agents.reporting import ReportingAgent
from .evidence import EvidenceStore
from .protocol import Envelope, Finding, MsgType
from .report import Report
from .topology import Topology

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
MAX_CHECKS = 2
MARGIN_CONFIDENT_ENOUGH = 0.2  # stop requesting checks once margin reaches this


class Orchestrator:
    def __init__(self, analysis_agents: list[Agent] | None = None, logs_dir: Path | str = LOGS_DIR,
                 topology: Topology | None = None, checks_dir: Path | str | None = None):
        self.analysis_agents = analysis_agents if analysis_agents is not None else [
            PerformanceAgent(), InfrastructureAgent(), ConnectivityAgent(topology=topology),
        ]
        self.correlation_agent = CorrelationAgent()
        self.reporting_agent = ReportingAgent()
        self.topology = topology
        self.checks_dir = Path(checks_dir) if checks_dir is not None else None
        self.logs_dir = Path(logs_dir)
        self.last_store: EvidenceStore | None = None  # the store from the most recent run(), for introspection

    def _log(self, incident_id: str, envelope: Envelope) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / f"{incident_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(envelope.model_dump_json() + "\n")

    def _dispatch_agents(self, incident_id: str, devices: list[str], store: EvidenceStore
                          ) -> tuple[list[Finding], list[str], list[str], bool]:
        """Runs every analysis agent once. Returns (findings, evidence_sources, assumptions, had_errors)."""
        agent_findings: list[Finding] = []
        evidence_sources: list[str] = []
        assumptions: list[str] = []
        had_errors = False

        for agent in self.analysis_agents:
            task = Envelope(
                incident_id=incident_id, sender="orchestrator", recipient=agent.name,
                type=MsgType.TASK, payload={"devices": devices},
            )
            self._log(incident_id, task)
            try:
                reply = agent.handle(task, store)
                if not isinstance(reply, Envelope):
                    raise TypeError(f"{agent.name} returned {type(reply).__name__}, not an Envelope")
            except Exception as e:
                had_errors = True
                self._log(incident_id, Envelope(
                    incident_id=incident_id, sender=agent.name, recipient="orchestrator",
                    type=MsgType.ERROR, errors=[f"{agent.name} failed: {e}"],
                ))
                continue
            self._log(incident_id, reply)
            agent_findings.extend(reply.findings)
            assumptions.extend(reply.assumptions)
            evidence_sources.append(agent.name)

        return agent_findings, evidence_sources, assumptions, had_errors

    def _correlate(self, incident_id: str, devices: list[str], store: EvidenceStore,
                    agent_findings: list[Finding]) -> Envelope:
        task = Envelope(
            incident_id=incident_id, sender="orchestrator", recipient=self.correlation_agent.name,
            type=MsgType.TASK, payload={"devices": devices},
        )
        self._log(incident_id, task)
        reply = self.correlation_agent.handle(task, store, agent_findings)
        self._log(incident_id, reply)
        return reply

    def run(self, path: str | Path, incident_id: str | None = None) -> Report:
        path = Path(path)
        incident_id = incident_id or f"INC-{uuid4().hex[:8]}"

        store = EvidenceStore()
        self.last_store = store
        rejected = ingest.load_file(store, path)
        devices = sorted({o.device for o in store.observations.values()})

        agent_findings, evidence_sources, agent_assumptions, had_agent_errors = \
            self._dispatch_agents(incident_id, devices, store)
        correlation_reply = self._correlate(incident_id, devices, store, agent_findings)
        agent_assumptions.extend(correlation_reply.assumptions)
        evidence_sources.append(self.correlation_agent.name)

        checks_performed: list[dict] = []
        for _ in range(MAX_CHECKS):
            devices_payload = correlation_reply.payload["devices"]
            if not devices_payload:
                break
            best_device = max(devices_payload, key=lambda d: devices_payload[d]["ranking"][0]["score"])
            ranking = devices_payload[best_device]["ranking"]
            margin = devices_payload[best_device]["margin"]
            if margin >= MARGIN_CONFIDENT_ENOUGH:
                break  # already confident enough; no need to spend a check

            top_hyp = ranking[0]["hypothesis"]
            second_hyp = ranking[1]["hypothesis"] if len(ranking) > 1 else None
            check_name = checks.discriminating_check(top_hyp, second_hyp) if second_hyp else None
            if check_name is None:
                checks_performed.append({
                    "name": f"(none declared for {top_hyp} vs {second_hyp})", "status": "check_failed",
                    "result": "no discriminating check declared in rules.yaml for this hypothesis pair",
                })
                break
            if self.checks_dir is None:
                checks_performed.append({
                    "name": check_name, "status": "check_failed", "result": "no checks directory provided",
                })
                break

            self._log(incident_id, Envelope(
                incident_id=incident_id, sender="orchestrator", recipient="checks",
                type=MsgType.CHECK_REQUEST,
                payload={"check": check_name, "device": best_device, "discriminates": [top_hyp, second_hyp]},
            ))
            outcome = checks.perform(store, self.checks_dir, check_name)
            self._log(incident_id, Envelope(
                incident_id=incident_id, sender="checks", recipient="orchestrator",
                type=MsgType.CHECK_RESULT, payload={"status": outcome.status, "result": outcome.result},
            ))
            checks_performed.append({"name": outcome.name, "status": outcome.status, "result": outcome.result})
            if outcome.status != "applied":
                break  # nothing more to try for this check; stop rather than guess again

            # New data may have changed the picture: re-run analysis and correlation.
            devices = sorted({o.device for o in store.observations.values()})
            agent_findings, round_sources, round_assumptions, round_errors = \
                self._dispatch_agents(incident_id, devices, store)
            evidence_sources.extend(round_sources)
            agent_assumptions.extend(round_assumptions)
            had_agent_errors = had_agent_errors or round_errors
            correlation_reply = self._correlate(incident_id, devices, store, agent_findings)
            agent_assumptions.extend(correlation_reply.assumptions)

        log_path = str(self.logs_dir / f"{incident_id}.jsonl")
        report = self.reporting_agent.build_report(
            incident_id=incident_id, store=store, correlation_envelope=correlation_reply,
            agent_findings=agent_findings, evidence_sources=evidence_sources,
            rejected_inputs=rejected, log_path=log_path, had_agent_errors=had_agent_errors,
            agent_assumptions=agent_assumptions, topology=self.topology,
            checks_performed=checks_performed,
        )

        self._log(incident_id, Envelope(
            incident_id=incident_id, sender=self.reporting_agent.name, recipient="orchestrator",
            type=MsgType.REPORT, payload=report.model_dump(mode="json"),
        ))
        return report
