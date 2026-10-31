"""Robustness audit: the orchestrator must never crash on bad data and must
always emit a Report. One test per failure mode."""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from netcoop import checks
from netcoop.agents.base import has_conflicting_latest
from netcoop.agents.performance import PerformanceAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.evidence import EvidenceStore
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope, Finding, MsgType, Observation
from netcoop.report import Report

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"


@pytest.fixture()
def logs_dir(tmp_path):
    return tmp_path / "logs"


T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)


# ---- fake agents used to inject each failure mode --------------------------

class MalformedReplyAgent:
    name = "malformed_reply_agent"
    def handle(self, task, store):
        return {"not": "an envelope"}  # wrong type entirely


class RaisingAgent:
    name = "raising_agent"
    def handle(self, task, store):
        raise ValueError("deliberately broken agent")


class SlowAgent:
    name = "slow_agent"
    def handle(self, task, store):
        time.sleep(1.5)  # exceeds the short test timeout below
        return Envelope(incident_id=task.incident_id, sender=self.name, recipient=task.sender,
                         type=MsgType.FINDINGS)


class FixedMsgIdAgent:
    """Returns a reply with a caller-supplied msg_id, to simulate a msg_id collision."""
    def __init__(self, name: str, msg_id: str):
        self.name = name
        self._msg_id = msg_id
    def handle(self, task, store):
        return Envelope(msg_id=self._msg_id, incident_id=task.incident_id, sender=self.name,
                         recipient=task.sender, type=MsgType.FINDINGS)


class BadSchemaVersionAgent:
    name = "bad_schema_version_agent"
    def handle(self, task, store):
        # Envelope's own validator rejects this at construction time.
        return Envelope(schema_version="9.9", incident_id=task.incident_id, sender=self.name,
                         recipient=task.sender, type=MsgType.FINDINGS)


# ---- 1. malformed agent reply ----------------------------------------------

def test_malformed_agent_reply_does_not_crash(logs_dir):
    orchestrator = Orchestrator(analysis_agents=[MalformedReplyAgent(), PerformanceAgent()], logs_dir=logs_dir)
    report = orchestrator.run(SCENARIOS / "hardware_failure.json", incident_id="M7-malformed")
    assert isinstance(report, Report)
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    errors = [l for l in lines if l["type"] == "error"]
    assert any("malformed_reply_agent" in e["errors"][0] for e in errors)


# ---- 2. agent raising an exception -----------------------------------------

def test_agent_raising_exception_does_not_crash(logs_dir):
    orchestrator = Orchestrator(analysis_agents=[RaisingAgent(), PerformanceAgent()], logs_dir=logs_dir)
    report = orchestrator.run(SCENARIOS / "hardware_failure.json", incident_id="M7-raises")
    assert isinstance(report, Report)
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    errors = [l for l in lines if l["type"] == "error"]
    assert any("deliberately broken agent" in e["errors"][0] for e in errors)


# ---- 3. agent timeout (per-agent time limit) -------------------------------

def test_agent_timeout_does_not_crash(logs_dir):
    orchestrator = Orchestrator(analysis_agents=[SlowAgent(), PerformanceAgent()], logs_dir=logs_dir,
                                 agent_timeout_seconds=0.2)
    report = orchestrator.run(SCENARIOS / "hardware_failure.json", incident_id="M7-timeout")
    assert isinstance(report, Report)
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    errors = [l for l in lines if l["type"] == "error"]
    assert any("time limit" in e["errors"][0] for e in errors)
    # PerformanceAgent still ran and the diagnosis still completed despite the hung agent.
    assert report.status in ("diagnosed", "partial")


# ---- 4. duplicate envelope msg_id ------------------------------------------

def test_duplicate_envelope_msg_id_is_rejected_not_crashed(logs_dir):
    agents = [FixedMsgIdAgent("agent_one", "DUP-ID"), FixedMsgIdAgent("agent_two", "DUP-ID")]
    orchestrator = Orchestrator(analysis_agents=agents, logs_dir=logs_dir)
    report = orchestrator.run(SCENARIOS / "hardware_failure.json", incident_id="M7-dup-msgid")
    assert isinstance(report, Report)
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    findings_msgs = [l for l in lines if l["type"] == "findings" and l["sender"] == "agent_one"]
    errors = [l for l in lines if l["type"] == "error" and l["sender"] == "agent_two"]
    assert findings_msgs  # agent_one's reply was accepted
    assert any("duplicate msg_id" in e["errors"][0] or "reused msg_id" in e["errors"][0] for e in errors)


# ---- 5. envelope with unsupported schema_version ---------------------------

def test_envelope_construction_rejects_unsupported_schema_version():
    with pytest.raises(ValidationError):
        Envelope(schema_version="9.9", incident_id="i1", sender="x", recipient="y", type=MsgType.TASK)

def test_agent_returning_unsupported_schema_version_does_not_crash_orchestrator(logs_dir):
    orchestrator = Orchestrator(analysis_agents=[BadSchemaVersionAgent(), PerformanceAgent()], logs_dir=logs_dir)
    report = orchestrator.run(SCENARIOS / "hardware_failure.json", incident_id="M7-bad-schema")
    assert isinstance(report, Report)
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    errors = [l for l in lines if l["type"] == "error"]
    assert any("bad_schema_version_agent" in e["errors"][0] for e in errors)


# ---- 6. conflicting timestamps for the same device/metric ------------------

def _obs(**kw) -> Observation:
    base = dict(device="SW_X", metric="cpu", value=50.0, unit="pct", timestamp=T0,
                source="test", raw_ref="t#0")
    base.update(kw)
    return Observation(**base)

def test_conflicting_same_timestamp_readings_detected_not_crashed():
    store = EvidenceStore()
    store.add_observation(_obs(value=50.0, raw_ref="t#0"))
    store.add_observation(_obs(value=95.0, raw_ref="t#1"))  # same device/metric/timestamp, different value
    assert has_conflicting_latest(store, "SW_X", "cpu") is True

    task = Envelope(incident_id="i1", sender="orchestrator", recipient="infrastructure_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_X"]})
    reply = InfrastructureAgent().handle(task, store)  # must not raise
    assert any("conflicting readings" in a for a in reply.assumptions)

def test_conflicting_readings_do_not_crash_full_orchestrator_run(tmp_path, logs_dir):
    conflicting_file = tmp_path / "conflicting.json"
    conflicting_file.write_text(json.dumps([
        {"timestamp": "2026-10-31T12:00:00Z", "device_id": "SW_X",
         "metrics": {"cpu_utilization_pct": 50.0}, "status": "Warning"},
        {"timestamp": "2026-10-31T12:00:00Z", "device_id": "SW_X",
         "metrics": {"cpu_utilization_pct": 95.0}, "status": "Warning"},
    ]), encoding="utf-8")
    orchestrator = Orchestrator(logs_dir=logs_dir)
    report = orchestrator.run(conflicting_file, incident_id="M7-conflict")
    assert isinstance(report, Report)


# ---- 7. unknown device in a finding ----------------------------------------

def test_finding_with_unknown_device_grounds_unsupported():
    store = EvidenceStore(known_devices={"SW_X"})
    obs = _obs()
    store.add_observation(obs)
    rogue = Finding(claim="high_cpu", device="SW_GHOST", evidence_refs=[obs.obs_id])
    grounded = store.ground(rogue)
    assert grounded.grounding == "unsupported"  # cites a real obs_id, but the device isn't recognized

def test_known_topology_device_with_no_telemetry_still_grounds_normally():
    # A device present in the topology-derived known_devices set but with no
    # observations of its own (e.g. a distribution switch) must NOT be treated
    # as "unknown" -- only devices outside known_devices entirely are rejected.
    store = EvidenceStore(known_devices={"SW_X", "DIST_SW_HOSTEL"})
    obs = _obs()
    store.add_observation(obs)
    f = Finding(claim="shared_upstream_scope", device="DIST_SW_HOSTEL", evidence_refs=[obs.obs_id])
    grounded = store.ground(f)
    assert grounded.grounding == "observed"


# ---- 8. empty input file ----------------------------------------------------

def test_empty_json_array_input_produces_invalid_input_report(tmp_path, logs_dir):
    empty_file = tmp_path / "empty_array.json"
    empty_file.write_text("[]", encoding="utf-8")
    orchestrator = Orchestrator(logs_dir=logs_dir)
    report = orchestrator.run(empty_file, incident_id="M7-empty-array")
    assert isinstance(report, Report)
    assert report.status == "invalid_input"

def test_zero_byte_input_file_produces_invalid_input_report(tmp_path, logs_dir):
    empty_file = tmp_path / "zero_bytes.json"
    empty_file.write_text("", encoding="utf-8")
    orchestrator = Orchestrator(logs_dir=logs_dir)
    report = orchestrator.run(empty_file, incident_id="M7-zero-bytes")
    assert isinstance(report, Report)
    assert report.status == "invalid_input"
    assert report.rejected_inputs
    assert "malformed JSON" in report.rejected_inputs[0].reason


# ---- 9. all inputs rejected -> invalid_input with reasons -------------------

def test_all_records_rejected_produces_invalid_input_with_reasons(tmp_path, logs_dir):
    all_bad = tmp_path / "all_bad.json"
    all_bad.write_text(json.dumps([
        {"timestamp": "not-a-timestamp", "device_id": "SW_X", "metrics": {"cpu_utilization_pct": 10}},
        {"timestamp": "2026-10-31T12:00:00", "device_id": "SW_X", "metrics": {}},  # naive timestamp
        {"timestamp": "2026-10-31T12:00:00Z", "device_id": "SW_X", "metrics": {"bogus_field_pct": 1}},
    ]), encoding="utf-8")
    orchestrator = Orchestrator(logs_dir=logs_dir)
    report = orchestrator.run(all_bad, incident_id="M7-all-rejected")
    assert isinstance(report, Report)
    assert report.status == "invalid_input"
    assert len(report.rejected_inputs) >= 2
    assert all(r.reason for r in report.rejected_inputs)


# ---- 10. partial data -> status partial with limitations -------------------

def test_partial_agent_failure_yields_partial_status_with_limitations(logs_dir):
    # Raw observations (including queue_drops) are still in the store regardless of
    # which agents ran -- correlation re-reads the store directly -- so losing
    # InfrastructureAgent here degrades the evidence trail, not the score itself.
    agents = [PerformanceAgent(), RaisingAgent()]
    orchestrator = Orchestrator(analysis_agents=agents, logs_dir=logs_dir)
    report = orchestrator.run(SCENARIOS / "congestion.json", incident_id="M7-partial")
    assert isinstance(report, Report)
    assert report.status == "partial"
    assert report.suspected_root_cause == "Link congestion"
    assert any("agent" in lim.lower() and "fail" in lim.lower() for lim in report.limitations)
