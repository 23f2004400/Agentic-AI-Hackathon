"""Rich-based terminal rendering for the CLI: a colored per-agent message
timeline, a hypotheses table, a checks-performed table, and a report summary
panel. Pure presentation -- every function here reads Envelopes/Report dicts
that already exist; nothing here computes a diagnosis or simulates work.
"""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

TYPE_STYLES = {
    "task": "cyan",
    "findings": "green",
    "check_request": "yellow",
    "check_result": "bold yellow",
    "error": "bold red",
    "report": "bold magenta",
}

STATUS_STYLES = {
    "diagnosed": "bold green",
    "partial": "yellow",
    "insufficient_evidence": "yellow",
    "invalid_input": "bold red",
}

CATEGORY_STYLES = {"high": "bold green", "medium": "yellow", "low": "bold red"}

CHECK_STATUS_STYLES = {"applied": "green", "check_failed": "red"}


def _envelope_summary(env: dict) -> str:
    parts = []
    if env.get("findings"):
        parts.append(f"findings={len(env['findings'])}")
    if env.get("missing"):
        parts.append(f"missing={len(env['missing'])}")
    if env.get("assumptions"):
        parts.append(f"assumptions={len(env['assumptions'])}")
    if env.get("errors"):
        parts.append(f"errors={env['errors']}")
    payload = env.get("payload") or {}
    if env["type"] == "check_request":
        parts.append(f"check={payload.get('check')}")
    if env["type"] == "check_result":
        parts.append(f"status={payload.get('status')}, {payload.get('result', '')}")
    return ", ".join(parts) or "-"


def render_message_timeline(log_path: Path) -> None:
    if not log_path.exists():
        console.print(f"[dim]No message log found at {log_path}[/]")
        return
    table = Table(title="Message Timeline", show_lines=False, expand=True)
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Sender", no_wrap=True)
    table.add_column("Recipient", no_wrap=True)
    table.add_column("Summary", overflow="fold")
    for i, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        env = json.loads(line)
        style = TYPE_STYLES.get(env["type"], "white")
        table.add_row(str(i), Text(env["type"], style=style), env["sender"], env["recipient"],
                      _envelope_summary(env))
    console.print(table)


def render_hypotheses(report_dict: dict) -> None:
    table = Table(title="Hypotheses", expand=True)
    table.add_column("Hypothesis", min_width=14, no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Coverage", justify="right", no_wrap=True)
    table.add_column("Support", overflow="fold")
    table.add_column("Contradictions", overflow="fold")
    table.add_column("Missing", overflow="fold")
    top_hypothesis = report_dict["hypotheses"][0]["hypothesis"] if report_dict["hypotheses"] else None
    for h in report_dict["hypotheses"]:
        is_top = h["hypothesis"] == top_hypothesis
        label = Text(h["label"], style="bold" if is_top else None)
        table.add_row(label, f"{h['score']:.2f}", f"{h['coverage']:.2f}",
                      ", ".join(h["support"]) or "-", ", ".join(h["contradictions"]) or "-",
                      ", ".join(h["missing"]) or "-")
    console.print(table)
    category = report_dict["confidence_category"]
    style = CATEGORY_STYLES.get(category, "white")
    console.print(Panel(f"[{style}]{category.upper()}[/] confidence ({report_dict['confidence']:.2f})",
                         title="Confidence", width=40))


def render_checks(report_dict: dict) -> None:
    checks = report_dict.get("checks_performed", [])
    if not checks:
        console.print("[dim]No targeted checks were performed.[/]")
        return
    table = Table(title="Checks Performed", expand=True)
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Result")
    for c in checks:
        style = CHECK_STATUS_STYLES.get(c["status"], "white")
        table.add_row(c["name"], Text(c["status"], style=style), c["result"])
    console.print(table)


def render_report_summary(report_dict: dict, json_path: Path, md_path: Path) -> None:
    status = report_dict["status"]
    style = STATUS_STYLES.get(status, "white")
    body = (
        f"[bold]{report_dict['incident_id']}[/]\n"
        f"Status: [{style}]{status}[/]\n"
        f"Suspected root cause: {report_dict['suspected_root_cause'] or '[dim]none[/]'}\n"
        f"Confidence: {report_dict['confidence']:.2f} "
        f"([{CATEGORY_STYLES.get(report_dict['confidence_category'], '')}]"
        f"{report_dict['confidence_category']}[/])\n"
        f"Affected devices: {', '.join(report_dict['affected_devices']) or '[dim]none[/]'}\n\n"
        f"Report JSON: {json_path}\n"
        f"Report Markdown: {md_path}"
    )
    console.print(Panel(body, title="Diagnostic Report", border_style=style))


def render_full_report(report_dict: dict, json_path: Path, md_path: Path) -> None:
    """The full per-incident view used by both `run` and `demo`."""
    render_message_timeline(Path(report_dict["message_log"]))
    console.print()
    render_hypotheses(report_dict)
    console.print()
    render_checks(report_dict)
    console.print()
    render_report_summary(report_dict, json_path, md_path)
