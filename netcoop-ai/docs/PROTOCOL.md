# NetCoop AI — Protocol and Architecture

This document describes the message protocol, agent responsibilities, evidence
model, correlation algorithm, and orchestration flow. It is the interoperability
documentation required by the problem statement (section 7.2) and by
CLAUDE.md's "Agent interoperability and protocol design" criterion (30%).

## 1. Core differentiator: evidence-first cooperative diagnosis

The central design rule, enforced in code rather than by agent convention:

> A claim only counts as **observed** if it cites a raw observation ID that
> exists in the `EvidenceStore`. A claim built on top of observed (or other
> inferred) claims is **inferred**. Everything else is **unsupported** — and
> an unsupported claim never becomes evidence just because another agent
> repeats it.

No agent decides its own grounding. `EvidenceStore.ground()` (`netcoop/evidence.py`)
recomputes it from scratch for every `Finding`, ignoring whatever the sender
claims. This is why a "rumor" — a `Finding` with `grounding="observed"` set by
its author but citing no real `obs_id` — is relabeled `unsupported` the moment
it passes through the store, and the Fault Correlation agent only accepts
`observed`/`inferred` Findings as evidence (`agents/correlation.py`,
`_grounded_finding_for`).

## 2. Message protocol (`netcoop/protocol.py`)

All inter-agent communication is a single `Envelope`:

```python
Envelope(
    msg_id: str,                 # auto-generated, unique
    incident_id: str,
    schema_version: str = "1.0", # rejected if not in SUPPORTED_VERSIONS
    sender: str,
    recipient: str,
    type: MsgType,                # task | findings | check_request | check_result | error | report
    created_at: datetime,         # always UTC
    findings: list[Finding],
    payload: dict,
    missing: list[str],           # evidence the sender knows it lacks
    assumptions: list[str],       # caveats the sender is explicitly flagging
    errors: list[str],
)
```

`extra="forbid"` on every model: an unrecognized field is a validation error,
not a silently dropped one. A `Finding` is:

```python
Finding(
    finding_id: str,
    claim: str,                   # e.g. "high_link_utilization" or "root_cause:hardware_failure"
    device: str | None,
    evidence_refs: list[str],     # obs_ids or other finding_ids
    grounding: "observed" | "inferred" | "unsupported",  # set by the store, never trusted from the sender
    detail: str,
)
```

An `Observation` is the atomic, immutable unit of evidence: `device`,
`metric` (canonical name), `value`, `unit` (canonical, or `None` for
unitless counts/categorical), `timestamp` (must be timezone-aware — a naive
timestamp is rejected, never silently assumed UTC), `source`, and `raw_ref`
(a pointer back to the original record, e.g.
`"hardware_failure.json#0.cpu_utilization_pct"`).

## 3. Interoperability: two input schemas, one canonical form

- **Schema A** (`adapters/schema_a.py`) — the official generator's format
  exactly. A field's canonical unit is derived from its name suffix via an
  explicit table (`_pct` → `pct`, `_ms` → `ms`, `_mbps` → `Mbps`, `_dbm` →
  `dBm`); a field with an unrecognized suffix or base name is rejected with a
  reason, never guessed.
- **Schema B** (`adapters/mapped.py` + `mappings/vendor_b.yaml`) — a
  deliberately different vendor format (flat, `hostname`, `lat_s` seconds,
  `loss_ratio` 0–1, epoch timestamps). Field mapping is declarative YAML, not
  hand-written if-statements; adding a third vendor format means adding a
  mapping file.
- **`netcoop/ingest.py`** sniffs which adapter a payload belongs to
  (`device_id`+`metrics` → Schema A; a `schema` tag → look up a matching
  mapping) and feeds the result into the `EvidenceStore`. A file with some
  bad records still yields every good observation; malformed JSON, an
  unknown schema tag, and an unsupported schema version are all rejected
  with a specific reason rather than crashing the run.

Both schemas, given the same incident, produce byte-identical canonical
`Observation`s (verified in `tests/test_milestone2.py`).

## 4. Canonical metrics (`netcoop/metrics.py`, `netcoop/units.py`)

Every metric has a registry entry of `(name, unit, kind)`:

- **gauge** — unit-bearing, converted via `units.normalize()` (e.g. `latency`
  → `ms`). A missing or ambiguous unit is rejected, never assumed.
- **count** — cumulative, unitless, reported as an absolute count, never a
  rate (`queue_drops`, `interface_errors`, `dhcp_failures`).
- **categorical** — an enum/string state, not numeric (`device_status`).

## 5. Agents

Every analysis agent implements `agents/base.py`'s `Agent`: `analyze(device,
store) -> list[Finding]`, and `handle(task, store) -> Envelope` grounds every
Finding through the store before returning. `latest_observation()` is the
one place "freshness" is decided: an observation older than `max_age`
(10 minutes by default) relative to the *latest timestamp anywhere in the
incident* — not wall-clock time, so static scenario files stay reproducible
— is treated as if it weren't there. `is_only_stale()` distinguishes that
case from "never reported" for reporting purposes.

| Agent | Module | Scope |
|---|---|---|
| Performance | `agents/performance.py` | latency, jitter, packet loss, throughput, bandwidth, link utilization |
| Infrastructure | `agents/infrastructure.py` | device status, CPU, memory, interface errors, queue drops |
| Connectivity | `agents/connectivity.py` | topology scope (shared upstream of the affected devices), wired vs. wireless segment mix, DNS/DHCP service-failure signals. Without a topology, it records `"scope limited to reporting device"` as an Envelope `assumption` rather than silently skipping the analysis. |
| Correlation | `agents/correlation.py` | builds one `Hypothesis` per rule in `rules.yaml`, scores it, ranks them |
| Reporting | `agents/reporting.py` | turns the ranking into the final `Report` |

Performance, Infrastructure, and Connectivity's metric-threshold logic is
entirely data-driven: each `signal` in `rules.yaml` names a metric, a
comparison, a threshold, which agent owns it, and which hypotheses it
supports/contradicts and by how much. An agent emits a Finding (citing the
triggering `obs_id`) for every signal it owns that fires; it never contains
hand-written per-hypothesis logic.

## 6. Correlation algorithm (`agents/correlation.py`, `netcoop/confidence.py`)

For a device, every `rules.yaml` signal is re-evaluated directly against the
raw `EvidenceStore` (never against what an agent merely *claims* fired).
For each hypothesis:

```
support_weight    = sum of weights of this hypothesis's "supports" signals that fired
contradict_weight = sum of weights of its "contradicts" signals that fired
possible_support   = sum of weights of every "supports" signal defined for it
coverage            = (required metrics present and fresh) / (required metrics)
score = clamp((support_weight - contradict_weight) / possible_support, 0, 1) * coverage
```

`healthy` is the exception: its score is `(0 if any signal fired else 1) *
coverage` — a clean bill of health is only as credible as how much was
actually measured; zero evidence must not score the same as full coverage.

**Co-occurrence rule for `hardware_failure`**: if signals from at least
`co_occurrence_min` (3) independent subsystems fire for `hardware_failure`
*and* `interface_errors_present` is among them, the single-subsystem
alternatives (`link_congestion`, `cpu_overload`, `interface_fault`) are
demoted by `co_occurrence_demote_factor` (0.5) rather than tying with it —
this is what makes `hardware_failure` rank first with `link_congestion`
and `cpu_overload` falling out as `alternative_causes`, matching the
generator's `hardware_failure` fault type.

A hypothesis-level `Finding` (`claim="root_cause:<hypothesis>"`) cites the
`finding_id`s of the agent Findings whose signals fired — so it grounds as
**inferred**, never observed. A Finding whose grounding resolves to
`unsupported` (a rumor) is recorded in `assumptions` but excluded from
scoring entirely; because scoring re-evaluates the raw store rather than
trusting any Finding's claim, a rumor cannot move a score even before that
filter is applied — the filter only governs whether it's cited as the
*reason* behind a hypothesis.

**Confidence** (`netcoop/confidence.py`) is one formula, used everywhere a
confidence number appears:

- `margin = top score − second-best score`
- **High**: score ≥ 0.75 and margin ≥ 0.2
- **Medium**: score ≥ 0.5 and margin ≥ 0.1
- **Low**: otherwise
- **`insufficient_evidence`**: coverage < 0.5, or score < 0.3, or margin < 0.1

This is a documented heuristic, not a calibrated probability — every report
says so explicitly.

**`next_check`**: when a hypothesis isn't fully resolved, a generic,
non-hardcoded heuristic suggests what to check next — the highest-priority
*missing* required metric, or (if nothing is missing but the score is still
ambiguous) a re-poll of the same required metrics to see whether the trend
confirms or rules the hypothesis out. For the generator's `latency_spike`
fault type (link utilization 60–85%, few drops), no threshold is lowered to
force a cause — the system reports `insufficient_evidence` with ranked
candidates and a next check, exactly as CLAUDE.md requires.

## 7. Targeted diagnostic checks (`netcoop/checks.py`)

When the top two hypotheses are too close (margin < 0.2), the orchestrator
may request one targeted check per round, up to 2 per incident, stopping
early once margin ≥ 0.2. A check is not live telemetry — there are no real
devices — it's a pre-authored file at `<checks_dir>/<device>__<metric>.json`,
in the same Schema A format as ordinary input, representing what a human or
script would obtain by proactively re-polling that device right now. If no
matching file exists, the orchestrator records that plainly
(`"no check available for <metric> on <device>"`) and stops; a check never
fabricates data or lowers a threshold to manufacture a conclusion. Applying a
check re-runs every analysis agent and the correlation agent against the
updated store, so new data can genuinely change the ranking (see
`scenarios/checks/stale/` and `scenarios/checks/insufficient/`, which do
exactly that; `scenarios/checks/contradictory/` deliberately doesn't exist,
demonstrating the "no check available" path).

## 8. Topology and multi-device scope (`netcoop/topology.py`)

`topology.json` declares `devices` and directed `links` (`a` = upstream
parent, `b` = downstream child, plus a `medium`). `load_topology()` rejects
any link referencing a device not in the declared `devices` list. Query
methods (`downstream_of`, `ancestors_of`, `common_upstream`, `is_wireless`)
reject an unknown device rather than silently returning nothing.

When an incident involves multiple devices, `ReportingAgent._cluster_by_scope`
groups them by their immediate topology parent. Devices that share a parent
(e.g. three access switches under the same distribution switch, as in the
`upstream_failure` scenario) get one diagnosis; devices in genuinely
different branches (as in the `simultaneous` scenario) each get their own —
the report's `additional_diagnoses` field carries the others instead of
silently picking one "strongest" device and discarding an unrelated second
fault. If the only thing two devices share is the topology's root (true of
*any* two devices on campus), the Connectivity agent says so honestly
("only share the network core... consistent with unrelated faults") instead
of implying the root switch is a candidate cause.

## 9. Orchestration and error handling (`netcoop/orchestrator.py`)

```
ingest(file) -> EvidenceStore
  -> dispatch TASK to Performance, Infrastructure, Connectivity
       -> each reply validated as a well-formed Envelope; a malformed reply
          or an agent exception is caught, logged as an ERROR Envelope, and
          the run continues with whatever the other agents produced
  -> dispatch TASK to Correlation -> ranked Hypotheses per device
  -> while margin < 0.2 and checks remain (max 2):
       request a targeted check -> if applied, re-dispatch everything above
  -> ReportingAgent builds the final Report
```

Every `Envelope` exchanged — including `CHECK_REQUEST`/`CHECK_RESULT` pairs
and the final `REPORT` — is appended to `logs/<incident_id>.jsonl`, one JSON
object per line, giving a complete, replayable audit trail of the incident.

**Report status**: `invalid_input` (nothing usable was ingested),
`insufficient_evidence` (confidence formula says so),
`partial` (a diagnosis was still reached despite an agent error),
`diagnosed` (none of the above).

## 10. Running it

```
pytest -q
python -m netcoop run scenarios/inputs/<file>.json \
    [--topology scenarios/topology.json] \
    [--checks scenarios/checks/<scenario>]
python eval/run_eval.py   # compares every scenario against scenarios/ground_truth.json
```

`ground_truth.json` is read only by `eval/run_eval.py` and by humans — no
agent or orchestrator code reads it, per CLAUDE.md's non-negotiable rule 2.
