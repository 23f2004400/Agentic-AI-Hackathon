"""app.py's diagnosis helper, exercised directly -- never the Streamlit UI.
Importing `app` must not execute any Streamlit call (st.* lives only inside
main(), guarded by `if __name__ == "__main__"`), so this import is safe in a
plain pytest run with no Streamlit script context.
"""
from pathlib import Path

import app
from netcoop.protocol import Envelope
from netcoop.topology import load_topology

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "scenarios" / "inputs"
CHECKS_DIR = ROOT / "scenarios" / "checks"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"


def _cleanup_log(incident_id: str) -> None:
    path = ROOT / "logs" / f"{incident_id}.jsonl"
    if path.exists():
        path.unlink()


def test_ambiguous_loss_checks_a_and_b_via_app_helper_give_different_root_causes():
    topology = load_topology(TOPOLOGY_PATH)
    try:
        report_a = app.run_diagnosis(
            SCENARIOS / "ambiguous_loss.json",
            checks_dir=CHECKS_DIR / "ambiguous_loss_A",
            topology=topology,
            incident_id="M10-app-helper-a",
        )
        report_b = app.run_diagnosis(
            SCENARIOS / "ambiguous_loss.json",
            checks_dir=CHECKS_DIR / "ambiguous_loss_B",
            topology=topology,
            incident_id="M10-app-helper-b",
        )
        assert report_a.suspected_root_cause != report_b.suspected_root_cause
        assert report_a.suspected_root_cause == "Link congestion"
        assert report_b.suspected_root_cause == "Interface or cabling fault on a single link"
    finally:
        _cleanup_log("M10-app-helper-a")
        _cleanup_log("M10-app-helper-b")


def test_run_diagnosis_on_event_receives_real_envelopes_in_order():
    events: list[Envelope] = []
    incident_id = "M10-app-helper-events"
    try:
        report = app.run_diagnosis(
            SCENARIOS / "hardware_failure.json",
            incident_id=incident_id,
            on_event=events.append,
        )
        assert events, "on_event should have fired for every logged Envelope"
        assert all(isinstance(e, Envelope) for e in events)
        assert events[0].type.value == "task"
        assert events[-1].type.value == "report"
        assert events[-1].payload["incident_id"] == report.incident_id
    finally:
        _cleanup_log(incident_id)


def test_list_scenarios_and_check_dirs_find_real_bundled_files():
    scenarios = app.list_scenarios()
    assert "ambiguous_loss" in scenarios
    assert "hardware_failure" in scenarios

    check_dirs = app.list_check_dirs()
    assert "ambiguous_loss_A" in check_dirs
    assert "ambiguous_loss_B" in check_dirs


def test_resolve_input_text_scenario_name_and_pasted_json():
    path, is_temp = app.resolve_input_text("ambiguous_loss")
    assert path == SCENARIOS / "ambiguous_loss.json"
    assert is_temp is False

    path, is_temp = app.resolve_input_text('{"device_id": "SW_CAMPUS_1", "metrics": {}}')
    assert is_temp is True
    assert path.exists()
    path.unlink()


def test_resolve_input_text_unknown_scenario_name_raises():
    import pytest
    with pytest.raises(ValueError):
        app.resolve_input_text("not_a_real_scenario_name")
