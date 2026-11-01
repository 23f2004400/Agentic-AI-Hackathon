"""Covers demo/vendor_c.yaml: a third vendor format (vendorC/1.0), prepared for
a live interoperability demo but not wired into netcoop/mappings yet. Copies
the mapping into a temporary mappings directory -- never the real one -- so
this test can't accidentally enable vendorC for the rest of the suite."""
import shutil
from pathlib import Path

from netcoop.adapters import mapped
from netcoop.orchestrator import Orchestrator

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
SCENARIOS = ROOT / "scenarios" / "inputs"


def test_vendor_c_mapping_matches_congestion_scenario(tmp_path, monkeypatch):
    temp_mappings = tmp_path / "mappings"
    temp_mappings.mkdir()
    shutil.copy(DEMO / "vendor_c.yaml", temp_mappings / "vendor_c.yaml")
    monkeypatch.setattr(mapped, "MAPPINGS_DIR", temp_mappings)

    vendor_c = Orchestrator(logs_dir=tmp_path / "logs_c")
    report_c = vendor_c.run(DEMO / "vendor_c_congestion.json", incident_id="TEST-vendor-c")

    reference = Orchestrator(logs_dir=tmp_path / "logs_ref")
    report_ref = reference.run(SCENARIOS / "congestion.json", incident_id="TEST-congestion-ref")

    assert report_c.status == "diagnosed"
    assert report_c.suspected_root_cause == report_ref.suspected_root_cause
    assert report_c.affected_devices == report_ref.affected_devices


def test_vendor_c_schema_is_unrecognized_without_the_mapping(tmp_path):
    """Sanity check for the live-demo setup: before the mapping is installed into
    the real netcoop/mappings dir, the real MAPPINGS_DIR has no vendorC entry, so
    the file is rejected rather than silently misparsed."""
    assert mapped.find_mapping_for_schema("vendorC/1.0") is None
    orch = Orchestrator(logs_dir=tmp_path / "logs")
    report = orch.run(DEMO / "vendor_c_congestion.json", incident_id="TEST-vendor-c-unknown")
    assert report.status == "invalid_input"
    assert any("unknown schema" in r.reason for r in report.rejected_inputs)
