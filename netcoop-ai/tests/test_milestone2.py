from datetime import timezone
from pathlib import Path

import pytest

from netcoop import ingest
from netcoop.adapters import mapped, schema_a
from netcoop.evidence import EvidenceStore
from netcoop.metrics import MetricError, normalize_metric
from netcoop.topology import Link, Topology
from netcoop.units import UnitError

SCENARIOS = Path(__file__).resolve().parent.parent / "scenarios" / "inputs"

# ---- metrics.py ---------------------------------------------------------

def test_gauge_metric_normalizes_through_units():
    assert normalize_metric("latency", 0.5, "s") == (500.0, "ms")

def test_count_metric_passthrough():
    assert normalize_metric("queue_drops", 13416, None) == (13416.0, None)

def test_count_metric_rejects_negative_and_unit():
    with pytest.raises(UnitError):
        normalize_metric("queue_drops", -1, None)
    with pytest.raises(UnitError):
        normalize_metric("queue_drops", 5, "pct")

def test_categorical_metric_passthrough():
    assert normalize_metric("device_status", "Critical", None) == ("Critical", None)

def test_unknown_metric_rejected():
    with pytest.raises(MetricError):
        normalize_metric("bogus", 1, None)


# ---- adapters/schema_a.py -----------------------------------------------

HW_FAILURE = {
    "timestamp": "2026-10-14T07:05:49.619791Z",
    "device_id": "SW_CAMPUS_4",
    "metrics": {
        "cpu_utilization_pct": 99.9,
        "link_utilization_pct": 100.0,
        "packet_loss_pct": 28.51,
        "latency_ms": 926.46,
        "queue_drops": 13416,
        "interface_errors": 382,
    },
    "status": "Critical",
}

def test_schema_a_parses_record_into_canonical_observations():
    obs, rejected = schema_a.parse(HW_FAILURE)
    assert rejected == []
    by_metric = {o.metric: o for o in obs}
    assert by_metric["latency"].value == 926.46
    assert by_metric["latency"].unit == "ms"
    assert by_metric["queue_drops"].value == 13416.0
    assert by_metric["queue_drops"].unit is None
    assert by_metric["device_status"].value == "Critical"
    assert by_metric["cpu"].device == "SW_CAMPUS_4"
    assert by_metric["latency"].timestamp.tzinfo == timezone.utc
    assert by_metric["latency"].raw_ref == "schema_a#0.latency_ms"

def test_schema_a_accepts_list_of_records():
    obs, rejected = schema_a.parse([HW_FAILURE, HW_FAILURE])
    assert rejected == []
    assert len({o.raw_ref for o in obs}) == len(obs)  # distinct raw_refs per record

def test_schema_a_rejects_malformed_record_without_crashing():
    bad = {"timestamp": "not-a-timestamp", "device_id": "x", "metrics": {}}
    obs, rejected = schema_a.parse([HW_FAILURE, bad])
    assert len(obs) > 0          # good record still processed
    assert len(rejected) == 1
    assert "malformed record" in rejected[0][1]

def test_schema_a_rejects_unrecognized_field():
    rec = {**HW_FAILURE, "metrics": {**HW_FAILURE["metrics"], "foobar_pct": 1}}
    obs, rejected = schema_a.parse(rec)
    reasons = [r for _, r in rejected]
    assert any("unrecognized field" in r for r in reasons)

def test_schema_a_rejects_unrecognized_unit_suffix():
    rec = {**HW_FAILURE, "metrics": {**HW_FAILURE["metrics"], "latency": 250}}
    obs, rejected = schema_a.parse(rec)
    reasons = [r for _, r in rejected]
    assert any("unrecognized unit suffix" in r for r in reasons)
    assert not any(o.metric == "latency" and o.raw_ref.endswith(".latency") for o in obs)


# ---- adapters/mapped.py (Schema B) ---------------------------------------

VENDOR_B_RECORD = {
    "schema": "vendorB/2.0",
    "hostname": "SW_CAMPUS_4",
    "ts": 1791961549,
    "lat_s": 0.92646,
    "loss_ratio": 0.2851,
    "link_util_pct": 100.0,
    "cpu_pct": 99.9,
    "q_drops": 13416,
    "if_errors": 382,
    "health": "Critical",
}

def test_mapped_adapter_translates_vendor_b_via_yaml():
    mapping = mapped.load_mapping("vendor_b")
    obs, rejected = mapped.parse(VENDOR_B_RECORD, mapping, source="vendorB")
    assert rejected == []
    by_metric = {o.metric: o for o in obs}
    assert by_metric["latency"].value == pytest.approx(926.46, abs=0.01)
    assert by_metric["latency"].unit == "ms"
    assert by_metric["packet_loss"].value == pytest.approx(28.51, abs=0.01)
    assert by_metric["packet_loss"].unit == "pct"
    assert by_metric["device_status"].value == "Critical"
    assert by_metric["latency"].device == "SW_CAMPUS_4"
    assert by_metric["latency"].timestamp.tzinfo == timezone.utc

def test_mapped_adapter_rejects_unknown_schema_tag():
    mapping = mapped.load_mapping("vendor_b")
    bad = {**VENDOR_B_RECORD, "schema": "vendorC/1.0"}
    obs, rejected = mapped.parse(bad, mapping, source="vendorB")
    assert obs == []
    assert "unknown schema" in rejected[0][1]

def test_mapped_adapter_rejects_unsupported_version():
    mapping = mapped.load_mapping("vendor_b")
    bad = {**VENDOR_B_RECORD, "schema": "vendorB/9.9"}
    obs, rejected = mapped.parse(bad, mapping, source="vendorB")
    assert obs == []
    assert "unsupported" in rejected[0][1] and "version" in rejected[0][1]

def test_mapped_adapter_rejects_ambiguous_unit_without_crashing():
    mapping = mapped.load_mapping("vendor_b")
    bad = {**VENDOR_B_RECORD, "loss_ratio": 5.0}  # out of [0,1], can't be a ratio
    obs, rejected = mapped.parse(bad, mapping, source="vendorB")
    reasons = [r for _, r in rejected]
    assert any("packet_loss" in r for r in reasons)
    assert all(o.metric != "packet_loss" for o in obs)


# ---- topology.py ----------------------------------------------------------

def test_topology_common_neighbor():
    t = Topology(devices=["A", "B", "CORE"], links=[
        Link(a="A", b="CORE"), Link(a="B", b="CORE"),
    ])
    assert t.common_neighbor(["A", "B"]) == "CORE"
    assert t.common_neighbor(["A", "ghost"]) is None

def test_topology_medium_and_wireless_only():
    t = Topology(links=[Link(a="AP1", b="laptop1", medium="wireless")])
    assert t.medium_between("AP1", "laptop1") == "wireless"
    assert t.is_wireless_only("laptop1") is True
    assert t.is_wireless_only("unknown_device") is False


# ---- ingest.py: schema detection + EvidenceStore loading -----------------

def test_ingest_detects_schema_a_vs_mapped():
    obs_a, rej_a = ingest.detect_and_parse(HW_FAILURE, source="a.json")
    assert rej_a == []
    assert {o.metric for o in obs_a} >= {"latency", "cpu", "device_status"}

    obs_b, rej_b = ingest.detect_and_parse(VENDOR_B_RECORD, source="b.json")
    assert rej_b == []
    assert {o.metric for o in obs_b} >= {"latency", "cpu", "device_status"}

def test_ingest_unrecognized_input_rejected():
    obs, rejected = ingest.detect_and_parse({"foo": "bar"}, source="x.json")
    assert obs == []
    assert "unrecognized input format" in rejected[0]["reason"]

def test_ingest_load_into_store_dedupes_via_evidence_store():
    store = EvidenceStore()
    rejected1 = ingest.load_into_store(store, HW_FAILURE, source="a.json")
    assert rejected1 == []
    rejected2 = ingest.load_into_store(store, HW_FAILURE, source="a.json")
    # same raw_refs -> every observation looks like a duplicate the second time
    assert all(r["reason"] == "duplicate observation" for r in rejected2)
    assert len(rejected2) > 0

def test_ingest_load_file_handles_malformed_json_without_raising():
    store = EvidenceStore()
    rejected = ingest.load_file(store, SCENARIOS / "broken_malformed.json")
    assert len(rejected) == 1
    assert "malformed JSON" in rejected[0]["reason"]


# ---- scenario files: every broken file produces the expected reason ------

BROKEN_FILE_REASONS = {
    "broken_missing_unit.json": "unrecognized unit suffix",
    "broken_unknown_field.json": "unrecognized field",
    "broken_unsupported_schema_version.json": "unsupported",
    "broken_naive_timestamp.json": "naive timestamp",
    "broken_malformed.json": "malformed JSON",
    "broken_duplicate_records.json": "duplicate observation",
}

@pytest.mark.parametrize("filename,expected_reason", list(BROKEN_FILE_REASONS.items()))
def test_broken_scenario_files_produce_expected_reason(filename, expected_reason):
    store = EvidenceStore()
    rejected = ingest.load_file(store, SCENARIOS / filename)
    assert rejected, f"{filename} produced no rejections"
    assert any(expected_reason in r["reason"] for r in rejected), (
        f"{filename}: expected a reason containing {expected_reason!r}, got {rejected}"
    )

def test_broken_duplicate_records_file_still_keeps_the_first_observation():
    store = EvidenceStore()
    ingest.load_file(store, SCENARIOS / "broken_duplicate_records.json")
    assert len(store.observations) == 2  # cpu_utilization_pct + status, each once


# ---- cross-schema equivalence: Schema A and Schema B agree ---------------

def test_schema_a_and_schema_b_produce_identical_observations_for_same_incident():
    store_a = EvidenceStore()
    rejected_a = ingest.load_file(store_a, SCENARIOS / "schema_a_hardware_failure.json")
    store_b = EvidenceStore()
    rejected_b = ingest.load_file(store_b, SCENARIOS / "schema_b_hardware_failure.json")
    assert rejected_a == []
    assert rejected_b == []

    by_metric_a = {o.metric: o for o in store_a.observations.values()}
    by_metric_b = {o.metric: o for o in store_b.observations.values()}
    shared = {"latency", "packet_loss", "link_utilization", "cpu", "queue_drops",
              "interface_errors", "device_status"}
    assert set(by_metric_a) >= shared
    assert set(by_metric_b) >= shared

    for metric in shared:
        oa, ob = by_metric_a[metric], by_metric_b[metric]
        assert oa.device == ob.device
        assert oa.unit == ob.unit
        if isinstance(oa.value, str):
            assert oa.value == ob.value
        else:
            assert oa.value == pytest.approx(ob.value, rel=1e-3)
        # Schema B's epoch_s timestamp truncates sub-second precision
        assert abs((oa.timestamp - ob.timestamp).total_seconds()) < 1


# ---- missing field is not reported as zero --------------------------------

def test_missing_field_is_absent_not_zero():
    # The generator's latency_spike records have no interface_errors field at all.
    latency_spike = {
        "timestamp": "2026-10-14T06:45:27.884215Z",
        "device_id": "SW_CAMPUS_7",
        "metrics": {
            "cpu_utilization_pct": 55.23,
            "link_utilization_pct": 72.84,
            "packet_loss_pct": 0.81,
            "latency_ms": 310.52,
            "jitter_ms": 58.41,
            "queue_drops": 12,
        },
        "status": "Warning",
    }
    obs, rejected = schema_a.parse(latency_spike)
    assert rejected == []
    metrics = {o.metric for o in obs}
    assert "interface_errors" not in metrics          # absent, not a zero-value observation
    assert not any(o.metric == "interface_errors" for o in obs)
