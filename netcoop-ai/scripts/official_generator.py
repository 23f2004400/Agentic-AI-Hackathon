#!/usr/bin/env python
"""Verbatim copy of generate_telemetry() from
docs/Mock_data_builder_netScoopAI.ipynb (the official sample generator), plus
a thin CLI wrapper around it. The function below is byte-for-byte the
notebook's cell -- including its use of the now-deprecated
datetime.utcnow() -- deliberately unmodified, since the point is to exercise
the actual official generator, not a cleaned-up reimplementation of it.

    python scripts/official_generator.py <normal|latency_spike|hardware_failure> [--out FILE]

Prints the JSON record to stdout by default. In PowerShell, `>` redirection
doesn't reliably produce plain UTF-8 (depending on $OutputEncoding/console
codepage it can add a BOM or use a different encoding), which can break
json.loads on the receiving end -- use --out to write the file directly as
UTF-8 with no BOM instead of relying on shell redirection.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path


# ---- verbatim from docs/Mock_data_builder_netScoopAI.ipynb, cell-0 --------

def generate_telemetry(fault_type="normal"):
    data = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "device_id": f"SW_CAMPUS_{random.randint(1, 10)}",
        "metrics": {}
    }

    if fault_type == "latency_spike":
        data["metrics"] = {
            "cpu_utilization_pct": round(random.uniform(40, 70), 2),
            "link_utilization_pct": round(random.uniform(60, 85), 2),
            "packet_loss_pct": round(random.uniform(0.1, 1.5), 2),
            "latency_ms": round(random.uniform(150, 400), 2), # Spike
            "jitter_ms": round(random.uniform(30, 80), 2),
            "queue_drops": random.randint(0, 50)
        }
        data["status"] = "Warning"
    elif fault_type == "hardware_failure":
        data["metrics"] = {
            "cpu_utilization_pct": 99.9,
            "link_utilization_pct": 100.0,
            "packet_loss_pct": round(random.uniform(15, 30), 2),
            "latency_ms": round(random.uniform(500, 1000), 2),
            "queue_drops": random.randint(5000, 15000),
            "interface_errors": random.randint(100, 500)
        }
        data["status"] = "Critical"
    else:
        data["metrics"] = {
            "cpu_utilization_pct": round(random.uniform(10, 35), 2),
            "link_utilization_pct": round(random.uniform(20, 50), 2),
            "packet_loss_pct": 0.0,
            "latency_ms": round(random.uniform(5, 20), 2),
            "jitter_ms": round(random.uniform(1, 5), 2),
            "queue_drops": 0
        }
        data["status"] = "Healthy"

    return data

# ---- end verbatim section ---------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="official_generator")
    parser.add_argument("fault_type", choices=["normal", "latency_spike", "hardware_failure"])
    parser.add_argument("--out", default=None, help="Write to this file as UTF-8 (no BOM) instead of stdout")
    args = parser.parse_args(argv)

    record = generate_telemetry(args.fault_type)
    text = json.dumps(record, indent=2)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
