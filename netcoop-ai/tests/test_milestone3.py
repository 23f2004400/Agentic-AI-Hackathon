import json
from pathlib import Path

import pytest

from netcoop import ingest
from netcoop.agents.correlation import CorrelationAgent
from netcoop.agents.infrastructure import InfrastructureAgent
from netcoop.agents.performance import PerformanceAgent
from netcoop.agents.reporting import ReportingAgent
from netcoop.evidence import EvidenceStore
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope, MsgType
from netcoop.report import Report

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"


@pytest.fixture()
def work_dirs(tmp_path):
    """Isolated logs/ dir per test so runs don't pollute the real project logs/."""
    logs_dir = tmp_path / "logs"
    return logs_dir


def _load(filename: str) -> EvidenceStore:
    store = EvidenceStore()
    rejected = ingest.load_file(store, SCENARIOS / filename)
    assert rejected == []
    return store


# ---- agents/base.py + performance/infrastructure -------------------------

def test_performance_agent_emits_grounded_finding_citing_obs_id():
    store = _load("schema_a_hardware_failure.json")
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="performance_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_CAMPUS_4"]})
    reply = PerformanceAgent().handle(task, store)
    assert reply.type == MsgType.FINDINGS
    assert reply.findings
    for f in reply.findings:
        assert f.grounding == "observed"          # cites a real obs_id -> store grounded it
        assert f.evidence_refs[0] in store.observations
    assert reply.missing == []                     # all of performance's required metrics are present

def test_infrastructure_agent_reports_missing_when_field_absent():
    store = _load("schema_a_latency_spike.json")  # no interface_errors field in this fault type
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="infrastructure_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_CAMPUS_7"]})
    reply = InfrastructureAgent().handle(task, store)
    assert "SW_CAMPUS_7:interface_errors" in reply.missing

def test_agent_never_fabricates_grounding_for_sender_claims():
    # Even if a Finding claims it's observed, the store recomputes grounding from evidence_refs.
    store = _load("schema_a_hardware_failure.json")
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="performance_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_CAMPUS_4"]})
    reply = PerformanceAgent().handle(task, store)
    assert all(f.grounding in ("observed", "inferred") for f in reply.findings)


# ---- agents/correlation.py -------------------------------------------------

def test_correlation_ranks_healthy_first_for_normal_scenario():
    store = _load("schema_a_normal.json")
    device = "SW_CAMPUS_2"
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="correlation_agent",
                     type=MsgType.TASK, payload={"devices": [device]})
    perf = PerformanceAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="performance_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    infra = InfrastructureAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="infrastructure_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    reply = CorrelationAgent().handle(task, store, perf.findings + infra.findings)
    ranking = reply.payload["devices"][device]["ranking"]
    assert ranking[0]["hypothesis"] == "healthy"
    assert ranking[0]["score"] == pytest.approx(1.0)

def test_correlation_co_occurrence_rule_ranks_hardware_failure_first():
    store = _load("schema_a_hardware_failure.json")
    device = "SW_CAMPUS_4"
    perf = PerformanceAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="performance_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    infra = InfrastructureAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="infrastructure_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="correlation_agent",
                     type=MsgType.TASK, payload={"devices": [device]})
    reply = CorrelationAgent().handle(task, store, perf.findings + infra.findings)
    ranking = reply.payload["devices"][device]["ranking"]
    assert ranking[0]["hypothesis"] == "hardware_failure"
    # link_congestion and cpu_overload are explained away, not eliminated: they rank below
    # hardware_failure but above the hypotheses with no evidence at all.
    scores = {r["hypothesis"]: r["score"] for r in ranking}
    assert scores["hardware_failure"] > scores["link_congestion"] > 0
    assert scores["hardware_failure"] > scores["cpu_overload"] > 0
    # The root-cause Finding must be "inferred": it cites agent Finding ids, not obs_ids directly.
    root_cause_finding = next(f for f in reply.findings if f.device == device)
    assert root_cause_finding.grounding == "inferred"

def test_correlation_insufficient_evidence_for_latency_spike():
    store = _load("schema_a_latency_spike.json")
    device = "SW_CAMPUS_7"
    perf = PerformanceAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="performance_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    infra = InfrastructureAgent().handle(
        Envelope(incident_id="i1", sender="orchestrator", recipient="infrastructure_agent",
                 type=MsgType.TASK, payload={"devices": [device]}), store)
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="correlation_agent",
                     type=MsgType.TASK, payload={"devices": [device]})
    reply = CorrelationAgent().handle(task, store, perf.findings + infra.findings)
    top = reply.payload["devices"][device]["ranking"][0]
    assert top["score"] < 0.3          # below the rules.yaml insufficient_evidence threshold


# ---- orchestrator.py + Report model ---------------------------------------

@pytest.mark.parametrize("filename,expected_status,expected_hypothesis", [
    ("schema_a_normal.json", "diagnosed", "healthy"),
    ("schema_a_latency_spike.json", "insufficient_evidence", None),
    ("schema_a_hardware_failure.json", "diagnosed", "hardware_failure"),
])
def test_orchestrator_end_to_end_produces_valid_report(work_dirs, filename, expected_status, expected_hypothesis):
    orchestrator = Orchestrator(logs_dir=work_dirs)
    report = orchestrator.run(SCENARIOS / filename, incident_id=f"TEST-{filename}")
    assert isinstance(report, Report)          # already validated by construction
    assert report.status == expected_status
    if expected_hypothesis == "hardware_failure":
        assert report.suspected_root_cause == "Hardware failure"
    if expected_hypothesis == "healthy":
        assert report.suspected_root_cause == "No fault detected"

    # every supporting_evidence item traces to a real raw_ref via its obs_id
    for ev in report.supporting_evidence:
        store = EvidenceStore()
        # re-derive nothing; just check the obs_id format and that raw_ref is non-empty
        assert ev.obs_id.startswith("obs-")
        assert ev.raw_ref

    log_path = Path(report.message_log)
    assert log_path.exists()
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    types = [l["type"] for l in lines]
    assert types.count("task") >= 3            # performance, infrastructure, correlation
    assert "report" in types

def test_orchestrator_supporting_evidence_obs_id_traces_to_raw_ref(work_dirs):
    orchestrator = Orchestrator(logs_dir=work_dirs)
    report = orchestrator.run(SCENARIOS / "schema_a_hardware_failure.json", incident_id="TEST-trace")
    assert report.supporting_evidence
    store = orchestrator.last_store
    for ev in report.supporting_evidence:
        assert ev.obs_id in store.observations
        traced = store.trace(ev.obs_id)
        assert traced == [ev.raw_ref]

def test_orchestrator_handles_malformed_agent_reply_without_crashing(work_dirs):
    class BrokenAgent:
        name = "broken_agent"
        def handle(self, task, store):
            return {"not": "an envelope"}  # malformed: not an Envelope at all

    orchestrator = Orchestrator(analysis_agents=[BrokenAgent(), PerformanceAgent()], logs_dir=work_dirs)
    report = orchestrator.run(SCENARIOS / "schema_a_hardware_failure.json", incident_id="TEST-broken")
    assert isinstance(report, Report)  # pipeline still completes
    log_path = Path(report.message_log)
    lines = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines()]
    error_envelopes = [l for l in lines if l["type"] == "error"]
    assert any("broken_agent" in e["errors"][0] for e in error_envelopes)

def test_orchestrator_invalid_input_for_file_with_no_observations(work_dirs):
    orchestrator = Orchestrator(logs_dir=work_dirs)
    report = orchestrator.run(SCENARIOS / "broken_malformed.json", incident_id="TEST-invalid")
    assert report.status == "invalid_input"
    assert report.rejected_inputs


# ---- CLI --------------------------------------------------------------

def test_cli_run_writes_report_files():
    # Orchestrator() binds its logs_dir default at def-time, so run_command's bare
    # Orchestrator() always targets the real project logs/ dir; exercise it for real
    # and clean up the files this test creates afterward.
    from netcoop import cli
    incident_id = "TEST-cli"
    json_path = cli.REPORTS_DIR / f"{incident_id}.json"
    md_path = cli.REPORTS_DIR / f"{incident_id}.md"
    log_path = ROOT / "logs" / f"{incident_id}.jsonl"
    try:
        rc = cli.main(["run", str(SCENARIOS / "schema_a_hardware_failure.json"), "--incident-id", incident_id])
        assert rc == 0
        assert json_path.exists()
        assert md_path.exists()
        report_dict = json.loads(json_path.read_text(encoding="utf-8"))
        assert report_dict["status"] == "diagnosed"
        assert report_dict["suspected_root_cause"] == "Hardware failure"
    finally:
        for p in (json_path, md_path, log_path):
            if p.exists():
                p.unlink()
