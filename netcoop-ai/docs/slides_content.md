# NetCoop AI — Slide Content

9 slides, ≤40 words each. Sourced from `README.md`, `netcoop/` code, `logs/`,
`rules.yaml`, and `eval/results/results.md`.

---

## 1. Problem

Campus network faults (congestion, bad interface, weak Wi-Fi, DNS/DHCP
outage, CPU overload, hardware failure) aren't obvious from one metric.
Telemetry arrives incomplete, stale, or in incompatible vendor formats.
Diagnosing the cause — without guessing — is hard.

**Speaker notes:**
Set up the stakes before showing any architecture. The hard part isn't
detecting a fault — it's knowing which of several plausible causes actually
explains it, with evidence that's often partial or contradictory.

## 2. Our edge

Most diagnosis systems always give an answer. Ours knows when it doesn't
know, asks for exactly the evidence that would settle it, and never lets an
agent's unsupported claim count as evidence.

**Speaker notes:**
This is the one sentence to remember if nothing else lands. Two separate
claims: abstention (saying "insufficient evidence") and grounding (an
agent's word alone never counts). Both are enforced in code, not convention.

## 3. Architecture

`ingest.py` (schema detect) → `EvidenceStore` (`evidence.py`) →
**Performance, Infrastructure, Connectivity** agents → `Orchestrator` →
**Correlation** agent (`rules.yaml` scoring) → `checks.py` loop →
**Reporting** agent (`report.py`) → `logs/<incident_id>.jsonl`.

**Speaker notes:**
Every arrow is the same message shape, `Envelope`. Point out that the
Correlation agent re-scores against the raw store every time — it never
trusts what an upstream agent merely claims.

## 4. Message protocol

One real `Envelope` (`logs/INC-581b38d1.jsonl`, trimmed):
```json
{"msg_id":"msg-861c5634d9","incident_id":"INC-581b38d1","type":"findings",
 "sender":"performance_agent","recipient":"orchestrator",
 "findings":[{"claim":"high_link_utilization","device":"SW_CAMPUS_1",
              "evidence_refs":["obs-2078924a8e"],"grounding":"observed"}]}
```

**Speaker notes:**
This is an unedited line from a real log file, not a mock-up. `grounding`
is never set by the agent that sends this — `EvidenceStore.ground()`
recomputes it from whether `obs-2078924a8e` actually exists in the store.

## 5. Adaptive check

`ambiguous_loss`: first-pass scores tie `link_congestion` and
`interface_fault` (margin 0.075 < required 0.2), triggering the
`upstream_link_utilization` check. Variant A → high utilization →
congestion. Variant B → low utilization, rising errors → interface fault.

**Speaker notes:**
Live-run both variants of the same input file against the same declared
check to show the ranking genuinely flips based on which answer comes back
— it isn't hardcoded per scenario. Margin threshold (0.2) is in `rules.yaml`.

## 6. Interoperability

Two schemas, one canonical form: Schema A (official generator, unit from
field-name suffix) and Schema B (flat, seconds, 0–1 ratios) both map via
declarative YAML. Adding vendor C needs only one new mapping file — zero
code changes.

**Speaker notes:**
`demo/vendor_c.yaml` is a prepared, not-yet-installed third mapping
(`vendorC/1.0`: microsecond latency, ratio metrics). Live demo: run its
sample file, show it's rejected, copy the YAML in, re-run, show it matches.

## 7. Guardrails by design

| Guardrail | What it does |
|---|---|
| Input & message validation | Malformed/unrecognized data and envelopes rejected with a reason, never crash |
| Evidence grounding | The store decides grounding from citations, never the sender's word |
| Abstention | `insufficient_evidence` when score/coverage/margin are too low |
| Bounded checks | At most 2 targeted checks per incident |
| Fault isolation | Agent exception/timeout/malformed reply caught; run continues |
| Audit trail | Every Envelope logged to JSONL; every number traces to a `raw_ref` |

**Speaker notes:**
Proving tests, one per row: `tests/test_milestone2.py::test_broken_scenario_files_produce_expected_reason`
and `tests/test_milestone7.py::test_duplicate_envelope_msg_id_is_rejected_not_crashed`
(input & message validation); `tests/test_milestone1.py::test_repeated_claim_does_not_become_evidence`
(evidence grounding); `tests/test_milestone5.py::test_insufficient_scenario_abstains_from_naming_a_cause`
(abstention); `tests/test_milestone6.py::test_never_more_than_two_checks`
(bounded checks); `tests/test_milestone7.py::test_agent_timeout_does_not_crash`
(fault isolation); `tests/test_milestone5.py::test_check_request_and_result_envelopes_are_logged`
and `tests/test_milestone3.py::test_orchestrator_supporting_evidence_obs_id_traces_to_raw_ref`
(audit trail).

## 8. Results

*(Verbatim from `eval/results/results.md`.)* 15 evaluation runs, entirely
**synthetic data** (fixed random seed). Confidence is a documented
heuristic, not a calibrated probability.

| Metric | NetCoop pipeline | Naive baseline |
|---|---|---|
| Root-cause accuracy | 100% | 91% |
| Correct abstention rate | 100% | 0% |
| False confident diagnoses | 0/4 (0%) | 4/4 (100%) |
| Average checks used | 0.13 | 0.00 |
| Overall status accuracy | 100% | 73% |

**Speaker notes:**
"15 runs" = 13 hand-designed scenarios + 2 `ambiguous_loss__checks` variants
that exercise `checks_dir` end to end. The naive baseline never abstains —
that's why its false-confident-diagnoses rate is 4/4 against the pipeline's 0/4.

## 9. Limitations

*(Condensed from `README.md` → "Known limitations".)* Evaluation dataset is
small and synthetic (13 scenarios, 15 runs). `rules.yaml` thresholds aren't
calibrated against real data. Only one hypothesis pair has a bundled
end-to-end check. No real network connection, automatic remediation, or
MCP/HITL workflow.

**Speaker notes:**
Be upfront about these unprompted — it preempts the obvious judge question
and signals the mechanism (grounding, abstention, checks changing a ranking)
is the demonstrated claim, not large-scale statistical accuracy.

---

### ⚠ Flags — statements not independently verifiable by me from code/results alone

- **Slide 5's live "flip the ranking" demo**: confirmed by running
  `python -m netcoop run scenarios/inputs/ambiguous_loss.json --checks scenarios/checks/ambiguous_loss_A`
  myself this session (margin 0.075, final cause "Link congestion"); I did
  not separately re-run variant B in this pass, though `tests/test_milestone6.py::test_ambiguous_loss_variant_b_resolves_to_interface_fault`
  covers it and `eval/results/results.md` row `ambiguous_loss__checks_B`
  agrees.
- **Slide 6 "zero code changes" for vendor C**: verified that
  `netcoop/ingest.py::detect_and_parse()` auto-discovers any `*.yaml` in
  `netcoop/mappings/`, and that running `demo/vendor_c_congestion.json`
  today (mapping not installed) produces `unknown schema 'vendorC/1.0'` — I
  did not actually copy the file into `netcoop/mappings/` and re-run, since
  that would alter the running repo; this is exactly the live step the
  speaker notes ask the presenter to perform on stage.
- All other content (problem, architecture, Envelope fields, correlation
  math, interoperability, guardrail test names, results numbers,
  limitations) was copied or directly derived from current file contents
  read in this session: `README.md`, `netcoop/rules.yaml`,
  `logs/INC-581b38d1.jsonl`, `eval/results/results.md`, `tests/*.py`,
  `demo/vendor_c.yaml`.
