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


def _compact_list(items: list[str], max_items: int = 2, max_chars: int = 26) -> str:
    """Bounds a list's rendered width so a table cell can never explode into a
    cascade of single-word-per-line wrapping on a narrow terminal -- a few
    items plus a "+N more" beats every item folded one syllable per line."""
    if not items:
        return "-"
    shown = items[:max_items]
    text = ", ".join(shown)
    remaining = len(items) - len(shown)
    if remaining > 0:
        text += f" +{remaining} more"
    if len(text) > max_chars:
        text = text[:max_chars - 1].rstrip() + "…"
    return text


def _envelope_summary(env: dict) -> str:
    parts = []
    if env.get("findings"):
        parts.append(f"findings={len(env['findings'])}")
    if env.get("missing"):
        parts.append(f"missing={len(env['missing'])}")
    if env.get("assumptions"):
        parts.append(f"assumptions={len(env['assumptions'])}")
    if env.get("errors"):
        parts.append(f"errors={_compact_list(env['errors'], max_items=1, max_chars=40)}")
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
    table.add_column("Summary", overflow="fold", max_width=48)
    for i, line in enumerate(log_path.read_text(encoding="utf-8").splitlines(), start=1):
        env = json.loads(line)
        style = TYPE_STYLES.get(env["type"], "white")
        table.add_row(str(i), Text(env["type"], style=style), env["sender"], env["recipient"],
                      _envelope_summary(env))
    console.print(table)


def render_first_pass(report_dict: dict) -> None:
    """The ranking snapshot recorded at the moment the first targeted check
    was requested -- shown before the (possibly different) final ranking so
    a viewer can see what changed and why a check was requested at all."""
    checks_performed = report_dict.get("checks_performed", [])
    before = checks_performed[0].get("before") if checks_performed else None
    if not before:
        return
    table = Table(title="First pass (before check)", expand=True)
    table.add_column("Hypothesis", min_width=16, no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_row(before["top_label"], f"{before['top_score']:.2f}")
    if before.get("second_label") is not None:
        table.add_row(before["second_label"], f"{before['second_score']:.2f}")
    console.print(table)
    console.print(before["reason"])


def render_hypotheses(report_dict: dict, title: str = "Hypotheses") -> None:
    # One bounded "Evidence" column rather than three narrow ones: splitting
    # support/contradictions/missing into separate columns leaves each so
    # narrow at a normal terminal width that long signal names (e.g.
    # "interface_errors_present") wrap one or two characters per line. Full,
    # untruncated lists are always in the JSON/Markdown report regardless.
    table = Table(title=title, expand=True)
    table.add_column("Hypothesis", min_width=16, no_wrap=True)
    table.add_column("Score", justify="right", no_wrap=True)
    table.add_column("Coverage", justify="right", no_wrap=True)
    table.add_column("Evidence", overflow="fold")
    top_hypothesis = report_dict["hypotheses"][0]["hypothesis"] if report_dict["hypotheses"] else None
    for h in report_dict["hypotheses"]:
        is_top = h["hypothesis"] == top_hypothesis
        label = Text(h["label"], style="bold" if is_top else None)
        evidence_parts = []
        if h["support"]:
            evidence_parts.append(f"+{_compact_list(h['support'])}")
        if h["contradictions"]:
            evidence_parts.append(f"-{_compact_list(h['contradictions'])}")
        if h["missing"]:
            evidence_parts.append(f"missing {_compact_list(h['missing'])}")
        table.add_row(label, f"{h['score']:.2f}", f"{h['coverage']:.2f}", "; ".join(evidence_parts) or "-")
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


def render_rejected_inputs(report_dict: dict) -> None:
    rejected = report_dict.get("rejected_inputs", [])
    if not rejected:
        console.print("[dim]Rejected inputs: none[/]")
        return
    table = Table(title="Rejected Inputs", expand=True)
    table.add_column("Raw ref", overflow="fold", max_width=40)
    table.add_column("Reason", overflow="fold")
    for r in rejected:
        table.add_row(r["raw_ref"], r["reason"])
    console.print(table)


def render_supporting_evidence(report_dict: dict) -> None:
    evidence = report_dict.get("supporting_evidence", [])[:5]
    if not evidence:
        console.print("[dim]Supporting evidence: none[/]")
        return
    table = Table(title="Supporting Evidence (top 5)", expand=True)
    table.add_column("Metric", no_wrap=True)
    table.add_column("Value", justify="right", no_wrap=True)
    table.add_column("Interpretation", overflow="fold")
    table.add_column("Raw ref", overflow="fold", max_width=32)
    for ev in evidence:
        unit = ev["unit"] or ""
        table.add_row(ev["metric"], f"{ev['value']}{unit}", ev["interpretation"], ev["raw_ref"])
    console.print(table)


def _render_short_list(title: str, items: list[str]) -> None:
    if not items:
        console.print(f"[bold]{title}:[/] [dim]none[/]")
        return
    console.print(f"[bold]{title}:[/]")
    for item in items:
        console.print(f"  - {item}")


def render_report_details(report_dict: dict) -> None:
    """Printed after the Diagnostic Report panel: rejected inputs, the
    top supporting evidence, and the short free-text lists from the report."""
    render_rejected_inputs(report_dict)
    console.print()
    render_supporting_evidence(report_dict)
    console.print()
    _render_short_list("Alternative causes", report_dict.get("alternative_causes", []))
    _render_short_list("Recommended actions", report_dict.get("recommended_actions", []))
    _render_short_list("Limitations", report_dict.get("limitations", []))


def render_full_report(report_dict: dict, json_path: Path, md_path: Path) -> None:
    """The full per-incident view used by both `run` and `demo`."""
    render_message_timeline(Path(report_dict["message_log"]))
    console.print()
    checks_ran = bool(report_dict.get("checks_performed"))
    if checks_ran:
        render_first_pass(report_dict)
        console.print()
    render_hypotheses(report_dict, title="Hypotheses (After check)" if checks_ran else "Hypotheses")
    console.print()
    render_checks(report_dict)
    console.print()
    render_report_summary(report_dict, json_path, md_path)
    console.print()
    render_report_details(report_dict)
