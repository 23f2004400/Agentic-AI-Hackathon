"""NetCoop AI — incident viewer (Streamlit).

Reads already-completed incidents straight from reports/*.json and the
message log each report points to (logs/<incident_id>.jsonl). It never runs
the orchestrator and never simulates progress -- there's nothing to spin or
bar, since every incident shown here already finished.

Produce incidents first with:
    python -m netcoop run <file> [--topology ...] [--checks ...]
    python -m netcoop demo

Then view them with:
    streamlit run app.py
"""
from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
REPORTS_DIR = ROOT / "reports"

STATUS_LABELS = {
    "diagnosed": "🟢 diagnosed",
    "partial": "🟡 partial",
    "insufficient_evidence": "🟡 insufficient_evidence",
    "invalid_input": "🔴 invalid_input",
}
CATEGORY_LABELS = {"high": "🟢 high", "medium": "🟡 medium", "low": "🔴 low"}


def list_incidents() -> list[str]:
    if not REPORTS_DIR.exists():
        return []
    return sorted((p.stem for p in REPORTS_DIR.glob("*.json")), reverse=True)


def load_report(incident_id: str) -> dict | None:
    path = REPORTS_DIR / f"{incident_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_message_log(message_log_path: str) -> list[dict]:
    path = Path(message_log_path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def envelope_summary(env: dict) -> str:
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
        parts.append(f"status={payload.get('status')}")
    return ", ".join(parts) or "-"


st.set_page_config(page_title="NetCoop AI — Incident Viewer", layout="wide")
st.title("NetCoop AI — Incident Viewer")
st.caption(
    "Reads completed incidents straight from `reports/*.json` and the `logs/*.jsonl` message log "
    "each one points to. Nothing on this page is re-run, recomputed, or simulated."
)

incidents = list_incidents()
if not incidents:
    st.warning(
        f"No reports found in `{REPORTS_DIR}`. Run `python -m netcoop run <file>` or "
        "`python -m netcoop demo` first, then reload this page."
    )
    st.stop()

incident_id = st.selectbox("Incident", incidents)
report = load_report(incident_id)
if report is None:
    st.error(f"Could not load report for {incident_id}")
    st.stop()

# ---- summary header ---------------------------------------------------------
col1, col2, col3, col4 = st.columns(4)
col1.metric("Status", STATUS_LABELS.get(report["status"], report["status"]))
col2.metric("Confidence", f"{report['confidence']:.2f}",
            CATEGORY_LABELS.get(report["confidence_category"], report["confidence_category"]))
col3.metric("Suspected root cause", report["suspected_root_cause"] or "—")
col4.metric("Affected devices", ", ".join(report["affected_devices"]) or "—")

st.download_button(
    "⬇ Download report JSON",
    data=json.dumps(report, indent=2),
    file_name=f"{incident_id}.json",
    mime="application/json",
)

if report["additional_diagnoses"]:
    st.info(
        f"{len(report['additional_diagnoses'])} additional diagnosis(es) for devices in a "
        "non-overlapping topology scope: " +
        "; ".join(f"{d['suspected_root_cause'] or '—'} ({', '.join(d['scope_devices'])})"
                  for d in report["additional_diagnoses"])
    )

st.divider()

# ---- message timeline --------------------------------------------------------
st.subheader("Message timeline")
log_entries = load_message_log(report["message_log"])
if not log_entries:
    st.info(f"No message log found at `{report['message_log']}`.")
else:
    st.dataframe(
        [{"#": i, "type": env["type"], "sender": env["sender"], "recipient": env["recipient"],
          "summary": envelope_summary(env)}
         for i, env in enumerate(log_entries, start=1)],
        use_container_width=True, hide_index=True,
    )

# ---- hypotheses ---------------------------------------------------------------
st.subheader("Hypotheses")
if report["hypotheses"]:
    st.dataframe(
        [{"hypothesis": h["label"], "score": h["score"], "coverage": h["coverage"],
          "support": ", ".join(h["support"]) or "-",
          "contradictions": ", ".join(h["contradictions"]) or "-",
          "missing": ", ".join(h["missing"]) or "-"}
         for h in report["hypotheses"]],
        use_container_width=True, hide_index=True,
    )
else:
    st.info("No hypotheses recorded.")

# ---- checks performed -----------------------------------------------------------
st.subheader("Checks performed")
if report["checks_performed"]:
    st.dataframe(
        [{"name": c["name"], "status": c["status"], "result": c["result"]}
         for c in report["checks_performed"]],
        use_container_width=True, hide_index=True,
    )
else:
    st.info("No targeted checks were performed for this incident.")

# ---- supporting evidence / alternatives / limitations --------------------------
with st.expander(f"Supporting evidence ({len(report['supporting_evidence'])})"):
    if report["supporting_evidence"]:
        st.dataframe(report["supporting_evidence"], use_container_width=True, hide_index=True)
    else:
        st.write("None.")

with st.expander(f"Alternative causes ({len(report['alternative_causes'])})"):
    for cause in report["alternative_causes"]:
        st.write(f"- {cause}")
    if not report["alternative_causes"]:
        st.write("None.")

with st.expander(f"Limitations ({len(report['limitations'])})"):
    for lim in report["limitations"]:
        st.write(f"- {lim}")
    if not report["limitations"]:
        st.write("None.")

with st.expander(f"Rejected inputs ({len(report['rejected_inputs'])})"):
    if report["rejected_inputs"]:
        st.dataframe(report["rejected_inputs"], use_container_width=True, hide_index=True)
    else:
        st.write("None.")

st.divider()
st.caption(f"Report file: `{REPORTS_DIR / f'{incident_id}.json'}`")
st.caption(f"Message log: `{report['message_log']}`")
st.caption(f"Confidence method: {report['confidence_method']}")
