import io
import json
import re
from pathlib import Path

import pytest
from rich.console import Console

from netcoop import cli, render
from netcoop.orchestrator import Orchestrator
from netcoop.topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Rich's auto-highlighter can inject color codes in the middle of a plain
    string (e.g. around something that looks like an exception class name),
    which breaks a naive literal substring search even though a human reading
    a real terminal sees perfectly contiguous text. Strip before asserting."""
    return _ANSI_RE.sub("", text)


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


@pytest.fixture()
def cleanup_demo_reports():
    yield
    for i, step in enumerate(cli.DEMO_STEPS, start=1):
        incident_id = f"DEMO-{i}-{Path(step['file']).stem}"
        for ext in ("json", "md"):
            p = cli.REPORTS_DIR / f"{incident_id}.{ext}"
            if p.exists():
                p.unlink()
        log_path = ROOT / "logs" / f"{incident_id}.jsonl"
        if log_path.exists():
            log_path.unlink()


# ---- --no-pause --------------------------------------------------------

def test_no_pause_flag_skips_all_prompts(monkeypatch, cleanup_demo_reports):
    _captured_console(monkeypatch)
    def failing_input(prompt=""):
        raise AssertionError("input_fn should never be called when --no-pause is set")
    args = argparse_namespace_stub()
    args.no_pause = True
    rc = cli.demo_command(args, input_fn=failing_input)
    assert rc == 0

def test_no_pause_flag_parses_via_main(monkeypatch):
    called = {}
    def fake_demo(args):
        called["no_pause"] = getattr(args, "no_pause", None)
        return 0
    monkeypatch.setattr(cli, "demo_command", fake_demo)
    rc = cli.main(["demo", "--no-pause"])
    assert rc == 0
    assert called["no_pause"] is True

def test_demo_without_no_pause_still_defaults_to_pausing(monkeypatch):
    # argparse's own default for a bare `demo` (no flag) must be False, not None/missing
    called = {}
    def fake_demo(args):
        called["no_pause"] = args.no_pause
        return 0
    monkeypatch.setattr(cli, "demo_command", fake_demo)
    cli.main(["demo"])
    assert called["no_pause"] is False


# ---- a single step failing must not crash the whole walkthrough ------------

def test_demo_step_failure_is_isolated_and_prints_one_line_no_traceback(monkeypatch, cleanup_demo_reports):
    buf = _captured_console(monkeypatch)
    real_orchestrator_cls = cli.Orchestrator

    class FlakyOrchestrator:
        """Raises only for step 1's file; delegates to the real Orchestrator otherwise."""
        def __init__(self, *a, **kw):
            self._real = real_orchestrator_cls(*a, **kw)
        def run(self, path, incident_id=None):
            if "schema_a_hardware_failure" in str(path):
                raise RuntimeError("deliberately broken step for the test")
            return self._real.run(path, incident_id=incident_id)

    monkeypatch.setattr(cli, "Orchestrator", FlakyOrchestrator)
    args = argparse_namespace_stub()
    args.no_pause = True
    rc = cli.demo_command(args, input_fn=lambda *_: "")
    out = _strip_ansi(buf.getvalue())

    assert rc == 0                         # the walkthrough as a whole still "succeeds"
    assert "Traceback" not in out          # no raw Python traceback reached the screen
    assert "Step 1 failed" in out
    assert "RuntimeError" in out
    assert "deliberately broken step for the test" in out
    # steps 2-5 still ran despite step 1 failing
    for i in range(2, len(cli.DEMO_STEPS) + 1):
        step = cli.DEMO_STEPS[i - 1]
        incident_id = f"DEMO-{i}-{Path(step['file']).stem}"
        assert (cli.REPORTS_DIR / f"{incident_id}.json").exists(), f"step {i} should still have run"
    assert "Demo complete" in out


# ---- readability at 100 columns (regression guard) --------------------------

def test_compact_list_bounds_length_and_summarizes_remainder():
    assert render._compact_list([]) == "-"
    assert render._compact_list(["a"]) == "a"
    assert render._compact_list(["sig_a", "sig_b", "sig_c", "sig_d"], max_items=2) == "sig_a, sig_b +2 more"
    truncated = render._compact_list(["x" * 50], max_items=2, max_chars=20)
    assert len(truncated) <= 20
    assert truncated.endswith("…")

def test_render_hypotheses_never_exceeds_the_configured_console_width(monkeypatch, logs_dir):
    # Regression guard: the old 3-column (Support/Contradictions/Missing) layout
    # wrapped long signal names one or two characters per line at 100 columns.
    buf = _captured_console(monkeypatch, width=100)
    o = Orchestrator(logs_dir=logs_dir)
    report = o.run(SCENARIOS / "hardware_failure.json", incident_id="M9-narrow")
    render.render_hypotheses(report.model_dump(mode="json"))
    out = _strip_ansi(buf.getvalue())
    for line in out.splitlines():
        assert len(line) <= 100, f"line exceeds 100 columns: {line!r}"
    assert "Hardware failure" in out


# ---- the actual behavior this audit was asked to verify --------------------

def test_ambiguous_loss_variants_still_produce_different_top_causes():
    # Direct, non-CLI confirmation of what test_demo_variants_a_and_b_produce_different_causes
    # already proves end to end -- kept as a fast, focused regression anchor.
    topo = load_topology(TOPOLOGY_PATH)
    report_a = Orchestrator(topology=topo, logs_dir=ROOT / "logs",
                             checks_dir=ROOT / "scenarios" / "checks" / "ambiguous_loss_A"
                             ).run(SCENARIOS / "ambiguous_loss.json", incident_id="M9-audit-a")
    report_b = Orchestrator(topology=topo, logs_dir=ROOT / "logs",
                             checks_dir=ROOT / "scenarios" / "checks" / "ambiguous_loss_B"
                             ).run(SCENARIOS / "ambiguous_loss.json", incident_id="M9-audit-b")
    try:
        assert report_a.suspected_root_cause != report_b.suspected_root_cause
        assert report_a.suspected_root_cause == "Link congestion"
        assert report_b.suspected_root_cause == "Interface or cabling fault on a single link"
    finally:
        for incident_id in ("M9-audit-a", "M9-audit-b"):
            p = ROOT / "logs" / f"{incident_id}.jsonl"
            if p.exists():
                p.unlink()
