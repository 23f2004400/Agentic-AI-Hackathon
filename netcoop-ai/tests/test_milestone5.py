import json
from pathlib import Path

import pytest

from netcoop import confidence, ingest
from netcoop.agents.correlation import Hypothesis, score_device
from netcoop.evidence import EvidenceStore
from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Finding
from netcoop.report import Report
from netcoop.topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"
CHECKS_DIR = ROOT / "scenarios" / "checks"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"
GROUND_TRUTH = json.loads((ROOT / "scenarios" / "ground_truth.json").read_text(encoding="utf-8"))


@pytest.fixture()
def logs_dir(tmp_path):
    return tmp_path / "logs"


def _run(logs_dir: Path, filename: str, topo=None, incident_id: str = "T") -> Report:
    o = Orchestrator(topology=topo, logs_dir=logs_dir)
    return o.run(SCENARIOS / filename, incident_id=incident_id)


# ---- netcoop/confidence.py -------------------------------------------------

def test_confidence_category_boundaries():
    assert confidence.category_for(0.75, 0.2) == "high"
    assert confidence.category_for(0.74, 0.2) == "medium"    # below the high score threshold
    assert confidence.category_for(0.5, 0.1) == "medium"
    assert confidence.category_for(0.49, 0.1) == "low"
    assert confidence.category_for(0.9, 0.0) == "low"   # score high but margin too small

def test_confidence_is_insufficient_boundaries():
    assert confidence.is_insufficient(score=0.2, coverage=1.0, margin=1.0) is True   # score < 0.3
    assert confidence.is_insufficient(score=1.0, coverage=0.4, margin=1.0) is True   # coverage < 0.5
    assert confidence.is_insufficient(score=1.0, coverage=1.0, margin=0.05) is True  # margin < 0.1
    assert confidence.is_insufficient(score=1.0, coverage=1.0, margin=1.0) is False

def test_confidence_evaluate_returns_score_category_and_method():
    result = confidence.evaluate(0.9, 0.3)
    assert result.score == 0.9
    assert result.category == "high"
    assert "clamp" in result.method and "coverage" in result.method


# ---- agents/correlation.py: Hypothesis structure ---------------------------

def test_hypothesis_has_required_fields():
    store = EvidenceStore()
    rejected = ingest.load_file(store, SCENARIOS / "hardware_failure.json")
    assert rejected == []
    results, ignored = score_device(store, "SW_CAMPUS_4", [])
    assert ignored == []
    top = results[0]
    assert isinstance(top, Hypothesis)
    assert top.hypothesis == "hardware_failure"
    assert top.affected_devices == ["SW_CAMPUS_4"]
    assert isinstance(top.assumptions, list)
    assert isinstance(top.stale, list)
    # fully confirmed, full coverage -> nothing left to check
    assert top.next_check is None

def test_rumor_finding_never_changes_the_ranking():
    store = EvidenceStore()
    ingest.load_file(store, SCENARIOS / "hardware_failure.json")
    baseline, _ = score_device(store, "SW_CAMPUS_4", [])

    # A rumor: claims to support a signal, but cites no real evidence, so the
    # store grounds it "unsupported" when correlation re-checks it.
    rumor = store.ground(Finding(claim="high_cpu", device="SW_CAMPUS_4", evidence_refs=[],
                                   grounding="observed"))  # sender lies about its own grounding
    assert rumor.grounding == "unsupported"

    with_rumor, ignored = score_device(store, "SW_CAMPUS_4", [rumor])
    assert [(r.hypothesis, r.score) for r in baseline] == [(r.hypothesis, r.score) for r in with_rumor]
    assert any("unsupported" in note for note in ignored)

def test_unsupported_finding_listed_but_not_counted_as_evidence():
    store = EvidenceStore()
    ingest.load_file(store, SCENARIOS / "hardware_failure.json")
    rumor = store.ground(Finding(claim="interface_errors_present", device="SW_CAMPUS_4", evidence_refs=[]))
    assert rumor.grounding == "unsupported"
    results, ignored = score_device(store, "SW_CAMPUS_4", [rumor])
    top = next(r for r in results if r.hypothesis == "interface_fault")
    # the real obs_id-citing evidence still grounds it -- the rumor doesn't erase real evidence
    assert rumor.finding_id not in top.evidence_refs
    assert ignored  # the rumor is on record, just not counted


# ---- latency_spike special case (task 2) -----------------------------------

def test_latency_spike_is_insufficient_with_candidates_and_next_check(logs_dir):
    report = _run(logs_dir, "schema_a_latency_spike.json", incident_id="M5-latency-spike")
    assert report.status == "insufficient_evidence"
    assert report.suspected_root_cause is None
    assert len(report.alternative_causes) >= 1       # candidate causes listed
    assert report.next_check is not None             # a next check is suggested


# ---- simultaneous faults in non-overlapping scopes (task 3) ---------------

def test_simultaneous_faults_in_different_segments_produce_two_diagnoses(logs_dir):
    topo = load_topology(TOPOLOGY_PATH)
    report = _run(logs_dir, "simultaneous.json", topo=topo, incident_id="M5-simultaneous")
    assert report.status == "diagnosed"
    assert report.additional_diagnoses, "expected a second diagnosis for the non-overlapping segment"
    primary_devices = set(report.affected_devices)
    other_devices = set(report.additional_diagnoses[0].scope_devices)
    assert primary_devices.isdisjoint(other_devices)
    assert report.additional_diagnoses[0].suspected_root_cause is not None

def test_overlapping_scope_multi_device_incident_has_no_additional_diagnoses(logs_dir):
    # upstream_failure: SW_CAMPUS_1/2/3 all share DIST_SW_HOSTEL -- one scope, one diagnosis.
    topo = load_topology(TOPOLOGY_PATH)
    report = _run(logs_dir, "upstream_failure.json", topo=topo, incident_id="M5-upstream")
    assert report.additional_diagnoses == []


# ---- stale observations excluded from coverage (task 5) -------------------

def test_stale_scenario_excludes_stale_metric_from_coverage_and_limitations(logs_dir):
    report = _run(logs_dir, "stale.json", incident_id="M5-stale")
    assert report.status == "insufficient_evidence"
    stale_hyp = next(h for h in report.hypotheses if h.hypothesis == "interface_fault")
    assert "interface_errors" in stale_hyp.stale
    assert "interface_errors" in stale_hyp.missing   # stale is a subset of missing
    assert any("stale" in lim.lower() for lim in report.limitations)


# ---- contradictory scenario lowers confidence (task 6) ---------------------

def test_contradictory_scenario_has_low_confidence(logs_dir):
    report = _run(logs_dir, "contradictory.json", incident_id="M5-contradictory")
    assert report.status == "insufficient_evidence"
    assert report.confidence < 0.3
    assert report.confidence_category == "low"


# ---- insufficient scenario abstains (task 6) -------------------------------

def test_insufficient_scenario_abstains_from_naming_a_cause(logs_dir):
    report = _run(logs_dir, "insufficient.json", incident_id="M5-insufficient")
    assert report.status == "insufficient_evidence"
    assert report.suspected_root_cause is None


# ---- every generator sample: status AND root cause match ground truth -----

@pytest.mark.parametrize("scenario", list(GROUND_TRUTH.keys()))
def test_scenario_status_and_root_cause_match_ground_truth(logs_dir, scenario):
    topo = load_topology(TOPOLOGY_PATH)
    gt = GROUND_TRUTH[scenario]
    report = _run(logs_dir, f"{scenario}.json", topo=topo, incident_id=f"M5-GT-{scenario}")
    assert report.status == gt["expected_status"]
    if gt["expected_status"] == "diagnosed":
        # ground_truth's root_cause for multi-fault scenarios documents both faults in
        # prose; just check the primary diagnosis's label appears somewhere in it.
        assert report.suspected_root_cause in gt["root_cause"] or gt["root_cause"].startswith(
            report.suspected_root_cause or "\0")


# ---- netcoop/checks.py: targeted diagnostic checks -------------------------

def test_without_checks_dir_insufficient_scenario_stays_insufficient(logs_dir):
    report = _run(logs_dir, "stale.json", incident_id="M5-check-none")
    assert report.status == "insufficient_evidence"
    assert report.checks_performed
    assert report.checks_performed[0].result == "no checks directory provided"

def test_check_resolves_stale_scenario_to_diagnosed(logs_dir):
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "stale")
    report = o.run(SCENARIOS / "stale.json", incident_id="M5-check-stale")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "Interface or cabling fault on a single link"
    assert any(c.result.startswith("applied") for c in report.checks_performed)

def test_check_resolves_insufficient_scenario_to_diagnosed(logs_dir):
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "insufficient")
    report = o.run(SCENARIOS / "insufficient.json", incident_id="M5-check-insufficient")
    assert report.status == "diagnosed"
    assert report.suspected_root_cause == "No fault detected"

def test_no_matching_check_file_leaves_contradictory_insufficient(logs_dir):
    # scenarios/checks/contradictory/ doesn't exist -- checks must never force a cause.
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "contradictory")
    report = o.run(SCENARIOS / "contradictory.json", incident_id="M5-check-contradictory")
    assert report.status == "insufficient_evidence"
    assert report.suspected_root_cause is None
    assert "no check available" in report.checks_performed[0].result

def test_checks_budget_is_at_most_two(logs_dir):
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "insufficient")
    report = o.run(SCENARIOS / "insufficient.json", incident_id="M5-check-budget")
    assert len(report.checks_performed) <= 2

def test_check_request_and_result_envelopes_are_logged(logs_dir):
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "stale")
    report = o.run(SCENARIOS / "stale.json", incident_id="M5-check-log")
    lines = [json.loads(l) for l in Path(report.message_log).read_text(encoding="utf-8").splitlines()]
    types = [l["type"] for l in lines]
    assert "check_request" in types
    assert "check_result" in types

def test_well_diagnosed_scenario_requests_no_check(logs_dir):
    # margin is already >= 0.2 for hardware_failure, so no check should be requested at all.
    o = Orchestrator(logs_dir=logs_dir, checks_dir=CHECKS_DIR / "stale")
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M5-check-skip")
    assert report.checks_performed == []


# ---- eval/baseline.py: the naive comparison point --------------------------

def _baseline():
    import sys
    eval_dir = str(ROOT / "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    import baseline
    return baseline

def test_baseline_diagnoses_from_status_field_alone():
    baseline = _baseline()
    result = baseline.diagnose(SCENARIOS / "hardware_failure.json")
    assert result["status"] == "diagnosed"
    assert result["suspected_root_cause"] == "Hardware failure"

def test_baseline_is_less_accurate_than_the_real_pipeline_on_ambiguous_scenarios():
    baseline = _baseline()
    for name in ("insufficient", "stale", "contradictory"):
        gt = GROUND_TRUTH[name]
        result = baseline.diagnose(SCENARIOS / f"{name}.json")
        # the naive baseline trusts the device's self-reported "Warning" status
        # and confidently (wrongly) diagnoses something, unlike the real pipeline
        assert result["status"] != gt["expected_status"]
