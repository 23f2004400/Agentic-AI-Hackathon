import json
from pathlib import Path

import pytest

from netcoop import checks
from netcoop.orchestrator import Orchestrator
from netcoop.topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"
CHECKS_DIR = ROOT / "scenarios" / "checks"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"


@pytest.fixture()
def logs_dir(tmp_path):
    return tmp_path / "logs"


def _run(logs_dir: Path, filename: str, checks_dir=None, topo=None, incident_id: str = "T"):
    o = Orchestrator(logs_dir=logs_dir, checks_dir=checks_dir, topology=topo)
    return o.run(SCENARIOS / filename, incident_id=incident_id)


# ---- netcoop/checks.py: named checks + discriminating_check ----------------

def test_named_checks_are_declared_in_rules_yaml():
    for name in ("upstream_link_utilization", "interface_counters_next_window",
                 "wifi_client_rssi", "dns_probe"):
        assert name in checks.CHECK_DEFINITIONS

def test_discriminating_check_is_order_independent():
    a = checks.discriminating_check("link_congestion", "interface_fault")
    b = checks.discriminating_check("interface_fault", "link_congestion")
    assert a == b == "upstream_link_utilization"

def test_discriminating_check_returns_none_for_undeclared_pair():
    assert checks.discriminating_check("healthy", "cpu_overload") is None

def test_perform_goes_through_the_real_ingest_validation_path(tmp_path):
    # a malformed check file must be rejected, not crash the check mechanism
    from netcoop.evidence import EvidenceStore
    bad_dir = tmp_path / "bad_checks"
    bad_dir.mkdir()
    (bad_dir / "upstream_link_utilization.json").write_text("{not valid json", encoding="utf-8")
    store = EvidenceStore()
    outcome = checks.perform(store, bad_dir, "upstream_link_utilization")
    assert outcome.status == "check_failed"
    assert "rejected" in outcome.result


# ---- scenario ambiguous_loss: variant A vs B (task 3 + 4) -------------------

def test_ambiguous_loss_baseline_is_insufficient_without_a_check(logs_dir):
    report = _run(logs_dir, "ambiguous_loss.json", incident_id="M6-baseline")
    assert report.status == "insufficient_evidence"
    assert report.suspected_root_cause is None

def test_ambiguous_loss_variant_a_resolves_to_link_congestion(logs_dir):
    report = _run(logs_dir, "ambiguous_loss.json",
                   checks_dir=CHECKS_DIR / "ambiguous_loss_A", incident_id="M6-variant-a")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "Link congestion"
    assert report.checks_performed[0].status == "applied"
    assert report.checks_performed[0].name == "upstream_link_utilization"

def test_ambiguous_loss_variant_b_resolves_to_interface_fault(logs_dir):
    report = _run(logs_dir, "ambiguous_loss.json",
                   checks_dir=CHECKS_DIR / "ambiguous_loss_B", incident_id="M6-variant-b")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "Interface or cabling fault on a single link"
    assert report.checks_performed[0].status == "applied"

def test_variant_a_and_b_produce_different_top_causes(logs_dir):
    a = _run(logs_dir, "ambiguous_loss.json", checks_dir=CHECKS_DIR / "ambiguous_loss_A", incident_id="M6-diff-a")
    b = _run(logs_dir, "ambiguous_loss.json", checks_dir=CHECKS_DIR / "ambiguous_loss_B", incident_id="M6-diff-b")
    assert a.suspected_root_cause != b.suspected_root_cause
    assert {a.suspected_root_cause, b.suspected_root_cause} == {
        "Link congestion", "Interface or cabling fault on a single link",
    }


# ---- orchestrator check-loop behavior (task 2 + 4) --------------------------

def test_no_check_requested_when_margin_already_large(logs_dir):
    # hardware_failure's margin is already well above 0.2 -- no check should fire at all.
    report = _run(logs_dir, "hardware_failure.json", checks_dir=CHECKS_DIR / "stale", incident_id="M6-no-check")
    assert report.checks_performed == []

def test_failed_check_is_recorded_and_does_not_crash(logs_dir):
    # scenarios/checks/contradictory/ doesn't exist -> check_failed, not an exception.
    report = _run(logs_dir, "contradictory.json",
                   checks_dir=CHECKS_DIR / "contradictory", incident_id="M6-failed")
    assert report.status == "insufficient_evidence"
    assert report.checks_performed
    assert report.checks_performed[0].status == "check_failed"

def test_never_more_than_two_checks(logs_dir):
    for scenario, checks_subdir in [
        ("ambiguous_loss.json", "ambiguous_loss_A"),
        ("ambiguous_loss.json", "ambiguous_loss_B"),
        ("stale.json", "stale"),
        ("insufficient.json", "insufficient"),
        ("contradictory.json", "contradictory"),
    ]:
        report = _run(logs_dir, scenario, checks_dir=CHECKS_DIR / checks_subdir,
                       incident_id=f"M6-budget-{scenario}-{checks_subdir}")
        assert len(report.checks_performed) <= 2

def test_check_request_and_result_envelopes_carry_the_check_name(logs_dir):
    report = _run(logs_dir, "ambiguous_loss.json",
                   checks_dir=CHECKS_DIR / "ambiguous_loss_A", incident_id="M6-envelopes")
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    requests = [l for l in lines if l["type"] == "check_request"]
    results = [l for l in lines if l["type"] == "check_result"]
    assert requests and requests[0]["payload"]["check"] == "upstream_link_utilization"
    assert requests[0]["payload"]["discriminates"] == ["link_congestion", "interface_fault"] or \
           requests[0]["payload"]["discriminates"] == ["interface_fault", "link_congestion"]
    assert results and results[0]["payload"]["status"] == "applied"


# ---- CLI --------------------------------------------------------------

def test_cli_checks_flag_selects_variant_directory():
    from netcoop import cli
    incident_id_a, incident_id_b = "M6-cli-a", "M6-cli-b"
    json_a = cli.REPORTS_DIR / f"{incident_id_a}.json"
    json_b = cli.REPORTS_DIR / f"{incident_id_b}.json"
    log_a = ROOT / "logs" / f"{incident_id_a}.jsonl"
    log_b = ROOT / "logs" / f"{incident_id_b}.jsonl"
    try:
        rc_a = cli.main(["run", str(SCENARIOS / "ambiguous_loss.json"),
                          "--checks", str(CHECKS_DIR / "ambiguous_loss_A"), "--incident-id", incident_id_a])
        rc_b = cli.main(["run", str(SCENARIOS / "ambiguous_loss.json"),
                          "--checks", str(CHECKS_DIR / "ambiguous_loss_B"), "--incident-id", incident_id_b])
        assert rc_a == 0 and rc_b == 0
        report_a = json.loads(json_a.read_text(encoding="utf-8"))
        report_b = json.loads(json_b.read_text(encoding="utf-8"))
        assert report_a["suspected_root_cause"] == "Link congestion"
        assert report_b["suspected_root_cause"] == "Interface or cabling fault on a single link"
    finally:
        for p in (json_a, cli.REPORTS_DIR / f"{incident_id_a}.md", log_a,
                  json_b, cli.REPORTS_DIR / f"{incident_id_b}.md", log_b):
            if p.exists():
                p.unlink()


# ---- the other 3 named checks, proven end-to-end -----------------------
# upstream_link_utilization is exercised extensively above via ambiguous_loss/
# stale/insufficient. These build small, self-contained (not permanent
# fixture) scenarios purely to prove interface_counters_next_window,
# wifi_client_rssi, and dns_probe are correctly selected, applied, and change
# the ranking too -- not just declared in rules.yaml and never exercised.

def _write_schema_a(path: Path, device: str, metrics: dict, status: str = "Warning") -> None:
    path.write_text(json.dumps({
        "timestamp": "2026-10-31T12:00:00Z", "device_id": device, "metrics": metrics, "status": status,
    }), encoding="utf-8")

def _write_check(path: Path, device: str, metrics: dict, status: str = "Warning") -> None:
    path.write_text(json.dumps({
        "timestamp": "2026-10-31T12:01:00Z", "device_id": device, "metrics": metrics, "status": status,
    }), encoding="utf-8")

def test_interface_counters_next_window_resolves_interface_fault_vs_hardware_failure(tmp_path, logs_dir):
    device = "SW_TEST_1"
    scenario = tmp_path / "scenario.json"
    _write_schema_a(scenario, device, {
        "packet_loss_pct": 8.0, "queue_drops": 150, "cpu_utilization_pct": 40.0,
        "link_utilization_pct": 40.0, "latency_ms": 50.0,
    })
    report = _run(logs_dir, str(scenario), incident_id="M6-icnw-baseline")
    assert report.status == "insufficient_evidence"
    assert checks.discriminating_check("interface_fault", "hardware_failure") == "interface_counters_next_window"

    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    _write_check(checks_dir / "interface_counters_next_window.json", device, {"interface_errors": 60})

    report = _run(logs_dir, str(scenario), checks_dir=checks_dir, incident_id="M6-icnw-applied")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "Interface or cabling fault on a single link"
    assert report.checks_performed[0].name == "interface_counters_next_window"
    assert report.checks_performed[0].status == "applied"

def test_wifi_client_rssi_resolves_weak_wifi_vs_interface_fault(tmp_path, logs_dir):
    device = "AP_TEST_1"
    scenario = tmp_path / "scenario.json"
    _write_schema_a(scenario, device, {"retransmission_pct": 15.0, "packet_loss_pct": 8.0})
    report = _run(logs_dir, str(scenario), incident_id="M6-rssi-baseline")
    assert report.status == "insufficient_evidence"
    assert checks.discriminating_check("weak_wifi", "interface_fault") == "wifi_client_rssi"

    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    _write_check(checks_dir / "wifi_client_rssi.json", device, {"rssi_dbm": -85.0})

    report = _run(logs_dir, str(scenario), checks_dir=checks_dir, incident_id="M6-rssi-applied")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "Weak wireless signal"
    assert report.checks_performed[0].name == "wifi_client_rssi"
    assert report.checks_performed[0].status == "applied"

def test_dns_probe_resolves_dns_dhcp_failure_vs_link_congestion(tmp_path, logs_dir):
    device = "DNS_TEST_1"
    scenario = tmp_path / "scenario.json"
    _write_schema_a(scenario, device, {
        "dhcp_failures": 3, "queue_drops": 120, "link_utilization_pct": 60.0, "latency_ms": 50.0,
    })
    report = _run(logs_dir, str(scenario), incident_id="M6-dns-baseline")
    assert report.status == "insufficient_evidence"
    assert checks.discriminating_check("dns_dhcp_failure", "link_congestion") == "dns_probe"

    checks_dir = tmp_path / "checks"
    checks_dir.mkdir()
    _write_check(checks_dir / "dns_probe.json", device, {"dns_failure_pct": 35.0})

    report = _run(logs_dir, str(scenario), checks_dir=checks_dir, incident_id="M6-dns-applied")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "DNS or DHCP service failure"
    assert report.checks_performed[0].name == "dns_probe"
    assert report.checks_performed[0].status == "applied"
