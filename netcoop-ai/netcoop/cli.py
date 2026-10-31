"""CLI entry points:
  python -m netcoop run <file> [--incident-id ID] [--topology ...] [--checks ...]
  python -m netcoop demo

`run` renders the inter-agent message timeline, hypotheses, checks performed,
and final report with rich, then writes reports/<incident_id>.json and .md.
`demo` walks through a fixed sequence of scenarios for a live presentation,
pausing for Enter between steps.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import render
from .orchestrator import Orchestrator
from .report import Report
from .topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "reports"


def _render_markdown(r: dict) -> str:
    lines = [
        f"# Diagnostic Report: {r['incident_id']}",
        "",
        f"**Status:** {r['status']}",
        f"**Suspected root cause:** {r['suspected_root_cause'] or '_none_'}",
        f"**Confidence:** {r['confidence']:.2f} ({r['confidence_category']})",
        f"**Affected devices:** {', '.join(r['affected_devices']) or '_none_'}",
        "",
        "## Supporting evidence",
    ]
    for ev in r["supporting_evidence"]:
        unit = ev["unit"] or ""
        lines.append(f"- `{ev['metric']}` = {ev['value']}{unit} — {ev['interpretation']} "
                      f"(obs `{ev['obs_id']}`, raw `{ev['raw_ref']}`)")
    if not r["supporting_evidence"]:
        lines.append("_none_")

    if r["alternative_causes"]:
        lines += ["", "## Alternative causes"] + [f"- {a}" for a in r["alternative_causes"]]

    lines += ["", "## Recommended actions"] + [f"- {a}" for a in r["recommended_actions"]]

    if r["limitations"]:
        lines += ["", "## Limitations"] + [f"- {lim}" for lim in r["limitations"]]

    lines += ["", "## Hypotheses considered"]
    for h in r["hypotheses"]:
        lines.append(f"- **{h['label']}** — score {h['score']:.2f}, coverage {h['coverage']:.2f}")

    if r["checks_performed"]:
        lines += ["", "## Checks performed"]
        for c in r["checks_performed"]:
            lines.append(f"- **{c['name']}** ({c['status']}): {c['result']}")

    lines += [
        "",
        f"**Evidence sources:** {', '.join(r['evidence_sources'])}",
        f"**Message log:** {r['message_log']}",
    ]
    return "\n".join(lines) + "\n"


def _execute(orchestrator: Orchestrator, file: str | Path, incident_id: str | None
             ) -> tuple[Report, Path, Path]:
    """Runs one incident and writes its report files. Returns (report, json_path, md_path)."""
    report = orchestrator.run(file, incident_id=incident_id)
    report_dict = report.model_dump(mode="json")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{report.incident_id}.json"
    md_path = REPORTS_DIR / f"{report.incident_id}.md"
    json_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report_dict), encoding="utf-8")
    return report, json_path, md_path


def run_command(args: argparse.Namespace) -> int:
    topology = load_topology(args.topology) if args.topology else None
    orchestrator = Orchestrator(topology=topology, checks_dir=args.checks)
    report, json_path, md_path = _execute(orchestrator, args.file, args.incident_id)
    render.render_full_report(report.model_dump(mode="json"), json_path, md_path)
    return 0


# ---- demo ------------------------------------------------------------------

DEMO_STEPS = [
    {
        "title": "Step 1/5 — one generator hardware_failure record",
        "file": "scenarios/inputs/schema_a_hardware_failure.json",
        "checks": None,
    },
    {
        "title": "Step 2/5 — ambiguous_loss, variant A (high upstream utilization)",
        "file": "scenarios/inputs/ambiguous_loss.json",
        "checks": "scenarios/checks/ambiguous_loss_A",
    },
    {
        "title": "Step 3/5 — ambiguous_loss, variant B (low utilization, rising errors)",
        "file": "scenarios/inputs/ambiguous_loss.json",
        "checks": "scenarios/checks/ambiguous_loss_B",
    },
    {
        "title": "Step 4/5 — Schema B interoperability (vendorB/2.0, same incident as step 1)",
        "file": "scenarios/inputs/schema_b_hardware_failure.json",
        "checks": None,
    },
    {
        "title": "Step 5/5 — broken input (one bad field rejected, diagnosis still runs)",
        "file": "scenarios/inputs/broken_unknown_field.json",
        "checks": None,
    },
]


def demo_command(args: argparse.Namespace, input_fn=input) -> int:
    topology_path = ROOT / "scenarios" / "topology.json"
    topology = load_topology(topology_path) if topology_path.exists() else None

    render.console.rule("[bold]NetCoop AI — Live Demo[/]")
    for i, step in enumerate(DEMO_STEPS, start=1):
        render.console.print()
        render.console.rule(f"[bold cyan]{step['title']}[/]")
        file_path = ROOT / step["file"]
        checks_dir = ROOT / step["checks"] if step["checks"] else None
        orchestrator = Orchestrator(topology=topology, checks_dir=checks_dir)
        incident_id = f"DEMO-{i}-{file_path.stem}"
        report, json_path, md_path = _execute(orchestrator, file_path, incident_id)
        render.render_full_report(report.model_dump(mode="json"), json_path, md_path)
        if i < len(DEMO_STEPS):
            input_fn("\nPress Enter to continue...")
    render.console.print()
    render.console.rule("[bold green]Demo complete[/]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcoop")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Diagnose a telemetry file")
    run_parser.add_argument("file", help="Path to a Schema A or Schema B JSON input file")
    run_parser.add_argument("--incident-id", default=None)
    run_parser.add_argument("--topology", default=None, help="Path to a topology.json file")
    run_parser.add_argument("--checks", default=None, help="Directory of targeted diagnostic check files")
    run_parser.set_defaults(func=run_command)

    demo_parser = sub.add_parser("demo", help="Walk through a live demo sequence")
    demo_parser.set_defaults(func=demo_command)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
