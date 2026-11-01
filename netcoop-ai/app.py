"""NetCoop AI — live diagnosis UI (Streamlit).

Runs the real orchestrator locally against a chosen scenario, a generated
live-looking record, or pasted telemetry JSON, and streams every real
Envelope as it's produced. Nothing here recomputes a diagnosis, simulates
progress, or adds artificial delay -- it's a thin display layer over
`netcoop.orchestrator.Orchestrator`, the same one `python -m netcoop run`
uses.

    streamlit run app.py

All module-level code below `main()` is import-safe (no Streamlit calls at
import time), so `run_diagnosis` can be imported and called directly from a
test without a running Streamlit app -- see tests/test_milestone10.py.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Callable

from netcoop.orchestrator import Orchestrator
from netcoop.protocol import Envelope
from netcoop.report import Report
from netcoop.topology import Topology, load_topology
from scripts.official_generator import generate_telemetry

ROOT = Path(__file__).resolve().parent
SCENARIOS_DIR = ROOT / "scenarios" / "inputs"
CHECKS_ROOT = ROOT / "scenarios" / "checks"
TOPOLOGY_PATH = ROOT / "scenarios" / "topology.json"

FAULT_TYPES = ["normal", "latency_spike", "hardware_failure"]


# ---- pure helpers (no Streamlit calls; safe to import and unit-test) -------

def list_scenarios() -> list[str]:
    if not SCENARIOS_DIR.exists():
        return []
    return sorted(p.stem for p in SCENARIOS_DIR.glob("*.json"))


def list_check_dirs() -> list[str]:
    if not CHECKS_ROOT.exists():
        return []
    return sorted(p.name for p in CHECKS_ROOT.iterdir() if p.is_dir())


def resolve_input_text(text: str) -> tuple[Path, bool]:
    """Resolves `text` -- a scenario name or raw pasted/generated JSON -- to a
    file path the orchestrator can ingest exactly like any other input file.

    Returns (path, is_temp); the caller must delete the path afterward when
    is_temp is True. Raises ValueError for a scenario name that doesn't exist
    -- malformed pasted JSON is *not* pre-validated here, since rejecting bad
    input is ingest's job (see netcoop/ingest.py), not the UI's.
    """
    stripped = text.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", prefix="netcoop_ui_", delete=False, encoding="utf-8"
        )
        tmp.write(stripped)
        tmp.close()
        return Path(tmp.name), True

    name = stripped if stripped.endswith(".json") else f"{stripped}.json"
    path = SCENARIOS_DIR / name
    if not path.exists():
        raise ValueError(f"No scenario named {stripped!r} in {SCENARIOS_DIR}")
    return path, False


def run_diagnosis(
    input_path: str | Path,
    *,
    checks_dir: str | Path | None = None,
    topology: Topology | None = None,
    incident_id: str | None = None,
    on_event: Callable[[Envelope], None] | None = None,
) -> Report:
    """Runs the real orchestrator against `input_path` and returns its Report.
    This is the one place app.py touches diagnosis at all -- it wires up the
    orchestrator (including the optional live-event callback) and nothing
    else; no rule, threshold, scoring, or ingest behavior lives here.
    """
    orchestrator = Orchestrator(topology=topology, checks_dir=checks_dir, on_event=on_event)
    return orchestrator.run(input_path, incident_id=incident_id)


def envelope_line(env: Envelope) -> str:
    """One line per message: sender -> recipient, type, findings/missing counts."""
    parts = [f"{env.sender} → {env.recipient}", env.type.value]
    parts.append(f"findings={len(env.findings)}")
    if env.missing:
        parts.append(f"missing={len(env.missing)}")
    if env.errors:
        parts.append(f"errors={len(env.errors)}")
    return " | ".join(parts)


# ---- Streamlit UI (only touched when this script is actually run) ---------

def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="NetCoop AI — Live Diagnosis", layout="wide")
    st.title("NetCoop AI — Live Diagnosis")
    st.caption(
        "Runs the real orchestrator locally. Every message shown below is a real "
        "`Envelope` produced during this run -- nothing is pre-recorded or simulated."
    )

    with st.sidebar:
        st.header("Scenario")
        scenario_names = list_scenarios()
        scenario = st.selectbox("From scenarios/inputs", ["(none)"] + scenario_names)
        check_dirs = list_check_dirs()
        checks_choice = st.selectbox("Checks directory (optional)", ["(none)"] + check_dirs)
        if scenario != "(none)" and st.button("Diagnose selected scenario"):
            st.session_state["pending_input"] = scenario

        st.divider()
        st.header("Generate a live record")
        fault_type = st.selectbox("Fault type", FAULT_TYPES)
        if st.button("Generate live record"):
            record = generate_telemetry(fault_type)
            st.session_state["last_generated"] = record
            st.session_state["pending_input"] = json.dumps(record)
        if "last_generated" in st.session_state:
            st.caption("Last generated record:")
            st.json(st.session_state["last_generated"])

    topology = load_topology(TOPOLOGY_PATH) if TOPOLOGY_PATH.exists() else None

    prompt = st.chat_input("Scenario name (e.g. ambiguous_loss) or pasted telemetry JSON")
    pending = st.session_state.pop("pending_input", None)
    user_input = prompt if prompt else pending

    if user_input:
        checks_dir = None if checks_choice == "(none)" else CHECKS_ROOT / checks_choice
        try:
            input_path, is_temp = resolve_input_text(user_input)
        except ValueError as e:
            st.error(str(e))
            input_path, is_temp = None, False

        if input_path is not None:
            events: list[Envelope] = []
            with st.status("Diagnosing…", expanded=True) as status:
                def on_event(env: Envelope, _status=status, _events=events) -> None:
                    _events.append(env)
                    _status.write(envelope_line(env))

                try:
                    report = run_diagnosis(
                        input_path, checks_dir=checks_dir, topology=topology, on_event=on_event,
                    )
                finally:
                    if is_temp:
                        input_path.unlink(missing_ok=True)
                status.update(label="Diagnosis complete", state="complete")

            st.session_state["last_result"] = {
                "user_input": user_input,
                "report_dict": report.model_dump(mode="json"),
                "event_lines": [envelope_line(e) for e in events],
            }

    result = st.session_state.get("last_result")
    if not result:
        st.info(
            "Pick a scenario in the sidebar, generate a live record, or type a scenario "
            "name / paste telemetry JSON below to diagnose."
        )
        return

    st.chat_message("user").write(result["user_input"][:500])

    with st.expander("Message timeline", expanded=False):
        for line in result["event_lines"]:
            st.write(line)

    _render_report(st, result["report_dict"])


def _render_report(st, report_dict: dict) -> None:
    checks_performed = report_dict.get("checks_performed", [])
    before = checks_performed[0].get("before") if checks_performed else None

    if before:
        st.subheader("First pass (before check)")
        rows = [{"hypothesis": before["top_label"], "score": before["top_score"]}]
        if before.get("second_label") is not None:
            rows.append({"hypothesis": before["second_label"], "score": before["second_score"]})
        st.table(rows)
        st.write(
            f"margin {before['margin']:.3f} < threshold {before['margin_threshold']} "
            f"→ requested `{checks_performed[0]['name']}`"
        )
        st.subheader("After check")
    else:
        st.subheader("Hypotheses")

    hyps = report_dict.get("hypotheses", [])
    if hyps:
        st.dataframe(
            [{"hypothesis": h["label"], "score": h["score"], "coverage": h["coverage"]} for h in hyps],
            width="stretch", hide_index=True,
        )
        st.bar_chart(
            {"hypothesis": [h["label"] for h in hyps], "score": [h["score"] for h in hyps]},
            x="hypothesis", y="score", horizontal=True,
        )
    else:
        st.write("No hypotheses recorded.")

    st.divider()
    st.subheader("Report")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status", report_dict["status"])
    col2.metric("Confidence", f"{report_dict['confidence']:.2f}", report_dict["confidence_category"])
    col3.metric("Root cause", report_dict["suspected_root_cause"] or "—")
    col4.metric("Affected devices", ", ".join(report_dict["affected_devices"]) or "—")

    st.markdown("**Rejected inputs**")
    rejected = report_dict.get("rejected_inputs", [])
    if rejected:
        st.dataframe(
            [{"raw_ref": r["raw_ref"], "reason": r["reason"]} for r in rejected],
            width="stretch", hide_index=True,
        )
    else:
        st.write("none")

    st.markdown("**Supporting evidence (top 5)**")
    evidence = report_dict.get("supporting_evidence", [])[:5]
    if evidence:
        st.dataframe(
            [{"metric": e["metric"], "value": f"{e['value']}{e['unit'] or ''}",
              "interpretation": e["interpretation"], "raw_ref": e["raw_ref"]} for e in evidence],
            width="stretch", hide_index=True,
        )
    else:
        st.write("none")

    for title, items in (
        ("Alternative causes", report_dict.get("alternative_causes", [])),
        ("Recommended actions", report_dict.get("recommended_actions", [])),
        ("Limitations", report_dict.get("limitations", [])),
    ):
        st.markdown(f"**{title}**")
        if items:
            for item in items:
                st.write(f"- {item}")
        else:
            st.write("none")

    st.download_button(
        "⬇ Download report JSON",
        data=json.dumps(report_dict, indent=2),
        file_name=f"{report_dict['incident_id']}.json",
        mime="application/json",
    )


if __name__ == "__main__":
    main()
