"""CLI entry point: `python -m netcoop run <file> [--incident-id ID]`.

Prints the inter-agent message flow from logs/<incident_id>.jsonl and the
final report, then writes reports/<incident_id>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .orchestrator import Orchestrator
from .topology import load_topology

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"


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

    lines += [
        "",
        "## Hypotheses considered",
    ]
    for h in r["hypotheses"]:
        lines.append(f"- **{h['label']}** — score {h['score']:.2f}, coverage {h['coverage']:.2f}")

    lines += [
        "",
        f"**Evidence sources:** {', '.join(r['evidence_sources'])}",
        f"**Message log:** {r['message_log']}",
    ]
    return "\n".join(lines) + "\n"


def _print_message_flow(log_path: Path) -> None:
    if not log_path.exists():
        return
    print("\n--- message flow ---")
    for line in log_path.read_text(encoding="utf-8").splitlines():
        env = json.loads(line)
        extra = ""
        if env.get("findings"):
            extra += f"  findings={len(env['findings'])}"
        if env.get("errors"):
            extra += f"  errors={env['errors']}"
        print(f"[{env['type']}] {env['sender']} -> {env['recipient']}{extra}")


def run_command(args: argparse.Namespace) -> int:
    topology = load_topology(args.topology) if args.topology else None
    orchestrator = Orchestrator(topology=topology)
    report = orchestrator.run(args.file, incident_id=args.incident_id)
    report_dict = report.model_dump(mode="json")

    _print_message_flow(Path(report.message_log))

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORTS_DIR / f"{report.incident_id}.json"
    md_path = REPORTS_DIR / f"{report.incident_id}.md"
    json_path.write_text(json.dumps(report_dict, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(report_dict), encoding="utf-8")

    print("\n--- report ---")
    print(json.dumps(report_dict, indent=2))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcoop")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Diagnose a telemetry file")
    run_parser.add_argument("file", help="Path to a Schema A or Schema B JSON input file")
    run_parser.add_argument("--incident-id", default=None)
    run_parser.add_argument("--topology", default=None, help="Path to a topology.json file")
    run_parser.set_defaults(func=run_command)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
