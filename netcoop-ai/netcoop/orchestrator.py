"""Coordinates a single incident end-to-end: ingest -> dispatch per-device
analysis tasks -> validate each reply -> correlate -> (optionally) request a
targeted check and re-correlate -> report. Every Envelope exchanged is
appended to logs/<incident_id>.jsonl so the run is reproducible and
auditable.

The orchestrator must never crash on bad data -- a malformed reply, an agent
that raises, an agent that hangs, a reply that reuses a msg_id, or the
correlation step itself failing are all caught and turned into an ERROR
Envelope plus a degraded (but always-produced) report, never an exception
escaping run().
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable
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
AGENT_TIMEOUT_SECONDS = 5.0


def _call_with_timeout(fn: Callable, args: tuple, timeout_seconds: float) -> tuple[bool, Any]:
    """Calls fn(*args) in a daemon thread and waits up to timeout_seconds.

    Returns (completed, result). If it times out, returns (False, None) and
    the thread is left running in the background -- Python can't forcibly
    kill a thread, and a daemon thread doesn't block interpreter/process
    exit, so this is a bounded, honest best effort rather than true
    cancellation. Any exception raised by fn is re-raised here, in the
    caller's thread, so normal try/except handling still works.
    """
    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = fn(*args)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller below
            box["error"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    t.join(timeout_seconds)
    if t.is_alive():
        return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


class Orchestrator:
    def __init__(self, analysis_agents: list[Agent] | None = None, logs_dir: Path | str = LOGS_DIR,
                 topology: Topology | None = None, checks_dir: Path | str | None = None,
                 agent_timeout_seconds: float = AGENT_TIMEOUT_SECONDS,
                 on_event: Callable[[Envelope], None] | None = None):
        self.analysis_agents = analysis_agents if analysis_agents is not None else [
            PerformanceAgent(), InfrastructureAgent(), ConnectivityAgent(topology=topology),
        ]
        self.correlation_agent = CorrelationAgent()
        self.reporting_agent = ReportingAgent()
        self.topology = topology
        self.checks_dir = Path(checks_dir) if checks_dir is not None else None
        self.logs_dir = Path(logs_dir)
        self.agent_timeout_seconds = agent_timeout_seconds
        self.on_event = on_event  # optional Callable[[Envelope], None], e.g. for a live UI; never required
        self.last_store: EvidenceStore | None = None  # the store from the most recent run(), for introspection
        self._seen_msg_ids: set[str] = set()           # reset per run(); duplicates are treated as malformed

    def _log(self, incident_id: str, envelope: Envelope) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        path = self.logs_dir / f"{incident_id}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(envelope.model_dump_json() + "\n")
        if self.on_event is not None:
            self.on_event(envelope)

    def _safe_handle(self, name: str, fn: Callable, *args) -> tuple[Envelope | None, str | None]:
        """Calls an agent-like handle method with a timeout and validates the result:
        must be a well-formed Envelope that doesn't reuse a msg_id already seen this
        incident. Never raises -- returns (envelope, None) on success or
        (None, error_message) on any failure, including a hung agent."""
        try:
            completed, result = _call_with_timeout(fn, args, self.agent_timeout_seconds)
            if not completed:
                raise TimeoutError(f"exceeded the {self.agent_timeout_seconds}s time limit")
            if not isinstance(result, Envelope):
                raise TypeError(f"returned {type(result).__name__}, not an Envelope")
            if result.msg_id in self._seen_msg_ids:
                raise ValueError(f"reply reused msg_id {result.msg_id!r}, already seen this incident")
        except Exception as e:
            return None, f"{name} failed: {e}"
        self._seen_msg_ids.add(result.msg_id)
        return result, None

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
            reply, error = self._safe_handle(agent.name, agent.handle, task, store)
            if error:
                had_errors = True
                self._log(incident_id, Envelope(
                    incident_id=incident_id, sender=agent.name, recipient="orchestrator",
                    type=MsgType.ERROR, errors=[error],
                ))
                continue
            self._log(incident_id, reply)
            agent_findings.extend(reply.findings)
            assumptions.extend(reply.assumptions)
            evidence_sources.append(agent.name)

        return agent_findings, evidence_sources, assumptions, had_errors

    def _correlate(self, incident_id: str, devices: list[str], store: EvidenceStore,
                    agent_findings: list[Finding]) -> tuple[Envelope, bool]:
        """Returns (envelope, had_error). On failure, returns a degenerate empty-payload
        Envelope so the caller can still build a (degraded) report rather than crash."""
        task = Envelope(
            incident_id=incident_id, sender="orchestrator", recipient=self.correlation_agent.name,
            type=MsgType.TASK, payload={"devices": devices},
        )
        self._log(incident_id, task)
        reply, error = self._safe_handle(self.correlation_agent.name, self.correlation_agent.handle,
                                          task, store, agent_findings)
        if error:
            self._log(incident_id, Envelope(
                incident_id=incident_id, sender=self.correlation_agent.name, recipient="orchestrator",
                type=MsgType.ERROR, errors=[error],
            ))
            empty = Envelope(
                incident_id=incident_id, sender=self.correlation_agent.name, recipient="orchestrator",
                type=MsgType.FINDINGS, payload={"devices": {}},
            )
            return empty, True
        self._log(incident_id, reply)
        return reply, False

    def run(self, path: str | Path, incident_id: str | None = None) -> Report:
        path = Path(path)
        incident_id = incident_id or f"INC-{uuid4().hex[:8]}"
        self._seen_msg_ids = set()

        store = EvidenceStore()
        self.last_store = store
        rejected = ingest.load_file(store, path)
        devices = sorted({o.device for o in store.observations.values()})
        store.known_devices = set(devices) | (self.topology.known_devices() if self.topology else set())

        agent_findings, evidence_sources, agent_assumptions, had_agent_errors = \
            self._dispatch_agents(incident_id, devices, store)
        correlation_reply, correlation_failed = self._correlate(incident_id, devices, store, agent_findings)
        had_agent_errors = had_agent_errors or correlation_failed
        agent_assumptions.extend(correlation_reply.assumptions)
        evidence_sources.append(self.correlation_agent.name)

        checks_performed: list[dict] = []
        for _ in range(MAX_CHECKS):
            devices_payload = correlation_reply.payload.get("devices", {})
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

            # Snapshot of the ranking at the moment this check was requested --
            # recording only, for display in the report; it does not change
            # which check is chosen or how scoring works below.
            snapshot_base = {
                "device": best_device,
                "top_hypothesis": top_hyp,
                "top_label": ranking[0]["label"],
                "top_score": ranking[0]["score"],
                "second_hypothesis": second_hyp,
                "second_label": ranking[1]["label"] if len(ranking) > 1 else None,
                "second_score": ranking[1]["score"] if len(ranking) > 1 else None,
                "margin": margin,
                "margin_threshold": MARGIN_CONFIDENT_ENOUGH,
            }
            margin_prefix = f"margin {margin:.3f} < threshold {MARGIN_CONFIDENT_ENOUGH}"

            if check_name is None:
                checks_performed.append({
                    "name": f"(none declared for {top_hyp} vs {second_hyp})", "status": "check_failed",
                    "result": "no discriminating check declared in rules.yaml for this hypothesis pair",
                    "before": {**snapshot_base, "reason": (
                        f"{margin_prefix} -> no discriminating check declared for {top_hyp} vs {second_hyp}"
                    )},
                })
                break
            if self.checks_dir is None:
                checks_performed.append({
                    "name": check_name, "status": "check_failed", "result": "no checks directory provided",
                    "before": {**snapshot_base, "reason": f"{margin_prefix} -> requested {check_name}"},
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
            checks_performed.append({
                "name": outcome.name, "status": outcome.status, "result": outcome.result,
                "before": {**snapshot_base, "reason": f"{margin_prefix} -> requested {outcome.name}"},
            })
            if outcome.status != "applied":
                break  # nothing more to try for this check; stop rather than guess again

            # New data may have changed the picture: re-run analysis and correlation.
            devices = sorted({o.device for o in store.observations.values()})
            store.known_devices = set(devices) | (self.topology.known_devices() if self.topology else set())
            agent_findings, round_sources, round_assumptions, round_errors = \
                self._dispatch_agents(incident_id, devices, store)
            evidence_sources.extend(round_sources)
            agent_assumptions.extend(round_assumptions)
            had_agent_errors = had_agent_errors or round_errors
            correlation_reply, correlation_failed = self._correlate(incident_id, devices, store, agent_findings)
            had_agent_errors = had_agent_errors or correlation_failed
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
