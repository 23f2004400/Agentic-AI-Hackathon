import io
import json
from pathlib import Path

import pytest
from rich.console import Console

from netcoop import cli, render
from netcoop.orchestrator import Orchestrator
from netcoop.topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"


@pytest.fixture()
def logs_dir(tmp_path):
    return tmp_path / "logs"


def _captured_console(monkeypatch, width: int = 120) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(render, "console", Console(file=buf, width=width, force_terminal=True))
    return buf


# ---- netcoop/render.py ------------------------------------------------------

def test_render_message_timeline_shows_each_envelope_type(monkeypatch, logs_dir):
    buf = _captured_console(monkeypatch)
    o = Orchestrator(logs_dir=logs_dir)
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M9-timeline")
    render.render_message_timeline(Path(report.message_log))
    out = buf.getvalue()
    assert "task" in out and "findings" in out and "report" in out
    assert "performance_agent" in out

def test_render_hypotheses_shows_score_and_confidence_category(monkeypatch, logs_dir):
    buf = _captured_console(monkeypatch)
    o = Orchestrator(logs_dir=logs_dir)
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M9-hyp")
    render.render_hypotheses(report.model_dump(mode="json"))
    out = buf.getvalue()
    assert "Hardware failure" in out
    assert "1.00" in out
    assert "HIGH" in out

def test_render_checks_handles_none_performed(monkeypatch, logs_dir):
    buf = _captured_console(monkeypatch)
    o = Orchestrator(logs_dir=logs_dir)
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M9-no-checks")
    render.render_checks(report.model_dump(mode="json"))
    assert "No targeted checks" in buf.getvalue()

def test_render_checks_shows_applied_and_failed_with_distinct_styling(monkeypatch, logs_dir):
    buf = _captured_console(monkeypatch)
    checks_dir = ROOT / "scenarios" / "checks" / "ambiguous_loss_A"
    o = Orchestrator(logs_dir=logs_dir, checks_dir=checks_dir)
    report = o.run(SCENARIOS / "ambiguous_loss.json", incident_id="M9-checks-applied")
    render.render_checks(report.model_dump(mode="json"))
    out = buf.getvalue()
    assert "upstream_link_utilization" in out
    assert "applied" in out

def test_render_report_summary_includes_report_paths(monkeypatch, logs_dir):
    buf = _captured_console(monkeypatch)
    o = Orchestrator(logs_dir=logs_dir)
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M9-summary")
    json_path, md_path = Path("fake.json"), Path("fake.md")
    render.render_report_summary(report.model_dump(mode="json"), json_path, md_path)
    out = buf.getvalue()
    assert "fake.json" in out
    assert "fake.md" in out
    assert "diagnosed" in out


# ---- CLI `run` with rich output --------------------------------------------

def test_cli_run_still_writes_report_files_with_rich_enabled(monkeypatch, logs_dir):
    _captured_console(monkeypatch)
    o = Orchestrator(logs_dir=logs_dir)
    report, json_path, md_path = cli._execute(o, SCENARIOS / "hardware_failure.json", "M9-cli-execute")
    assert json_path.exists() and md_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["status"] == "diagnosed"
    json_path.unlink()
    md_path.unlink()


# ---- `python -m netcoop demo` ----------------------------------------------

def test_demo_runs_all_five_steps_and_pauses_between_each(monkeypatch):
    _captured_console(monkeypatch)
    prompts = []
    def fake_input(prompt=""):
        prompts.append(prompt)
        return ""
    try:
        rc = cli.demo_command(argparse_namespace_stub(), input_fn=fake_input)
        assert rc == 0
        assert len(prompts) == len(cli.DEMO_STEPS) - 1  # no pause after the last step
        for i, step in enumerate(cli.DEMO_STEPS, start=1):
            incident_id = f"DEMO-{i}-{Path(step['file']).stem}"
            json_path = cli.REPORTS_DIR / f"{incident_id}.json"
            assert json_path.exists(), f"missing report for step {i}"
    finally:
        for i, step in enumerate(cli.DEMO_STEPS, start=1):
            incident_id = f"DEMO-{i}-{Path(step['file']).stem}"
            for ext in ("json", "md"):
                p = cli.REPORTS_DIR / f"{incident_id}.{ext}"
                if p.exists():
                    p.unlink()
            log_path = ROOT / "logs" / f"{incident_id}.jsonl"
            if log_path.exists():
                log_path.unlink()

def test_demo_variants_a_and_b_produce_different_causes(monkeypatch):
    _captured_console(monkeypatch)
    try:
        cli.demo_command(argparse_namespace_stub(), input_fn=lambda *_: "")
        report_a = json.loads((cli.REPORTS_DIR / "DEMO-2-ambiguous_loss.json").read_text(encoding="utf-8"))
        report_b = json.loads((cli.REPORTS_DIR / "DEMO-3-ambiguous_loss.json").read_text(encoding="utf-8"))
        assert report_a["suspected_root_cause"] == "Link congestion"
        assert report_b["suspected_root_cause"] == "Interface or cabling fault on a single link"
    finally:
        for i, step in enumerate(cli.DEMO_STEPS, start=1):
            incident_id = f"DEMO-{i}-{Path(step['file']).stem}"
            for ext in ("json", "md"):
                p = cli.REPORTS_DIR / f"{incident_id}.{ext}"
                if p.exists():
                    p.unlink()
            log_path = ROOT / "logs" / f"{incident_id}.jsonl"
            if log_path.exists():
                log_path.unlink()

def test_demo_subcommand_is_registered_and_dispatches(monkeypatch):
    # `python -m netcoop demo` must parse with no extra args and reach demo_command.
    called = []
    def fake_demo(args):
        called.append(True)
        return 0
    monkeypatch.setattr(cli, "demo_command", fake_demo)
    rc = cli.main(["demo"])
    assert rc == 0
    assert called == [True]


def argparse_namespace_stub():
    import argparse
    return argparse.Namespace()
