# NetCoop AI

Multi-agent network fault diagnosis for the Agentic AI Grand Finale (Tech
Zephyr 4.0, IIT Bhubaneswar). Five specialized agents exchange structured,
self-grounding evidence over a shared protocol, correlate it into ranked
hypotheses, and produce a single evidence-backed diagnostic report — JSON and
Markdown — with confidence, alternatives, and recommended actions.

All code lives under **[netcoop-ai/](netcoop-ai/)** — every command below is
run from inside that directory. For the full protocol, scoring formula, and
architecture writeup, see **[netcoop-ai/docs/PROTOCOL.md](netcoop-ai/docs/PROTOCOL.md)**.
This README is the quick start.

## The core idea

No agent is trusted to grade its own evidence. Every claim an agent makes
either cites a raw observation ID that exists in the shared `EvidenceStore`
("observed"), cites another already-grounded claim ("inferred"), or it
doesn't count ("unsupported") — regardless of what the agent itself asserts.
Root cause is decided by deterministic rules and scoring against that
evidence, never by an LLM, and never hardcoded per input file.

## Setup

```bash
cd netcoop-ai
python -m venv .venv
.venv\Scripts\activate          # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pytest -q                       # 106 tests
```

(All further commands in this README assume you're still inside `netcoop-ai/`.)

## Running a diagnosis

```bash
python -m netcoop run scenarios/inputs/hardware_failure.json
python -m netcoop run scenarios/inputs/upstream_failure.json --topology scenarios/topology.json
python -m netcoop run scenarios/inputs/stale.json --topology scenarios/topology.json --checks scenarios/checks/stale
```

Each run prints the full inter-agent message flow, prints the final report,
and writes `reports/<incident_id>.json` / `.md`. Every `Envelope` exchanged
during the run is appended to `logs/<incident_id>.jsonl` for replay/audit.

To see every bundled scenario scored against its documented expected outcome,
alongside a naive baseline that diagnoses purely from the device's
self-reported `status` field (no correlation, no evidence weighting — exactly
the shortcut the real pipeline refuses to take):

```bash
python eval/run_eval.py
```

On the bundled scenarios this currently shows the pipeline at 100%
status/cause accuracy against the documented ground truth, versus 75%/17%
for the naive baseline.

## Agents

| Agent | Responsibility |
|---|---|
| **Performance** | latency, jitter, packet loss, throughput, bandwidth, link utilization |
| **Infrastructure** | device status, CPU, memory, interface errors, queue drops |
| **Connectivity** | shared-upstream scope of the affected devices, wired vs. wireless, DNS/DHCP failures. No topology → explicitly flags "scope limited to reporting device" rather than guessing |
| **Correlation** | builds one scored `Hypothesis` per rule in `rules.yaml` from the raw evidence, ranks them, resolves the hardware-failure co-occurrence case, decides when a targeted check is warranted |
| **Reporting** | turns the ranking into the final `Report`, tracing every piece of supporting evidence back to a raw record |

Agents talk in one message shape (`Envelope`, see `netcoop/protocol.py`):
`task → findings → (check_request/check_result)* → report`, all logged.
A malformed or exception-raising agent reply is caught, logged as an `error`
Envelope, and the run continues with whatever the other agents produced —
diagnosis degrades to `partial`, not a crash.

## Interoperability

Two input formats are accepted and produce identical canonical evidence:

- **Schema A** — the official generator's format exactly, unit-suffix-driven
  (`_pct`, `_ms`, `_mbps`, `_dbm`).
- **Schema B** — a deliberately different vendor format (flat, `hostname`,
  seconds, 0–1 ratios, epoch time), translated by a declarative YAML mapping
  (`netcoop/mappings/vendor_b.yaml`), not hand-written if-statements.

`netcoop/ingest.py` auto-detects which one a file is. A record with a
missing unit, an unrecognized field, a naive timestamp, an unsupported
schema version, or malformed JSON is rejected with a specific reason — the
rest of the file still loads. See `scenarios/inputs/broken_*.json` for one
example of each.

## Scenarios

`scripts/gen_scenarios.py` is seeded (reproducible) and generates 12 fault
scenarios — `normal`, `congestion`, `interface_fault`, `weak_wifi`,
`dns_dhcp_failure`, `cpu_overload`, `hardware_failure`, `upstream_failure`
(shared distribution switch), `simultaneous` (two unrelated faults in
different segments), `insufficient` (key metrics missing), `stale` (an old
reading that must be excluded), `contradictory` — each as a 6-sample time
series, plus 3 Schema B variants and 2 targeted-check fixtures. Regenerate
with:

```bash
python scripts/gen_scenarios.py
```

`scenarios/ground_truth.json` documents the expected outcome of each;
`eval/run_eval.py` is the only thing that reads it — no agent or
orchestrator code does (every scenario must be solved from its evidence,
not from the answer key).

## Project layout

```
netcoop/
  protocol.py evidence.py units.py metrics.py   # message protocol, grounding, canonical units/metrics
  adapters/        mappings/                    # Schema A/B ingestion
  topology.py       rules.yaml       confidence.py
  agents/           checks.py        orchestrator.py
  cli.py  __main__.py  report.py     ingest.py
scenarios/   inputs/  checks/  topology.json  ground_truth.json
scripts/gen_scenarios.py
eval/        run_eval.py  baseline.py  results/
tests/       106 tests across 5 milestones
docs/        PROTOCOL.md  problem_statement.pdf
logs/        reports/     (generated; gitignored)
```

This `netcoop-ai/` directory is the project; `Agentic-AI-Hackathon/` (this
repo's root) exists only because that's the name of the GitHub repository
for the hackathon submission.
