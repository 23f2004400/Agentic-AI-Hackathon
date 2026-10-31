import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"


def _eval_modules():
    eval_dir = str(ROOT / "eval")
    if eval_dir not in sys.path:
        sys.path.insert(0, eval_dir)
    import baseline
    import run_eval
    return baseline, run_eval


# ---- eval/baseline.py: single-pass threshold classifier --------------------

def test_baseline_never_abstains_even_with_almost_no_data(tmp_path):
    baseline, _ = _eval_modules()
    sparse = tmp_path / "sparse.json"
    sparse.write_text(json.dumps({
        "timestamp": "2026-10-31T12:00:00Z", "device_id": "SW_X", "metrics": {}, "status": "Warning",
    }), encoding="utf-8")
    result = baseline.diagnose(sparse)
    assert result["status"] == "diagnosed"           # never "insufficient_evidence"
    assert result["suspected_root_cause"] is not None

def test_baseline_picks_latest_record_in_a_time_series():
    baseline, _ = _eval_modules()
    result = baseline.diagnose(SCENARIOS / "hardware_failure.json")
    assert result["status"] == "diagnosed"
    assert result["suspected_root_cause"] == "Hardware failure"
    assert result["affected_devices"] == ["SW_CAMPUS_4"]

def test_baseline_uses_raw_metrics_not_device_status_field(tmp_path):
    # status says "Healthy" but link_utilization is clearly congested -- a
    # single-pass threshold classifier on raw records must not defer to status.
    baseline, _ = _eval_modules()
    f = tmp_path / "mislabeled.json"
    f.write_text(json.dumps({
        "timestamp": "2026-10-31T12:00:00Z", "device_id": "SW_X",
        "metrics": {"link_utilization_pct": 95.0}, "status": "Healthy",
    }), encoding="utf-8")
    result = baseline.diagnose(f)
    assert result["suspected_root_cause"] == "Link congestion"

def test_baseline_defaults_to_no_fault_detected_when_nothing_crosses_a_threshold():
    baseline, _ = _eval_modules()
    result = baseline.diagnose(SCENARIOS / "normal.json")
    assert result["suspected_root_cause"] == "No fault detected"

def test_baseline_imports_nothing_from_netcoop():
    # standalone, independent comparison point -- no EvidenceStore, no topology,
    # no checks, no shared code path with the real pipeline at all
    baseline, _ = _eval_modules()
    assert not any(name == "netcoop" or name.startswith("netcoop.") for name in vars(baseline)
                   if not name.startswith("__"))
    import inspect
    src = inspect.getsource(baseline)
    assert "import netcoop" not in src


# ---- eval/run_eval.py: metric computation -----------------------------------

def test_cause_matches_handles_none_and_prose():
    _, run_eval = _eval_modules()
    assert run_eval._cause_matches(None, None) is True
    assert run_eval._cause_matches("Link congestion", None) is False
    assert run_eval._cause_matches(None, "Link congestion") is False
    assert run_eval._cause_matches("Link congestion", "Link congestion (shared upstream X)") is True

def test_devices_match_uses_overlap_not_exact_set():
    _, run_eval = _eval_modules()
    assert run_eval._devices_match(["SW_1"], ["SW_1", "SW_2", "SW_3"]) is True
    assert run_eval._devices_match(["SW_9"], ["SW_1", "SW_2"]) is False
    assert run_eval._devices_match([], []) is True

def test_compute_metrics_flags_false_confident_diagnoses():
    _, run_eval = _eval_modules()
    rows = [
        {"expected_status": "insufficient_evidence",
         "pipeline": {"status": "insufficient_evidence", "status_correct": True,
                      "cause_correct": True, "devices_correct": True, "checks_used": 0, "latency_s": 0.01},
         "baseline": {"status": "diagnosed", "status_correct": False,
                      "cause_correct": False, "devices_correct": False, "checks_used": 0, "latency_s": 0.001}},
        {"expected_status": "diagnosed",
         "pipeline": {"status": "diagnosed", "status_correct": True,
                      "cause_correct": True, "devices_correct": True, "checks_used": 0, "latency_s": 0.01},
         "baseline": {"status": "diagnosed", "status_correct": True,
                      "cause_correct": True, "devices_correct": True, "checks_used": 0, "latency_s": 0.001}},
    ]
    pipeline_metrics = run_eval._compute_metrics(rows, "pipeline")
    baseline_metrics = run_eval._compute_metrics(rows, "baseline")
    assert pipeline_metrics["false_confident_diagnoses"] == 0
    assert baseline_metrics["false_confident_diagnoses"] == 1
    assert pipeline_metrics["correct_abstention_rate"] == 1.0
    assert baseline_metrics["correct_abstention_rate"] == 0.0


# ---- end-to-end: results.json / results.md ----------------------------------

def test_run_eval_writes_results_with_expected_shape(tmp_path, monkeypatch):
    # Orchestrator() inside _evaluate_scenario binds its logs_dir default at
    # def-time, so this still writes real logs/EVAL-*.jsonl as a side effect;
    # clean those up afterward rather than trying to redirect them.
    _, run_eval = _eval_modules()
    results_dir = tmp_path / "results"
    monkeypatch.setattr(run_eval, "RESULTS_DIR", results_dir)
    ground_truth = json.loads((ROOT / "scenarios" / "ground_truth.json").read_text(encoding="utf-8"))
    try:
        rc = run_eval.main()
    finally:
        for name in ground_truth:
            p = ROOT / "logs" / f"EVAL-{name}.jsonl"
            if p.exists():
                p.unlink()
    assert rc == 0
    data = json.loads((results_dir / "results.json").read_text(encoding="utf-8"))
    assert data["dataset_is_synthetic"] is True
    assert data["dataset_size"] == len(data["scenarios"])
    for system in ("pipeline", "baseline"):
        m = data["metrics"][system]
        for key in ("root_cause_accuracy", "affected_device_accuracy", "correct_abstention_rate",
                    "false_confident_diagnoses", "average_checks_used", "mean_latency_s"):
            assert key in m
    md_text = (results_dir / "results.md").read_text(encoding="utf-8")
    assert "synthetic" in md_text
    assert str(data["dataset_size"]) in md_text
