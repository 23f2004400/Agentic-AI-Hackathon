# NetCoop AI — Protocol and Architecture

This document describes the message protocol, agent responsibilities, evidence
model, correlation algorithm, orchestration flow, and error taxonomy. It is the
interoperability documentation required by the problem statement (section 7.2)
and by CLAUDE.md's "Agent interoperability and protocol design" criterion (30%).

The Envelope/Observation field tables below (section 2) are generated
directly from `Envelope.model_json_schema()` / `Observation.model_json_schema()`
by `scripts/gen_protocol_tables.py --write` — they can't silently drift from
the actual protocol code; re-run that script after changing a field and the
tables update to match.

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
`_grounded_finding_for`). See section 3 for the full grounding rule table.

## 2. Message protocol (`netcoop/protocol.py`)

All inter-agent communication is a single `Envelope`, carrying zero or more
`Finding`s. `extra="forbid"` on every model: an unrecognized field is a
validation error at construction time, not a silently dropped one.

<!-- GENERATED:ENVELOPE_FIELDS -->
#### Envelope fields

| Field | Type | Unit | Required | Meaning |
|---|---|---|---|---|
| `msg_id` | `string` | — | no | Unique ID for this message, auto-generated (msg-<hex>). A reply reusing a msg_id already seen this incident is treated as malformed. |
| `incident_id` | `string` | — | yes | Identifies which incident this message belongs to. |
| `schema_version` | `string` | — | no | Protocol version this message was built against. Rejected at construction time if not in SUPPORTED_VERSIONS. |
| `sender` | `string` | — | yes | Name of the sending agent/component, e.g. 'performance_agent'. |
| `recipient` | `string` | — | yes | Name of the intended recipient, e.g. 'orchestrator'. |
| `type` | `MsgType` | — | yes | What kind of message this is; see MsgType. |
| `created_at` | `datetime` | — | no | UTC timestamp this Envelope was constructed. |
| `findings` | `Finding[]` | — | no | Findings this message carries, if any. |
| `payload` | `object` | — | no | Type-specific structured data, e.g. {'devices': [...]} for a task, {'devices': {...ranking...}} for correlation findings, or a full Report dict for a report. |
| `missing` | `string[]` | — | no | Evidence the sender knows it lacks, as '<device>:<metric>' strings. Distinct from silently omitting a metric -- this says 'I looked, it wasn't there.' |
| `assumptions` | `string[]` | — | no | Caveats the sender is explicitly flagging, e.g. 'scope limited to reporting device' or a conflicting-readings note. Surfaced in the final report's limitations. |
| `errors` | `string[]` | — | no | Error messages, for type=error envelopes. |
<!-- END GENERATED:ENVELOPE_FIELDS -->

<!-- GENERATED:OBSERVATION_FIELDS -->
#### Observation fields

| Field | Type | Unit | Required | Meaning |
|---|---|---|---|---|
| `obs_id` | `string` | — | no | Unique ID for this observation, auto-generated (obs-<hex>). |
| `device` | `string` | — | yes | Device identifier exactly as reported by the source feed. |
| `metric` | `string` | — | yes | Canonical metric name from netcoop/metrics.py, e.g. 'latency'. |
| `value` | `number / string / boolean` | value's own unit | yes | The measurement. float for gauge/count metrics, str for categorical (e.g. device_status), bool only if a source ever reports one. |
| `unit` | `string / null` | value's own unit | yes | Canonical unit after conversion (e.g. 'ms', 'pct'), or null for unitless count/categorical metrics. Never guessed: ingestion rejects an ambiguous unit instead of defaulting one. |
| `timestamp` | `datetime` | — | yes | When the measurement was taken. Must be timezone-aware; stored normalized to UTC. |
| `source` | `string` | — | yes | Which input feed/adapter produced this observation (e.g. a filename). |
| `raw_ref` | `string` | — | yes | Pointer back to the exact original record, e.g. 'file.json#2.cpu_utilization_pct'. This is what EvidenceStore.trace() ultimately resolves a claim down to. |
| `original_unit` | `string / null` | value's own unit | no | The unit as reported by the source before conversion to `unit`, for audit. |
<!-- END GENERATED:OBSERVATION_FIELDS -->

`Finding` (cited by Envelope.findings, not separately generated above since
its one non-obvious field, `grounding`, is covered in section 3):

```python
Finding(
    finding_id: str,               # auto-generated, unique
    claim: str,                    # e.g. "high_link_utilization" or "root_cause:hardware_failure"
    device: str | None,
    evidence_refs: list[str],      # obs_ids or other finding_ids; advisory only, see section 3
    grounding: "observed" | "inferred" | "unsupported",  # ALWAYS set by the store
    detail: str,
)
```

### Message types

| `MsgType` | Direction | Purpose |
|---|---|---|
| `task` | orchestrator → agent | "Analyze these devices" (or, for correlation, "rank hypotheses for these devices"). `payload = {"devices": [...]}`. |
| `findings` | agent → orchestrator | The agent's grounded Findings for the task, plus `missing`/`assumptions`. For the correlation agent, `payload["devices"]` carries the full per-device hypothesis ranking. |
| `check_request` | orchestrator → checks | A targeted check is being requested. `payload = {"check": name, "device": ..., "discriminates": [hyp_a, hyp_b]}`. |
| `check_result` | checks → orchestrator | The outcome of that check. `payload = {"status": "applied" \| "check_failed", "result": "..."}`. |
| `error` | agent/checks → orchestrator | A failure of any kind (malformed reply, exception, timeout, duplicate msg_id, correlation failure) — see section 10's error catalog. `errors` carries the message(s). |
| `report` | reporting_agent → orchestrator | The final, terminal message for an incident. `payload` is the full `Report` dict (section 8's shape). |

### Versioning policy

- `SCHEMA_VERSION = "1.0"` is the version new `Envelope`s are built with;
  `SUPPORTED_VERSIONS = {"1.0"}` is the full set this build accepts.
- `Envelope`'s `schema_version` field validator rejects any value outside
  `SUPPORTED_VERSIONS` **at construction time** — a pydantic `ValidationError`,
  not a runtime check somewhere downstream. An agent that tries to build an
  Envelope with an unsupported version fails before it can even return it;
  the orchestrator's `_safe_handle()` catches that exception the same way it
  catches any other agent failure (section 10).
- **To add a new protocol version**: add the new string to `SUPPORTED_VERSIONS`
  (both old and new versions are now accepted simultaneously — this is an
  additive, backward-compatible window, not a hard cutover), migrate internal
  callers to build `Envelope`s with the new `schema_version` once ready, and
  only then remove the old string from `SUPPORTED_VERSIONS` in a later change.
  There is deliberately no automatic up-conversion between versions: a
  version-1.0 message is never silently reinterpreted as 1.1's shape.

## 3. Grounding rules (`netcoop/evidence.py`)

| Grounding | Condition | Who decides |
|---|---|---|
| `observed` | `evidence_refs` contains at least one real `obs_id` in the store | `EvidenceStore.ground()` |
| `inferred` | `evidence_refs` contains no `obs_id`, but at least one `finding_id` whose own grounding is already `observed` or `inferred` | `EvidenceStore.ground()` |
| `unsupported` | Neither of the above — including a Finding whose `device` is set but isn't in `store.known_devices` (even if its `evidence_refs` would otherwise resolve to real observations) | `EvidenceStore.ground()` |

Three properties fall out of this, each exercised by a test:

1. **A sender's claimed grounding is always ignored.** `Finding(grounding="observed", evidence_refs=[])` is still relabeled `unsupported` — the field exists on the model so a sender *can* claim something, precisely so the store can prove that claim wrong.
2. **Repetition isn't evidence.** A Finding that cites only an `unsupported` Finding's `finding_id` is itself `unsupported`, not `inferred` — a rumor doesn't gain credibility by being repeated (`tests/test_milestone1.py::test_repeated_claim_does_not_become_evidence`).
3. **An unknown device poisons grounding regardless of evidence_refs.** `store.known_devices` is populated by the orchestrator after ingest as `{devices with observations} ∪ {topology-known devices}` (so a topology node with no telemetry of its own, like a distribution switch, still grounds normally) — a Finding naming anything outside that set is forced to `unsupported` (`tests/test_milestone7.py::test_finding_with_unknown_device_grounds_unsupported`).

`EvidenceStore.trace(ref)` walks a `finding_id`/`obs_id` back to the `raw_ref`(s) it ultimately rests on — this is what populates `supporting_evidence[].raw_ref` in the final report, so every number in a report can be traced to an exact original record.

## 4. Interoperability: two input schemas, one canonical form

- **Schema A** (`adapters/schema_a.py`) — the official generator's format
  exactly. A field's canonical unit is derived from its name suffix via an
  explicit table (`_pct` → `pct`, `_ms` → `ms`, `_mbps` → `Mbps`, `_dbm` →
  `dBm`); a field with an unrecognized suffix or base name is rejected with a
  reason, never guessed.
- **Schema B** (`adapters/mapped.py` + `mappings/vendor_b.yaml`) — a
  deliberately different vendor format (flat, `hostname`, `lat_s` seconds,
  `loss_ratio` 0–1, epoch timestamps). Field mapping is declarative YAML, not
  hand-written if-statements.
- **`netcoop/ingest.py`** sniffs which adapter a payload belongs to
  (`device_id`+`metrics` → Schema A; a `schema` tag → look up a matching
  mapping) and feeds the result into the `EvidenceStore`. A file with some
  bad records still yields every good observation; malformed JSON, an
  unknown schema tag, and an unsupported schema version are all rejected
  with a specific reason rather than crashing the run.

Both schemas, given the same incident, produce byte-identical canonical
`Observation`s (verified in `tests/test_milestone2.py`).

### Schema B mapping file reference

`netcoop/mappings/vendor_b.yaml`'s top-level keys, all consumed by
`adapters/mapped.py::parse()`:

| Key | Meaning |
|---|---|
| `schema` | The exact `"<name>/<version>"` tag a record's own `schema` field must match. A name mismatch is rejected as `"unknown schema"`; a version mismatch (same name) as `"unsupported ... version"` — two different, distinguishable errors (section 10). |
| `device_field` | Which raw JSON key holds the device identifier (e.g. `"hostname"`). |
| `timestamp_field` | Which raw JSON key holds the timestamp. |
| `timestamp_format` | `"iso8601"` (default) or `"epoch_s"` (Unix seconds). |
| `status_field` | (optional) Which raw JSON key holds the device-status string; mapped to the `device_status` metric. |
| `fields` | A dict of `<raw field name>: {metric: <canonical metric>, unit: <raw unit, or null for counts>}`. Each entry drives one call to `metrics.normalize_metric()` — the same validation path Schema A uses, so a vendor-specific unit quirk (e.g. `loss_ratio` as 0–1 instead of a percentage) is handled by unit conversion, not a special case in the adapter code. |

### Adding a new vendor format (Schema C, D, ...)

No code changes are required for a vendor whose data fits the "device + timestamp + flat metric fields + optional status" shape `adapters/mapped.py` already handles:

1. Create `netcoop/mappings/<vendor>.yaml` following the reference table above — map each raw field to a canonical metric name from `netcoop/metrics.py` and its raw unit.
2. That's it. `netcoop/ingest.py::detect_and_parse()` scans every `*.yaml` in `netcoop/mappings/` via `adapters.mapped.find_mapping_for_schema()`, matching on the record's own `schema` tag name — a new mapping file is picked up automatically, nothing to register.
3. Add a sample input file and, if it's exercising new ground, a scenario entry — see `scripts/gen_scenarios.py`'s `to_schema_b()` for a worked example of converting a Schema A record into another flat vendor shape.

If the new vendor's shape doesn't fit that pattern at all (nested records, a
genuinely different structure), write a new adapter module alongside
`adapters/schema_a.py`/`adapters/mapped.py` that produces `Observation`s the
same way, and add a detection branch to `ingest.py::detect_and_parse()`.

## 5. Canonical metrics (`netcoop/metrics.py`, `netcoop/units.py`)

Every metric has a registry entry of `(name, unit, kind)`:

- **gauge** — unit-bearing, converted via `units.normalize()` (e.g. `latency`
  → `ms`). A missing or ambiguous unit is rejected, never assumed.
- **count** — cumulative, unitless, reported as an absolute count, never a
  rate (`queue_drops`, `interface_errors`, `dhcp_failures`).
- **categorical** — an enum/string state, not numeric (`device_status`).

## 6. Agents

Every analysis agent implements `agents/base.py`'s `Agent`: `analyze(device,
store) -> list[Finding]`, and `handle(task, store) -> Envelope` grounds every
Finding through the store before returning. `latest_observation()` is the
one place "freshness" is decided: an observation older than `max_age`
(10 minutes by default) relative to the *latest timestamp anywhere in the
incident* — not wall-clock time, so static scenario files stay reproducible
— is treated as if it weren't there. `is_only_stale()` distinguishes that
case from "never reported" for reporting purposes, and `has_conflicting_latest()`
flags two observations disagreeing at the same timestamp as an `assumption`
instead of silently picking one.

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

## 7. Correlation algorithm (`agents/correlation.py`, `netcoop/confidence.py`)

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
**inferred**, never observed (section 3). A Finding whose grounding resolves
to `unsupported` (a rumor) is recorded in `assumptions` but excluded from
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

## 8. Targeted diagnostic checks (`netcoop/checks.py`)

Checks are **named and declared**, not derived from a generic heuristic.
`rules.yaml`'s `checks` section lists each one — a stable name
(`upstream_link_utilization`, `interface_counters_next_window`,
`wifi_client_rssi`, `dns_probe`), the single metric it re-polls, and which
hypothesis pairs it's able to discriminate between:

```yaml
checks:
  upstream_link_utilization:
    metric: link_utilization
    discriminates: [[link_congestion, interface_fault], [link_congestion, cpu_overload], ...]
```

When the top two hypotheses are too close (margin < 0.2), the orchestrator
looks up `checks.discriminating_check(top_hypothesis, second_hypothesis)` —
the first declared check whose `discriminates` list contains that unordered
pair — and requests it, up to 2 rounds per incident, stopping early once
margin ≥ 0.2. If no check is declared for that exact pair, it's recorded as
`check_failed` (`"no discriminating check declared in rules.yaml for this
hypothesis pair"`) and the loop stops rather than guessing.

A check is not live telemetry — there are no real devices — it's a
pre-authored file at `<checks_dir>/<check_name>.json`, ingested through the
*exact same* `ingest.load_file` validation path as ordinary input (so a
malformed check file is rejected the same way a malformed input file would
be, never crashes the run). It represents what a human or script would
obtain by actually running that specific diagnostic action right now. If the
file doesn't exist, or ingest rejects every record in it, the outcome is
`check_failed`, recorded plainly, and diagnosis continues on whatever
evidence is already on hand — a check never fabricates data or lowers a
threshold to manufacture a conclusion.

Applying a check re-runs every analysis agent and the correlation agent
against the updated store, so new data can genuinely change the ranking.
Two scenarios demonstrate a check changing the outcome outright:
`scenarios/checks/stale/` and `scenarios/checks/insufficient/` both resolve
their scenario from `insufficient_evidence` to a confident diagnosis.
`scenarios/inputs/ambiguous_loss.json` is the flagship case — `link_congestion`
and `interface_fault` are genuinely tied without a check, and the *same*
declared check (`upstream_link_utilization`) resolves to opposite diagnoses
depending on which variant directory answers it:

```
python -m netcoop run scenarios/inputs/ambiguous_loss.json --checks scenarios/checks/ambiguous_loss_A
# -> "Link congestion" (high upstream utilization)
python -m netcoop run scenarios/inputs/ambiguous_loss.json --checks scenarios/checks/ambiguous_loss_B
# -> "Interface or cabling fault on a single link" (low utilization, rising error counters)
```

`scenarios/checks/contradictory/` deliberately doesn't exist, demonstrating
the `check_failed` path when a checks directory is supplied but holds
nothing useful for this incident.

## 9. Topology and multi-device scope (`netcoop/topology.py`)

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

## 10. Orchestration and error handling (`netcoop/orchestrator.py`)

```
ingest(file) -> EvidenceStore
  -> dispatch TASK to Performance, Infrastructure, Connectivity
       -> each reply validated as a well-formed Envelope; a malformed reply,
          an agent exception, or an agent that hangs past its time limit is
          caught, logged as an ERROR Envelope, and the run continues with
          whatever the other agents produced
  -> dispatch TASK to Correlation -> ranked Hypotheses per device
       -> if correlation itself fails, that's caught too: a degenerate
          empty-payload Envelope stands in so reporting can still run
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

**Never crashes**: every one of the following is caught and turned into an
`error` Envelope rather than an exception escaping `Orchestrator.run()` —
proven by one test per case in `tests/test_milestone7.py`: a malformed agent
reply, an agent that raises, an agent that exceeds its time limit (a daemon
thread is abandoned, not killed — Python can't force-kill a thread — and the
run continues without it), a reply that reuses a `msg_id`, the correlation
step itself failing, an empty input file, and a file where every record is
rejected.

### Error and rejection catalog

Every distinct failure the system can produce, grouped by where it
originates. "Type" is how it surfaces: a Python exception (caught at the
nearest boundary), a `(raw_ref, reason)` entry in a rejection list, or a
`status` field on a structured result.

**Unit / metric validation** (`netcoop/units.py`, `netcoop/metrics.py` — raised as `UnitError`/`MetricError`, always caught by the calling adapter and turned into a rejection)

| Condition | Message |
|---|---|
| Metric name not in the registry | `unknown metric {metric!r}` |
| Gauge metric given no unit | `{metric}: missing unit (refusing to assume {target})` |
| `unit="ratio"` but value is outside [0,1] or metric isn't percentage-based | `{metric}: ratio value {value} out of [0,1] or not a percentage metric` |
| Unit given has no known conversion to the canonical unit | `{metric}: unit {unit!r} not convertible to {target}` |
| Count metric given a non-bare unit | `{metric}: expected a bare count, got unit {unit!r}` |
| Count value is boolean, non-numeric, or negative | `{metric}: invalid count value {value!r}` / `{metric}: count cannot be negative ({value!r})` |
| Categorical value is missing or blank | `{metric}: missing categorical value` |

**Schema / timestamp validation** (`protocol.py` field validators — pydantic `ValidationError` at construction; `adapters/*.py` — caught, becomes a rejection)

| Condition | Message |
|---|---|
| `Observation.timestamp` has no tzinfo | `timestamp must be timezone-aware (refusing to guess a timezone)` |
| `Envelope.schema_version` not in `SUPPORTED_VERSIONS` | `unsupported schema_version {v!r}; supported: {...}` |
| A parsed record's timestamp is naive | `malformed record: naive timestamp (refusing to guess a timezone)` |
| Mapped adapter given an unknown `timestamp_format` | `unknown timestamp_format {fmt!r}` |
| Schema B record's `schema` tag name doesn't match the mapping | `unknown schema {rec_schema!r} (expected {exp_name!r})` |
| Schema B record's `schema` tag version doesn't match | `unsupported {name} version {version!r} (expected {exp_version!r})` |

**Topology** (`topology.py` — raised as `TopologyError`)

| Condition | Message |
|---|---|
| A query names a device outside `known_devices()` | `unknown device {device!r}` |
| `topology.json`'s links reference an undeclared device | `topology.json references undeclared devices: {...}` |

**Ingestion** (`ingest.py`, `evidence.py` — rejection-list entries, file still loads whatever else is good)

| Condition | Message |
|---|---|
| File isn't valid JSON | `malformed JSON: {e}` |
| File can't be read at all | `cannot read file: {e}` |
| First record isn't a JSON object | `record is not a JSON object` |
| No `device_id`/`metrics` and no `schema` tag | `unrecognized input format: no device_id/metrics and no schema tag` |
| `schema` tag doesn't match any mapping file | `unknown schema {tag!r}` |
| Metric field's suffix isn't a recognized unit | `unrecognized unit suffix in field {field!r}` |
| Metric field's base name isn't recognized (suffix was fine) | `unrecognized field {field!r}` |
| Observation's device not in `store.known_devices` | `unknown device {obs.device!r}` |
| Identical (device, metric, timestamp, value) already stored | `duplicate observation` |

**Orchestration** (`orchestrator.py` — caught by `_safe_handle()`, always wrapped as `"{agent_name} failed: {reason}"` and logged as an `error` Envelope)

| Condition | Message |
|---|---|
| Agent/correlation call exceeds `agent_timeout_seconds` | `exceeded the {N}s time limit` |
| Agent returned something other than an `Envelope` | `returned {type}, not an Envelope` |
| Agent reply reused a `msg_id` already seen this incident | `reply reused msg_id {id!r}, already seen this incident` |
| Agent raised any exception | *(the exception's own message, propagated as-is)* |
| No discriminating check declared for the top-two pair | `no discriminating check declared in rules.yaml for this hypothesis pair` |
| No `--checks` directory was provided | `no checks directory provided` |

**Checks** (`checks.py` — a `CheckOutcome.status` of `"check_failed"`, never raised)

| Condition | Message |
|---|---|
| No file at `<checks_dir>/<check_name>.json` | `no check file found for {check_name!r}` |
| File exists but every record was rejected | `check {check_name!r} was rejected: {reason}` |

**Non-error assumptions** (surfaced in `Envelope.assumptions` / the report's `limitations`, not failures)

| Condition | Message |
|---|---|
| Two observations disagree at the same latest timestamp | `conflicting readings for {device}:{metric} at the same timestamp; one was picked arbitrarily` |
| Connectivity agent has no topology | `scope limited to reporting device: no topology was provided` |
| Connectivity agent's devices aren't all in the known topology | `scope limited to reporting device: {devices} not in the known topology` |
| Correlation found a signal claim grounded `unsupported` | `ignored unsupported claim {signal_id!r} for {device} (cites no verifiable evidence)` |

## 11. Running it

```
pytest -q
python -m netcoop run scenarios/inputs/<file>.json \
    [--topology scenarios/topology.json] \
    [--checks scenarios/checks/<scenario>]
python -m netcoop demo       # a 5-step guided walkthrough, pausing for Enter
streamlit run app.py         # browse completed incidents from the real log/report files
python eval/run_eval.py      # compares every scenario against scenarios/ground_truth.json
python scripts/gen_protocol_tables.py --write   # regenerate this doc's field tables
```

`ground_truth.json` is read only by `eval/run_eval.py` and by humans — no
agent or orchestrator code reads it, per CLAUDE.md's non-negotiable rule 2.
