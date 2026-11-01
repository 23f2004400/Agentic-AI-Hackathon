import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _official_generator():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import official_generator
    return official_generator


def test_every_fault_type_ingests_with_zero_rejections():
    og = _official_generator()
    from netcoop import ingest

    for fault_type in ("normal", "latency_spike", "hardware_failure"):
        record = og.generate_telemetry(fault_type)
        observations, rejected = ingest.detect_and_parse(record, source=f"official_generator:{fault_type}")
        assert rejected == [], f"{fault_type} produced rejections: {rejected}"
        assert observations, f"{fault_type} produced no observations"
