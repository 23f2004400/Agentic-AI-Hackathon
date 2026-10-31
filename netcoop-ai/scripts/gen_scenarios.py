#!/usr/bin/env python
"""Generates reproducible scenario input files for netcoop.

Extends the official generator format (docs/Mock_data_builder_netScoopAI.ipynb)
with explicit multi-sample time series (6 samples per device) and a broader
set of fault types than the sample generator covers. All metric values are
drawn from a seeded random generator, so re-running this script produces the
same values every time; only the timestamps shift, anchored to wall-clock
"now" at generation time, the way real telemetry timestamps would.

Writes:
  scenarios/inputs/<name>.json           (Schema A, one list of records)
  scenarios/inputs/schema_b_<name>.json  (Schema B equivalent, for 3 scenarios)
  scenarios/checks/<name>/<device>__<metric>.json  (targeted check files, for 2 scenarios)
  scenarios/ground_truth.json            ({scenario: {root_cause, affected_devices, expected_status}})

Check files are generated here too (not hand-authored) so their timestamps
stay anchored the same way as the scenario files they belong to -- a
hardcoded check timestamp would eventually look older than the scenario's
own "fresh" samples once this script is re-run on a later date.

Agents must never read ground_truth.json -- it exists for human review and a
future eval harness, not for the diagnosis pipeline.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 20261031
random.seed(SEED)

ROOT = Path(__file__).resolve().parent.parent
INPUTS_DIR = ROOT / "scenarios" / "inputs"
CHECKS_DIR = ROOT / "scenarios" / "checks"
GROUND_TRUTH_PATH = ROOT / "scenarios" / "ground_truth.json"

NOW = datetime.now(timezone.utc).replace(microsecond=0)
SAMPLE_INTERVAL = timedelta(minutes=2)

SCHEMA_B_SCENARIOS = {"normal", "congestion", "cpu_overload"}
# Note: not "hardware_failure" -- that name collides with the hand-crafted
# scenarios/inputs/schema_b_hardware_failure.json fixture from Milestone 2
# (tests/test_milestone2.py's cross-schema equivalence test depends on it).


# ---- helpers ---------------------------------------------------------------

def sample_times(n: int = 6, end: datetime = NOW, interval: timedelta = SAMPLE_INTERVAL) -> list[datetime]:
    return [end - interval * (n - 1 - i) for i in range(n)]


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def record(device: str, ts: datetime, metrics: dict, status: str) -> dict:
    return {"timestamp": iso(ts), "device_id": device, "metrics": metrics, "status": status}


def jitter(base: float, spread: float, digits: int = 2) -> float:
    return round(base + random.uniform(-spread, spread), digits)


def to_schema_b(records: list[dict]) -> list[dict]:
    """Converts a list of Schema A records into the equivalent Schema B (vendorB/2.0)
    records, using the same field names/units as netcoop/mappings/vendor_b.yaml."""
    out = []
    for rec in records:
        m = rec["metrics"]
        ts = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
        b = {"schema": "vendorB/2.0", "hostname": rec["device_id"], "ts": int(ts.timestamp()),
             "health": rec["status"]}
        if "latency_ms" in m:
            b["lat_s"] = round(m["latency_ms"] / 1000, 6)
        if "jitter_ms" in m:
            b["jitter_s"] = round(m["jitter_ms"] / 1000, 6)
        if "packet_loss_pct" in m:
            b["loss_ratio"] = round(m["packet_loss_pct"] / 100, 6)
        if "link_utilization_pct" in m:
            b["link_util_pct"] = m["link_utilization_pct"]
        if "cpu_utilization_pct" in m:
            b["cpu_pct"] = m["cpu_utilization_pct"]
        if "queue_drops" in m:
            b["q_drops"] = m["queue_drops"]
        if "interface_errors" in m:
            b["if_errors"] = m["interface_errors"]
        out.append(b)
    return out


# ---- per-scenario record builders ------------------------------------------

def gen_normal(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(22, 6), "link_utilization_pct": jitter(35, 8),
        "packet_loss_pct": 0.0, "latency_ms": jitter(14, 4), "jitter_ms": jitter(2, 1),
        "queue_drops": 0,
    }, "Healthy") for t in sample_times()]


def gen_congestion(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(58, 6), "link_utilization_pct": jitter(92, 3),
        "packet_loss_pct": jitter(2.5, 1), "latency_ms": jitter(240, 30), "jitter_ms": jitter(20, 5),
        "queue_drops": random.randint(300, 600),
    }, "Warning") for t in sample_times()]


def gen_interface_fault(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(30, 5), "link_utilization_pct": jitter(40, 6),
        "packet_loss_pct": jitter(9, 2), "latency_ms": jitter(45, 10),
        "interface_errors": random.randint(40, 90), "queue_drops": random.randint(0, 20),
    }, "Warning") for t in sample_times()]


def gen_weak_wifi(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(20, 5), "link_utilization_pct": jitter(25, 8),
        "packet_loss_pct": jitter(3, 1), "latency_ms": jitter(55, 10),
        "rssi_dbm": jitter(-82, 4), "retransmission_pct": jitter(22, 5),
    }, "Warning") for t in sample_times()]


def gen_dns_dhcp_failure(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(18, 4), "link_utilization_pct": jitter(20, 5),
        "packet_loss_pct": jitter(0.5, 0.3), "latency_ms": jitter(15, 4),
        "dns_failure_pct": jitter(35, 8), "dhcp_failures": random.randint(5, 20),
    }, "Warning") for t in sample_times()]


def gen_cpu_overload(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(96, 2), "link_utilization_pct": jitter(40, 8),
        "packet_loss_pct": jitter(0.3, 0.2), "latency_ms": jitter(220, 30),
        "queue_drops": random.randint(0, 10),
    }, "Warning") for t in sample_times()]


def gen_hardware_failure(device: str) -> list[dict]:
    return [record(device, t, {
        "cpu_utilization_pct": jitter(99.5, 0.4), "link_utilization_pct": jitter(99, 1),
        "packet_loss_pct": jitter(27, 4), "latency_ms": jitter(880, 80),
        "queue_drops": random.randint(9000, 15000), "interface_errors": random.randint(200, 450),
    }, "Critical") for t in sample_times()]


def gen_upstream_failure(devices: list[str]) -> list[dict]:
    out = []
    for device in devices:
        out += [record(device, t, {
            "cpu_utilization_pct": jitter(45, 6), "link_utilization_pct": jitter(90, 3),
            "packet_loss_pct": jitter(3, 1), "latency_ms": jitter(190, 20),
            "queue_drops": random.randint(150, 300),
        }, "Warning") for t in sample_times()]
    return out


def gen_insufficient(device: str) -> list[dict]:
    return [record(device, t, {"queue_drops": random.randint(0, 5)}, "Warning") for t in sample_times()]


def gen_stale(device: str) -> list[dict]:
    """5 fresh samples carry the metrics that would otherwise point at congestion;
    the only interface_errors reading is from 25 minutes ago, well past EvidenceStore's
    10-minute max_age, so it must be excluded from analysis as stale."""
    recs = [record(device, t, {
        "cpu_utilization_pct": jitter(45, 5), "link_utilization_pct": jitter(55, 6),
        "packet_loss_pct": jitter(8, 1.5), "latency_ms": jitter(60, 10),
    }, "Warning") for t in sample_times(5)]
    recs.append(record(device, NOW - timedelta(minutes=25), {"interface_errors": 220}, "Warning"))
    return recs


def gen_contradictory(device: str) -> list[dict]:
    """high_latency fires (supports link_congestion + cpu_overload) while
    low_link_utilization and normal_cpu both fire at the same time (contradicting
    both of those same hypotheses) -- a genuinely ambiguous picture."""
    return [record(device, t, {
        "cpu_utilization_pct": jitter(35, 5), "link_utilization_pct": jitter(28, 5),
        "packet_loss_pct": jitter(1, 0.3), "latency_ms": jitter(410, 25),
    }, "Warning") for t in sample_times()]


def gen_simultaneous() -> list[dict]:
    return gen_interface_fault("SW_CAMPUS_8") + gen_weak_wifi("AP_HOSTEL_2")


def gen_ambiguous_loss(device: str) -> list[dict]:
    """link_congestion and interface_fault are both plausible and neither is
    confident: elevated packet loss (weakly supports both), link utilization
    just under the congestion threshold, latency unremarkable, and no
    interface_errors reported at all -- that missing reading is exactly what
    rules.yaml's upstream_link_utilization check (declared to discriminate
    this pair) will reveal."""
    return [record(device, t, {
        "cpu_utilization_pct": jitter(40, 5), "link_utilization_pct": jitter(78, 4),
        "packet_loss_pct": jitter(8, 1.5), "latency_ms": jitter(100, 15),
        "queue_drops": random.randint(20, 70),
    }, "Warning") for t in sample_times()]


# ---- targeted check files ---------------------------------------------------
# {checks_dir_name: {check_name: record}} -- orchestrator looks these up as
# <checks_dir>/<check_name>.json when the top two hypotheses are too close to
# call and rules.yaml declares a check that discriminates between them.
# Timestamped after the relevant scenario's own samples so they read as the
# freshest available data once applied. A checks_dir_name need not match a
# SCENARIOS key one-for-one -- ambiguous_loss_A/_B are two alternate takes on
# the same "ambiguous_loss" scenario, selected via --checks.

CHECKS: dict[str, dict[str, dict]] = {
    "stale": {
        # link_congestion vs interface_fault, same pair as ambiguous_loss below,
        # so the same declared check (upstream_link_utilization) applies. A real
        # re-poll of the uplink naturally returns more than one counter: low
        # utilization rules out congestion further, and the interface_errors
        # reading confirms the one the "stale" scenario had to exclude.
        "upstream_link_utilization": record("SW_CAMPUS_5", NOW + timedelta(minutes=1), {
            "link_utilization_pct": 35.0, "interface_errors": 85,
        }, "Warning"),
    },
    "insufficient": {
        # A full re-poll of the device that reported almost nothing -- a check
        # naturally returns everything currently available, not just the one
        # metric that was missing.
        "upstream_link_utilization": record("SW_CAMPUS_9", NOW + timedelta(minutes=1), {
            "cpu_utilization_pct": jitter(24, 4), "link_utilization_pct": jitter(28, 5),
            "packet_loss_pct": 0.0, "latency_ms": jitter(13, 3), "queue_drops": random.randint(0, 3),
        }, "Healthy"),
    },
    "ambiguous_loss_A": {
        # High upstream utilization + elevated queue drops: confirms congestion.
        "upstream_link_utilization": record("SW_CAMPUS_1", NOW + timedelta(minutes=1), {
            "link_utilization_pct": 93.0, "queue_drops": 180,
        }, "Warning"),
    },
    "ambiguous_loss_B": {
        # Low upstream utilization (rules out congestion) + rising interface
        # errors: confirms an interface fault instead.
        "upstream_link_utilization": record("SW_CAMPUS_1", NOW + timedelta(minutes=1), {
            "link_utilization_pct": 42.0, "interface_errors": 48,
        }, "Warning"),
    },
    # Deliberately no check for "contradictory" or the M3 latency_spike sample:
    # CLAUDE.md is explicit that util 60-85 with few drops proves no cause, and
    # checks must never be used to force a conclusion that isn't there.
}


# ---- assembly ---------------------------------------------------------------

SCENARIOS: dict[str, list[dict]] = {
    "normal": gen_normal("SW_CAMPUS_1"),
    "congestion": gen_congestion("SW_CAMPUS_6"),
    "interface_fault": gen_interface_fault("SW_CAMPUS_2"),
    "weak_wifi": gen_weak_wifi("AP_HOSTEL_1"),
    "dns_dhcp_failure": gen_dns_dhcp_failure("DNS_DHCP_01"),
    "cpu_overload": gen_cpu_overload("SW_CAMPUS_7"),
    "hardware_failure": gen_hardware_failure("SW_CAMPUS_4"),
    "upstream_failure": gen_upstream_failure(["SW_CAMPUS_1", "SW_CAMPUS_2", "SW_CAMPUS_3"]),
    "simultaneous": gen_simultaneous(),
    "insufficient": gen_insufficient("SW_CAMPUS_9"),
    "stale": gen_stale("SW_CAMPUS_5"),
    "contradictory": gen_contradictory("SW_CAMPUS_10"),
    "ambiguous_loss": gen_ambiguous_loss("SW_CAMPUS_1"),
}

# Documents the intended diagnosis for human review / a future eval harness.
# Not read by any agent (CLAUDE.md rule 2) -- only by this script and whoever
# inspects it by hand.
GROUND_TRUTH: dict[str, dict] = {
    "normal": {"root_cause": "No fault detected", "affected_devices": ["SW_CAMPUS_1"],
               "expected_status": "diagnosed"},
    "congestion": {"root_cause": "Link congestion", "affected_devices": ["SW_CAMPUS_6"],
                   "expected_status": "diagnosed"},
    "interface_fault": {"root_cause": "Interface or cabling fault on a single link",
                         "affected_devices": ["SW_CAMPUS_2"], "expected_status": "diagnosed"},
    "weak_wifi": {"root_cause": "Weak wireless signal", "affected_devices": ["AP_HOSTEL_1"],
                  "expected_status": "diagnosed"},
    "dns_dhcp_failure": {"root_cause": "DNS or DHCP service failure", "affected_devices": ["DNS_DHCP_01"],
                         "expected_status": "diagnosed"},
    "cpu_overload": {"root_cause": "Device CPU overload", "affected_devices": ["SW_CAMPUS_7"],
                      "expected_status": "diagnosed"},
    "hardware_failure": {"root_cause": "Hardware failure", "affected_devices": ["SW_CAMPUS_4"],
                          "expected_status": "diagnosed"},
    "upstream_failure": {
        "root_cause": "Link congestion (shared upstream DIST_SW_HOSTEL; see connectivity scope finding)",
        "affected_devices": ["SW_CAMPUS_1", "SW_CAMPUS_2", "SW_CAMPUS_3"], "expected_status": "diagnosed",
    },
    "simultaneous": {
        "root_cause": "Two independent faults in non-overlapping topology scopes: primary diagnosis "
                       "Weak wireless signal (AP_HOSTEL_2); additional_diagnoses carries Interface or "
                       "cabling fault on a single link (SW_CAMPUS_8)",
        "affected_devices": ["SW_CAMPUS_8", "AP_HOSTEL_2"], "expected_status": "diagnosed",
    },
    "insufficient": {"root_cause": None, "affected_devices": ["SW_CAMPUS_9"],
                      "expected_status": "insufficient_evidence"},
    "stale": {"root_cause": None, "affected_devices": ["SW_CAMPUS_5"],
              "expected_status": "insufficient_evidence"},
    "contradictory": {"root_cause": None, "affected_devices": ["SW_CAMPUS_10"],
                       "expected_status": "insufficient_evidence"},
    "ambiguous_loss": {
        "root_cause": None, "affected_devices": ["SW_CAMPUS_1"],
        "expected_status": "insufficient_evidence",
        # Genuinely tied between link_congestion and interface_fault without a
        # check; --checks scenarios/checks/ambiguous_loss_A resolves it to Link
        # congestion, --checks scenarios/checks/ambiguous_loss_B resolves it to
        # Interface or cabling fault on a single link. See tests/test_milestone6.py.
    },
}


def main() -> None:
    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, records in SCENARIOS.items():
        (INPUTS_DIR / f"{name}.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
        if name in SCHEMA_B_SCENARIOS:
            b_records = to_schema_b(records)
            (INPUTS_DIR / f"schema_b_{name}.json").write_text(
                json.dumps(b_records, indent=2), encoding="utf-8")

    n_checks = 0
    for checks_dir_name, by_check in CHECKS.items():
        checks_dir = CHECKS_DIR / checks_dir_name
        checks_dir.mkdir(parents=True, exist_ok=True)
        for old_file in checks_dir.glob("*.json"):  # drop stale filenames from a prior naming scheme
            if old_file.stem not in by_check:
                old_file.unlink()
        for check_name, rec in by_check.items():
            (checks_dir / f"{check_name}.json").write_text(
                json.dumps(rec, indent=2), encoding="utf-8")
            n_checks += 1

    GROUND_TRUTH_PATH.write_text(json.dumps(GROUND_TRUTH, indent=2), encoding="utf-8")
    print(f"Wrote {len(SCENARIOS)} scenarios to {INPUTS_DIR}")
    print(f"Wrote {len(SCHEMA_B_SCENARIOS)} Schema B variants")
    print(f"Wrote {n_checks} check files to {CHECKS_DIR}")
    print(f"Wrote {GROUND_TRUTH_PATH}")


if __name__ == "__main__":
    main()
