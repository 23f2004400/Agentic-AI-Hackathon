#!/usr/bin/env python
"""Evaluates the real NetCoop pipeline and the naive baseline.py threshold
classifier against every scenario in scenarios/ground_truth.json.

Metrics (computed for both systems):
  root_cause_accuracy        - over scenarios expected to be diagnosed, does
                                the predicted cause match ground truth?
  affected_device_accuracy   - over scenarios expected to be diagnosed, does
                                the predicted device set overlap ground truth's?
  correct_abstention_rate    - over scenarios expected to need abstention
                                (insufficient_evidence/invalid_input), did the
                                system's status also abstain, matching exactly?
  false_confident_diagnoses  - over that same abstain-worthy set, how often did
                                the system commit to a specific cause anyway?
                                This is the overconfidence failure mode
                                insufficient_evidence exists to avoid.
  average_checks_used        - mean number of *applied* targeted checks per
                                row, across ALL rows (always 0 for the
                                baseline, which has no concept of checks; 0
                                for the pipeline on most rows too -- see the
                                note on checks_dir below).
  mean_latency_s             - mean wall-clock seconds per scenario.

Checks are deliberately NOT enabled for most rows. scenarios/ground_truth.json's
expected_status documents the no-checks baseline (M4-M7's test suites already
assert against it that way); enabling checks_dir here would silently change
stale/insufficient/ambiguous_loss to "diagnosed" and make this script
disagree with ground truth and those tests for no good reason. Checks
resolving exactly those scenarios are proven separately in
tests/test_milestone6.py and via `python -m netcoop run ... --checks ...`.

The two exceptions are "ambiguous_loss__checks_A" and "ambiguous_loss__checks_B":
these rows reuse the ambiguous_loss.json input but each pass a `checks` dir
(scenarios/checks/ambiguous_loss_A / _B) through to the pipeline's Orchestrator,
so the eval harness itself (not just tests/test_milestone6.py) demonstrates a
targeted check changing the ranking end to end. A ground_truth.json row may
carry optional "input" (defaults to the row's own key) and "checks" (defaults
to none) fields to express this. The naive baseline has no checks mechanism at
all, so it always reads the input file directly regardless of these fields and
its checks_used is always 0.

Writes eval/results/results.json (full per-scenario + aggregate data) and
eval/results/results.md (human-readable report, states dataset size and that
it is synthetic).

This script reads ground_truth.json; the diagnosis pipeline itself never does
(CLAUDE.md rule 2) -- this is an external evaluator, not part of the agents.
rules.yaml is NOT tuned against these results in this evaluation step.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import baseline  # noqa: E402
from netcoop.orchestrator import Orchestrator  # noqa: E402
from netcoop.topology import load_topology  # noqa: E402

SCENARIOS_DIR = ROOT / "scenarios" / "inputs"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"
GROUND_TRUTH_PATH = ROOT / "scenarios" / "ground_truth.json"
RESULTS_DIR = ROOT / "eval" / "results"

ABSTAIN_STATUSES = ("insufficient_evidence", "invalid_input")


def _cause_matches(cause: str | None, gt_cause: str | None) -> bool:
    if gt_cause is None:
        return cause is None
    if cause is None:
        return False
    return cause in gt_cause  # gt_cause may be prose for multi-fault scenarios


def _devices_match(devices: list[str], gt_devices: list[str]) -> bool:
    if not gt_devices:
        return not devices
    return bool(set(devices) & set(gt_devices))


def _evaluate_scenario(name: str, gt: dict, topology) -> dict:
    path = SCENARIOS_DIR / f"{gt.get('input', name)}.json"
    checks_dir = (ROOT / "scenarios" / "checks" / gt["checks"]) if "checks" in gt else None

    t0 = time.perf_counter()
    orchestrator = Orchestrator(topology=topology, checks_dir=checks_dir)  # None for most rows; see module docstring
    report = orchestrator.run(path, incident_id=f"EVAL-{name}")
    pipeline_latency = time.perf_counter() - t0
    checks_used = sum(1 for c in report.checks_performed if c.status == "applied")

    t0 = time.perf_counter()
    base = baseline.diagnose(path)
    baseline_latency = time.perf_counter() - t0

    return {
        "scenario": name,
        "expected_status": gt["expected_status"], "expected_root_cause": gt["root_cause"],
        "expected_devices": gt["affected_devices"],
        "pipeline": {
            "status": report.status, "root_cause": report.suspected_root_cause,
            "affected_devices": report.affected_devices, "confidence": report.confidence,
            "confidence_category": report.confidence_category,
            "status_correct": report.status == gt["expected_status"],
            "cause_correct": _cause_matches(report.suspected_root_cause, gt["root_cause"]),
            "devices_correct": _devices_match(report.affected_devices, gt["affected_devices"]),
            "checks_used": checks_used, "latency_s": round(pipeline_latency, 4),
        },
        "baseline": {
            "status": base["status"], "root_cause": base["suspected_root_cause"],
            "affected_devices": base["affected_devices"],
            "status_correct": base["status"] == gt["expected_status"],
            "cause_correct": _cause_matches(base["suspected_root_cause"], gt["root_cause"]),
            "devices_correct": _devices_match(base["affected_devices"], gt["affected_devices"]),
            "checks_used": 0, "latency_s": round(baseline_latency, 4),
        },
    }


def _compute_metrics(rows: list[dict], system: str) -> dict:
    n = len(rows)
    diagnosed = [r for r in rows if r["expected_status"] == "diagnosed"]
    abstain = [r for r in rows if r["expected_status"] in ABSTAIN_STATUSES]
    n_diag, n_abstain = len(diagnosed) or 1, len(abstain) or 1

    false_confident = sum(r[system]["status"] == "diagnosed" for r in abstain)
    return {
        "root_cause_accuracy": sum(r[system]["cause_correct"] for r in diagnosed) / n_diag,
        "affected_device_accuracy": sum(r[system]["devices_correct"] for r in diagnosed) / n_diag,
        "correct_abstention_rate": sum(r[system]["status_correct"] for r in abstain) / n_abstain,
        "false_confident_diagnoses": false_confident,
        "false_confident_diagnoses_rate": false_confident / n_abstain,
        "average_checks_used": sum(r[system]["checks_used"] for r in rows) / n,
        "mean_latency_s": round(sum(r[system]["latency_s"] for r in rows) / n, 5),
        "overall_status_accuracy": sum(r[system]["status_correct"] for r in rows) / n,
        "n_scenarios": n, "n_diagnosed_expected": len(diagnosed), "n_abstain_expected": len(abstain),
    }


def _render_console_table(rows: list[dict]) -> str:
    header = f"{'scenario':18s} {'pipeline status':22s}{'ok':>3s}   {'baseline status':22s}{'ok':>3s}"
    lines = [header, "-" * len(header)]
    for r in rows:
        p, b = r["pipeline"], r["baseline"]
        lines.append(f"{r['scenario']:18s} {p['status']:22s}{'OK' if p['status_correct'] else 'XX':>3s}   "
                      f"{b['status']:22s}{'OK' if b['status_correct'] else 'XX':>3s}")
    return "\n".join(lines)


def _render_metrics_table(metrics: dict) -> str:
    rows = [
        ("Root-cause accuracy", "root_cause_accuracy", "pct"),
        ("Affected-device accuracy", "affected_device_accuracy", "pct"),
        ("Correct abstention rate", "correct_abstention_rate", "pct"),
        ("False confident diagnoses", "false_confident_diagnoses", "count_rate"),
        ("Average checks used", "average_checks_used", "num"),
        ("Mean latency (s)", "mean_latency_s", "num4"),
        ("Overall status accuracy", "overall_status_accuracy", "pct"),
    ]
    lines = [f"{'Metric':30s} {'NetCoop pipeline':>18s} {'Naive baseline':>16s}"]
    lines.append("-" * len(lines[0]))
    for label, key, fmt in rows:
        p, b = metrics["pipeline"][key], metrics["baseline"][key]
        if fmt == "pct":
            pv, bv = f"{p:.0%}", f"{b:.0%}"
        elif fmt == "count_rate":
            pv = f"{p}/{metrics['pipeline']['n_abstain_expected']} ({metrics['pipeline']['false_confident_diagnoses_rate']:.0%})"
            bv = f"{b}/{metrics['baseline']['n_abstain_expected']} ({metrics['baseline']['false_confident_diagnoses_rate']:.0%})"
        elif fmt == "num4":
            pv, bv = f"{p:.4f}", f"{b:.4f}"
        else:
            pv, bv = f"{p:.2f}", f"{b:.2f}"
        lines.append(f"{label:30s} {pv:>18s} {bv:>16s}")
    return "\n".join(lines)


def _render_markdown(results: dict) -> str:
    rows, metrics = results["scenarios"], results["metrics"]
    lines = [
        "# NetCoop AI — Evaluation Results", "",
        f"**Dataset:** {results['dataset_size']} rows, entirely **synthetic** "
        "(generated by `scripts/gen_scenarios.py` with a fixed random seed; no production "
        "campus network data was used or is required). Ground truth is documented in "
        "`scenarios/ground_truth.json` and read only by this script, never by the diagnosis "
        "pipeline itself.", "",
        f"Of those {results['dataset_size']} rows, two (`ambiguous_loss__checks_A` and "
        "`ambiguous_loss__checks_B`) reuse the `ambiguous_loss` input file but each pass a "
        "different targeted-check directory to the pipeline, exercising `checks_dir` end to "
        "end; every other row runs with no checks dir, as before.", "",
        "`rules.yaml` was **not** tuned against these results in this evaluation step.", "",
        "## Metrics", "",
        "| Metric | NetCoop pipeline | Naive baseline |",
        "|---|---|---|",
    ]
    metric_rows = [
        ("Root-cause accuracy", "root_cause_accuracy", "pct"),
        ("Affected-device accuracy", "affected_device_accuracy", "pct"),
        ("Correct abstention rate", "correct_abstention_rate", "pct"),
        ("False confident diagnoses", "false_confident_diagnoses", "count_rate"),
        ("Average checks used", "average_checks_used", "num2"),
        ("Mean latency (s)", "mean_latency_s", "num4"),
        ("Overall status accuracy", "overall_status_accuracy", "pct"),
    ]
    for label, key, fmt in metric_rows:
        p, b = metrics["pipeline"][key], metrics["baseline"][key]
        if fmt == "pct":
            pv, bv = f"{p:.0%}", f"{b:.0%}"
        elif fmt == "count_rate":
            pv = f"{p}/{metrics['pipeline']['n_abstain_expected']} ({metrics['pipeline']['false_confident_diagnoses_rate']:.0%})"
            bv = f"{b}/{metrics['baseline']['n_abstain_expected']} ({metrics['baseline']['false_confident_diagnoses_rate']:.0%})"
        elif fmt == "num4":
            pv, bv = f"{p:.4f}", f"{b:.4f}"
        else:
            pv, bv = f"{p:.2f}", f"{b:.2f}"
        lines.append(f"| {label} | {pv} | {bv} |")

    n_diag, n_abstain = metrics["pipeline"]["n_diagnosed_expected"], metrics["pipeline"]["n_abstain_expected"]
    lines += [
        "", f"Root-cause and affected-device accuracy are computed over the "
        f"{n_diag} scenario(s) ground truth expects to be diagnosed. Correct abstention rate and "
        f"false confident diagnoses are computed over the {n_abstain} scenario(s) ground truth "
        "expects to need abstention (`insufficient_evidence` or `invalid_input`).",
        "", "## Per-scenario results", "",
        "| scenario | expected | pipeline status | pipeline cause | baseline status | baseline cause |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        p, b = r["pipeline"], r["baseline"]
        p_mark, b_mark = ("✓" if p["status_correct"] else "✗"), ("✓" if b["status_correct"] else "✗")
        lines.append(
            f"| {r['scenario']} | {r['expected_status']} | {p['status']} {p_mark} | "
            f"{p['root_cause'] or '—'} | {b['status']} {b_mark} | {b['root_cause'] or '—'} |"
        )

    lines += [
        "", "## Notes", "",
        "- The naive baseline is a single-pass threshold classifier reading raw metric values "
        "directly off the latest record in each file -- no `EvidenceStore`/grounding, no "
        "topology, no targeted checks, and **no abstention** (it always commits to a specific "
        "cause). Its false-confident-diagnoses rate is the clearest illustration of why "
        "`insufficient_evidence` exists as a status.",
        "- Most rows deliberately don't supply a checks dir, so \"average checks used\" is 0 for "
        "the baseline on every row and 0 for the pipeline on most rows: `ground_truth.json`'s "
        "`expected_status` documents the no-checks baseline for those (several test suites already "
        "assert against it that way), and enabling checks there would silently resolve "
        "`stale`/`insufficient`/`ambiguous_loss` to `diagnosed`, disagreeing with that ground truth "
        "for no good reason. The two `ambiguous_loss__checks_*` rows are the deliberate exception: "
        "they pass `scenarios/checks/ambiguous_loss_A` / `_B` through to the pipeline, which is why "
        "\"average checks used\" for the pipeline is nonzero overall even though it's 0 per-row "
        "everywhere else. The naive baseline has no checks mechanism at all and always ignores "
        "these directories. Targeted checks are also proven separately in "
        "`tests/test_milestone6.py` and via `python -m netcoop run ... --checks scenarios/checks/<name>`.",
        "- Affected-device accuracy counts a prediction correct if it overlaps ground truth's "
        "device set at all, since the pipeline documented-ly reports its single strongest device "
        "for multi-device incidents rather than every affected device.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    topology = load_topology(TOPOLOGY_PATH)

    rows = [_evaluate_scenario(name, gt, topology) for name, gt in ground_truth.items()]
    metrics = {system: _compute_metrics(rows, system) for system in ("pipeline", "baseline")}
    results = {"dataset_size": len(rows), "dataset_is_synthetic": True, "scenarios": rows, "metrics": metrics}

    print(_render_console_table(rows))
    print()
    print(_render_metrics_table(metrics))

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (RESULTS_DIR / "results.md").write_text(_render_markdown(results), encoding="utf-8")
    print(f"\nWrote {RESULTS_DIR / 'results.json'}")
    print(f"Wrote {RESULTS_DIR / 'results.md'}")

    all_status_ok = all(r["pipeline"]["status_correct"] for r in rows)
    print("ALL PIPELINE STATUSES MATCH GROUND TRUTH" if all_status_ok
          else "SOME PIPELINE STATUSES DISAGREE WITH GROUND TRUTH")
    return 0 if all_status_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
