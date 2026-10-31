# NetCoop AI — project context for Claude Code

Hackathon: Agentic AI Grand Finale, Tech Zephyr 4.0, IIT Bhubaneswar. 24-hour build, one coder.
Problem: multi-agent network fault diagnosis with real agent interoperability.
Official spec: `docs/problem_statement.pdf` (source of truth). Official data generator: `docs/Mock_data_builder_netScoopAI.ipynb`.

## Judging weights (official) — allocate effort accordingly
| Criterion | Weight |
|---|---|
| Agent interoperability and protocol design | 30% |
| Diagnostic accuracy and evidence quality | 25% |
| Agent orchestration and conflict resolution | 15% |
| Robustness to missing / inconsistent / incompatible data | 15% |
| Innovation, usability, extensibility | 10% |
| Code quality, documentation, demonstration | 5% |

## Central differentiator (must exist in code, not just slides)
Evidence-first cooperative diagnosis: grounding is computed by the EvidenceStore, never trusted
from an agent. A claim counts as `observed` only if it cites stored raw observation IDs; findings
citing only other findings are `inferred`; nothing else counts (`unsupported`). When the top two
hypotheses are too close, the orchestrator requests a targeted diagnostic check, and the result
can change the ranking.

## Non-negotiable rules
1. Root cause is decided by deterministic rules + scoring. No LLM chooses the diagnosis. (No LLM at all unless explicitly asked later.)
2. Never hardcode an answer per scenario file. Agents must never read `scenarios/ground_truth.json`.
3. Never guess units, timezones or missing values. Reject the measurement with a reason; continue with a stated limitation when partial diagnosis is still possible.
4. "Not reported" is different from zero. Missing fields go into `missing`, not into a default.
5. The generator's `status` field (Healthy/Warning/Critical) is a device-reported observation, NOT ground truth and NOT a shortcut to the diagnosis.
6. Every feature gets a pytest test. Run `pytest -q` after every change and show the real output. Never claim a test passed without running it.
7. Keep it small: plain Python, Pydantic v2, pytest, PyYAML, rich. No databases, vector stores, MCP, web frameworks, or async unless asked.
8. Don't rewrite working modules unnecessarily. Extend them.

## Existing code (Milestone 1, tested)
- `netcoop/protocol.py` — `Observation`, `Finding`, `Envelope`, `MsgType`, schema version check (`SUPPORTED_VERSIONS`), extra fields forbidden, tz-aware timestamps.
- `netcoop/evidence.py` — `EvidenceStore`: dedupe, unknown-device rejection, staleness, `ground()` assigns grounding, `trace()` resolves a finding to raw records.
- `netcoop/units.py` — canonical units; ambiguous/unknown units raise `UnitError`.
- `tests/test_milestone1.py` — 7 tests.

## Official generator format = "Schema A" (accept it exactly)
```json
{"timestamp": "2026-10-14T07:05:49.619791Z", "device_id": "SW_CAMPUS_4",
 "metrics": {"cpu_utilization_pct": 99.9, "link_utilization_pct": 100.0, "packet_loss_pct": 28.51,
             "latency_ms": 926.46, "queue_drops": 13416, "interface_errors": 382},
 "status": "Critical"}
```
Facts:
- Fault types in the generator: `normal`, `latency_spike`, `hardware_failure` only.
- One snapshot, one random device `SW_CAMPUS_1..10`, no topology, timestamp = generation time.
- Fields vary by type: `latency_spike` has `jitter_ms` but no `interface_errors`; `hardware_failure` has `interface_errors` but no `jitter_ms`.
- Units are encoded in field-name suffixes (`_pct`, `_ms`). `queue_drops`, `interface_errors` are counts with no time window — report them as absolute counts, not rates.
- Input may be a single record or a list of records.

"Schema B" is our own deliberately different vendor format (flat, `hostname`, `lat_s` seconds,
`loss_ratio` 0–1, epoch timestamps, `"schema": "vendorB/2.0"`), translated by a declarative
mapping file, not by hand-written if-statements.

## Target layout
```
netcoop/
  protocol.py evidence.py units.py          # exist
  metrics.py                                # canonical metric registry (name, unit, kind)
  adapters/schema_a.py  adapters/mapped.py  # mapped.py reads mappings/*.yaml
  mappings/vendor_b.yaml
  topology.py
  rules.yaml                                # all thresholds live here
  agents/base.py performance.py infrastructure.py connectivity.py correlation.py reporting.py
  checks.py                                 # targeted diagnostic checks (read real check files)
  confidence.py
  orchestrator.py
  cli.py  __main__.py                       # python -m netcoop run <file>
scenarios/ inputs/  checks/  topology.json  ground_truth.json
scripts/gen_scenarios.py                    # seeded, reproducible
eval/ run_eval.py  baseline.py  results/
tests/
docs/ PROTOCOL.md  problem_statement.pdf
logs/                                        # per-incident message logs (JSONL)
```

## Agents (each emits Envelope messages with grounded Findings)
- Performance: latency, jitter, packet loss, throughput, link utilization.
- Infrastructure: device status, CPU, memory, interface errors, queue drops, RSSI/retransmissions.
- Connectivity: topology scope (common upstream node), wired vs wireless, DNS/DHCP service failures. Without topology: report "scope limited to reporting device" as a limitation.
- Correlation: builds competing hypotheses, scores support vs contradiction, resolves conflicts, decides whether a targeted check is needed.
- Reporting: produces the final JSON + Markdown from a template.

## Diagnosis rules (thresholds in rules.yaml)
| Hypothesis | Supports | Contradicts |
|---|---|---|
| link_congestion | link_util > 85%, queue drops elevated, high latency, same device/link | low util; errors without load |
| interface_fault | interface errors, packet loss, flaps | util high enough to explain loss; zero errors |
| weak_wifi | RSSI < -70 dBm, retransmissions, wireless-only scope | wired clients on same segment degraded |
| dns_dhcp_failure | service failures with normal latency/loss, many clients | general loss/latency high |
| cpu_overload | CPU > 90%, latency through that device | CPU normal |
| hardware_failure | device down, or several independent subsystems saturated simultaneously + interface errors (co-occurrence rule) | single subsystem only |
| healthy | all metrics in range | any breach |

Special cases:
- Generator `hardware_failure` record: co-occurrence rule ranks hardware_failure first; congestion and cpu_overload become alternatives.
- Generator `latency_spike` record: util 60–85 and few drops prove no cause → status `insufficient_evidence`, list candidate causes and the next check. Do NOT lower thresholds to force a cause.

## Confidence (heuristic, documented — not a calibrated probability)
- score = clamp((Σ support weights − Σ contradiction weights) / Σ possible support weights, 0, 1) × coverage
- coverage = fraction of the hypothesis's required metrics that are present and fresh
- margin = top score − second score
- High: score ≥ 0.75 and margin ≥ 0.2; Medium: score ≥ 0.5 and margin ≥ 0.1; Low: otherwise
- `insufficient_evidence` when coverage < 0.5 or top score < 0.3 or (margin < 0.1 after checks are exhausted)
- Max 2 targeted checks per incident; stop early once margin ≥ 0.2.

## Report format (page 5 of the spec, plus our fields)
Required: `incident_id`, `status` (`diagnosed` | `insufficient_evidence` | `invalid_input` | `partial`),
`suspected_root_cause`, `confidence`, `affected_devices`, `supporting_evidence[{metric, value, unit, interpretation, obs_id, raw_ref}]`,
`alternative_causes`, `recommended_actions`, `evidence_sources`, `limitations`.
Added: `confidence_category`, `confidence_method`, `hypotheses[]` (score, support, contradictions, missing),
`checks_performed[]`, `rejected_inputs[]`, `message_log` (path).

## Commands
- Tests: `pytest -q`
- Run: `python -m netcoop run scenarios/inputs/<file>.json [--topology scenarios/topology.json] [--checks scenarios/checks/<dir>]`
- Eval: `python eval/run_eval.py`