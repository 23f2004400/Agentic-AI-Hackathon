"""Coordinates a single incident end-to-end: ingest -> dispatch per-device
analysis tasks -> validate each reply -> correlate -> report. Every Envelope
exchanged is appended to logs/<incident_id>.jsonl so the run is reproducible
and auditable.
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from . import ingest
from .agents.base import Agent
from .agents.correlation import CorrelationAgent
from .agents.infrastructure import InfrastructureAgent
from .agents.performance import PerformanceAgent
from .agents.reporting import ReportingAgent
from .evidence import EvidenceStore
from .protocol import Envelope, Finding, MsgType
from .report import Report

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


class Orchestrator:
    def __init__(self, analysis_agents: list[Agent] | None = None, logs_dir: Path | str = LOGS_DIR):
        self.analysis_agents = analysis_agents if analysis_agents is not None else [
            PerformanceAgent(), InfrastructureAgent(),
        ]
        self.correlation_agent = CorrelationAgent()
        self.reporting_agent = ReportingAgent()
        self.logs_dir = Path(logs_dir)
        self.last_store: EvidenceStore | None = None  # the store from the most recent run(), for introspection

    def _log(self, incident_id: str, envelope: Envelope) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / f"{incident_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(envelope.model_dump_json() + "\n")

    def run(self, path: str | Path, incident_id: str | None = None) -> Report:
        path = Path(path)
        incident_id = incident_id or f"INC-{uuid4().hex[:8]}"

        store = EvidenceStore()
        self.last_store = store
        rejected = ingest.load_file(store, path)
        devices = sorted({o.device for o in store.observations.values()})

        agent_findings: list[Finding] = []
        evidence_sources: list[str] = []
        had_agent_errors = False

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
                had_agent_errors = True
                self._log(incident_id, Envelope(
                    incident_id=incident_id, sender=agent.name, recipient="orchestrator",
                    type=MsgType.ERROR, errors=[f"{agent.name} failed: {e}"],
                ))
                continue
            self._log(incident_id, reply)
            agent_findings.extend(reply.findings)
            evidence_sources.append(agent.name)

        correlation_task = Envelope(
            incident_id=incident_id, sender="orchestrator", recipient=self.correlation_agent.name,
            type=MsgType.TASK, payload={"devices": devices},
        )
        self._log(incident_id, correlation_task)
        correlation_reply = self.correlation_agent.handle(correlation_task, store, agent_findings)
        self._log(incident_id, correlation_reply)
        evidence_sources.append(self.correlation_agent.name)

        log_path = str(self.logs_dir / f"{incident_id}.jsonl")
        report = self.reporting_agent.build_report(
            incident_id=incident_id, store=store, correlation_envelope=correlation_reply,
            agent_findings=agent_findings, evidence_sources=evidence_sources,
            rejected_inputs=rejected, log_path=log_path, had_agent_errors=had_agent_errors,
        )

        self._log(incident_id, Envelope(
            incident_id=incident_id, sender=self.reporting_agent.name, recipient="orchestrator",
            type=MsgType.REPORT, payload=report.model_dump(mode="json"),
        ))
        return report
