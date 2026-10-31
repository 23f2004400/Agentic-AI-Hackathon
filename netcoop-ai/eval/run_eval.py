#!/usr/bin/env python
"""Runs the orchestrator over every scenario in scenarios/ground_truth.json,
alongside the naive baseline.py (device_status only, no correlation), and
prints both against the documented ground truth. Exits non-zero if the real
pipeline disagrees with ground truth on any scenario's status.

This script reads ground_truth.json; the diagnosis pipeline itself never does
(CLAUDE.md rule 2) -- this is an external evaluator, not part of the agents.
"""
from __future__ import annotations

import json
import sys
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


def _cause_matches(cause: str | None, gt_cause: str | None) -> bool:
    if gt_cause is None:
        return cause is None
    if cause is None:
        return False
    return cause in gt_cause  # gt_cause may be prose for multi-fault scenarios


def main() -> int:
    ground_truth = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    topology = load_topology(TOPOLOGY_PATH)

    rows = []
    all_ok = True
    for name, gt in ground_truth.items():
        path = SCENARIOS_DIR / f"{name}.json"

        orchestrator = Orchestrator(topology=topology)
        report = orchestrator.run(path, incident_id=f"EVAL-{name}")
        status_ok = report.status == gt["expected_status"]
        cause_ok = _cause_matches(report.suspected_root_cause, gt["root_cause"])
        all_ok = all_ok and status_ok

        base = baseline.diagnose(path)
        base_status_ok = base["status"] == gt["expected_status"]
        base_cause_ok = _cause_matches(base["suspected_root_cause"], gt["root_cause"])

        rows.append({
            "scenario": name,
            "expected_status": gt["expected_status"],
            "pipeline_status": report.status, "pipeline_status_ok": status_ok,
            "pipeline_cause": report.suspected_root_cause, "pipeline_cause_ok": cause_ok,
            "confidence": report.confidence, "confidence_category": report.confidence_category,
            "additional_diagnoses": len(report.additional_diagnoses),
            "baseline_status": base["status"], "baseline_status_ok": base_status_ok,
            "baseline_cause": base["suspected_root_cause"], "baseline_cause_ok": base_cause_ok,
        })

    header = (f"{'scenario':18s} {'pipeline':>23s}   {'baseline':>23s}")
    print(header)
    print(f"{'':18s} {'status':20s}{'ok':>3s}   {'status':20s}{'ok':>3s}")
    print("-" * len(header + "   "))
    for r in rows:
        p_mark = "OK" if r["pipeline_status_ok"] else "XX"
        b_mark = "OK" if r["baseline_status_ok"] else "XX"
        print(f"{r['scenario']:18s} {r['pipeline_status']:20s}{p_mark:>3s}   "
              f"{r['baseline_status']:20s}{b_mark:>3s}")

    n = len(rows)
    pipeline_status_acc = sum(r["pipeline_status_ok"] for r in rows) / n
    pipeline_cause_acc = sum(r["pipeline_cause_ok"] for r in rows) / n
    baseline_status_acc = sum(r["baseline_status_ok"] for r in rows) / n
    baseline_cause_acc = sum(r["baseline_cause_ok"] for r in rows) / n
    print(f"\n{'':18s} {'status accuracy':20s} {'cause accuracy':20s}")
    print(f"{'pipeline':18s} {pipeline_status_acc:>16.0%} {pipeline_cause_acc:>20.0%}")
    print(f"{'baseline':18s} {baseline_status_acc:>16.0%} {baseline_cause_acc:>20.0%}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / "latest.json"
    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print("ALL PIPELINE STATUSES MATCH GROUND TRUTH" if all_ok else "SOME PIPELINE STATUSES DISAGREE WITH GROUND TRUTH")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
