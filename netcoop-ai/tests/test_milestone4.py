import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from netcoop.agents.connectivity import ConnectivityAgent
from netcoop.evidence import EvidenceStore
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope, MsgType, Observation
from netcoop.topology import TopologyError, load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = ROOT / "scenarios"
INPUTS = SCENARIOS_DIR / "inputs"
TOPOLOGY_PATH = SCENARIOS_DIR / "topology.json"
GROUND_TRUTH_PATH = SCENARIOS_DIR / "ground_truth.json"


# ---- topology.py ------------------------------------------------------

def test_load_topology_from_scenarios_file():
    topo = load_topology(TOPOLOGY_PATH)
    assert "CORE_SW_01" in topo.known_devices()
    assert "SW_CAMPUS_1" in topo.known_devices()

def test_load_topology_validation_rejects_unknown_links(tmp_path):
    bad_file = tmp_path / "bad_topology.json"
    bad_file.write_text(json.dumps({"devices": ["A", "B"], "links": [{"a": "A", "b": "GHOST"}]}),
                         encoding="utf-8")
    with pytest.raises(TopologyError):
        load_topology(bad_file)

def test_downstream_of_is_hierarchical():
    topo = load_topology(TOPOLOGY_PATH)
    hostel_children = topo.downstream_of("DIST_SW_HOSTEL")
    assert {"SW_CAMPUS_1", "SW_CAMPUS_2", "SW_CAMPUS_3", "SW_CAMPUS_4", "SW_CAMPUS_5",
            "AP_HOSTEL_1", "AP_HOSTEL_2"} <= hostel_children
    assert "SW_CAMPUS_6" not in hostel_children          # that's under DIST_SW_ACAD
    assert topo.downstream_of("CORE_SW_01") >= topo.known_devices() - {"CORE_SW_01"}

def test_common_upstream_finds_nearest_shared_ancestor():
    topo = load_topology(TOPOLOGY_PATH)
    assert topo.common_upstream(["SW_CAMPUS_1", "SW_CAMPUS_2", "SW_CAMPUS_3"]) == "DIST_SW_HOSTEL"
    # devices in different segments: nearest shared ancestor is the core switch
    assert topo.common_upstream(["SW_CAMPUS_1", "SW_CAMPUS_6"]) == "CORE_SW_01"

def test_is_wireless():
    topo = load_topology(TOPOLOGY_PATH)
    assert topo.is_wireless("AP_HOSTEL_1") is True
    assert topo.is_wireless("SW_CAMPUS_1") is False

def test_query_methods_reject_unknown_device():
    topo = load_topology(TOPOLOGY_PATH)
    with pytest.raises(TopologyError):
        topo.is_wireless("GHOST_DEVICE")
    with pytest.raises(TopologyError):
        topo.downstream_of("GHOST_DEVICE")
    with pytest.raises(TopologyError):
        topo.common_upstream(["SW_CAMPUS_1", "GHOST_DEVICE"])


# ---- agents/connectivity.py --------------------------------------------

def _status_obs(device: str, value: str) -> Observation:
    return Observation(device=device, metric="device_status", value=value, unit=None,
                        timestamp=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
                        source="test", raw_ref="t#0.status")

def test_connectivity_agent_without_topology_notes_limitation():
    store = EvidenceStore()
    store.add_observation(_status_obs("SW_CAMPUS_1", "Warning"))
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="connectivity_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_CAMPUS_1"]})
    reply = ConnectivityAgent(topology=None).handle(task, store)
    assert any("scope limited to reporting device" in a for a in reply.assumptions)

def test_connectivity_agent_detects_shared_upstream_with_topology():
    topo = load_topology(TOPOLOGY_PATH)
    store = EvidenceStore()
    devices = ["SW_CAMPUS_1", "SW_CAMPUS_2", "SW_CAMPUS_3"]
    for d in devices:
        store.add_observation(_status_obs(d, "Warning"))
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="connectivity_agent",
                     type=MsgType.TASK, payload={"devices": devices})
    reply = ConnectivityAgent(topology=topo).handle(task, store)
    assert reply.payload["common_upstream"] == "DIST_SW_HOSTEL"
    scope_finding = next(f for f in reply.findings if f.claim == "shared_upstream_scope")
    assert scope_finding.device == "DIST_SW_HOSTEL"
    assert scope_finding.grounding == "observed"     # cites the devices' device_status obs_ids
    assert not any("scope limited" in a for a in reply.assumptions)

def test_connectivity_agent_reports_segment_mix():
    topo = load_topology(TOPOLOGY_PATH)
    store = EvidenceStore()
    for d in ("SW_CAMPUS_1", "AP_HOSTEL_1"):
        store.add_observation(_status_obs(d, "Warning"))
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="connectivity_agent",
                     type=MsgType.TASK, payload={"devices": ["SW_CAMPUS_1", "AP_HOSTEL_1"]})
    reply = ConnectivityAgent(topology=topo).handle(task, store)
    assert reply.payload["segment_mix"] == "mixed"

def test_connectivity_agent_dns_dhcp_signal_grounded():
    store = EvidenceStore()
    store.add_observation(Observation(
        device="DNS_DHCP_01", metric="dns_failure", value=40.0, unit="pct",
        timestamp=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        source="test", raw_ref="t#0.dns_failure_pct",
    ))
    task = Envelope(incident_id="i1", sender="orchestrator", recipient="connectivity_agent",
                     type=MsgType.TASK, payload={"devices": ["DNS_DHCP_01"]})
    reply = ConnectivityAgent(topology=None).handle(task, store)
    dns_findings = [f for f in reply.findings if f.claim == "dns_failures_present"]
    assert len(dns_findings) == 1
    assert dns_findings[0].grounding == "observed"


# ---- end-to-end: all 12 generated scenarios match ground_truth.json's status ----

def _ground_truth() -> dict:
    return json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))

@pytest.mark.parametrize("scenario", list(_ground_truth().keys()))
def test_scenario_matches_ground_truth_status(tmp_path, scenario):
    gt = _ground_truth()[scenario]
    topo = load_topology(TOPOLOGY_PATH)
    # A row may carry "input" (another scenario's input file; defaults to its
    # own key) and "checks" (a scenarios/checks/<dir> name) -- see
    # ambiguous_loss__checks_A/_B, which reuse ambiguous_loss.json with a
    # targeted check applied.
    checks_dir = (SCENARIOS_DIR / "checks" / gt["checks"]) if "checks" in gt else None
    orchestrator = Orchestrator(topology=topo, logs_dir=tmp_path / "logs", checks_dir=checks_dir)
    report = orchestrator.run(INPUTS / f"{gt.get('input', scenario)}.json", incident_id=f"GT-{scenario}")
    assert report.status == gt["expected_status"], (
        f"{scenario}: expected status {gt['expected_status']!r}, got {report.status!r}"
    )

def test_upstream_failure_scope_note_surfaces_in_report(tmp_path):
    topo = load_topology(TOPOLOGY_PATH)
    orchestrator = Orchestrator(topology=topo, logs_dir=tmp_path / "logs")
    report = orchestrator.run(INPUTS / "upstream_failure.json", incident_id="GT-upstream-scope")
    assert any("DIST_SW_HOSTEL" in lim for lim in report.limitations)

def test_gen_scenarios_wrote_schema_b_variants_that_parse():
    from netcoop import ingest
    for name in ("normal", "congestion", "cpu_overload"):
        store = EvidenceStore()
        rejected = ingest.load_file(store, INPUTS / f"schema_b_{name}.json")
        assert rejected == []
        assert store.observations
