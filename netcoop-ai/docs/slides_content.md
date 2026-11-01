# NetCoop AI — Slide Content

8 slides, ≤40 words each. Sourced from `README.md`, `netcoop/` code, `logs/`, and `eval/results/results.md`.

---

## 1. Problem

Campus network faults (congestion, bad interface, weak Wi-Fi, DNS/DHCP outage, CPU overload, hardware failure) aren't obvious from one metric. Telemetry is incomplete, stale, or arrives in incompatible vendor formats. Diagnosing the true cause — and knowing when *not* to guess — is hard.

## 2. One-line idea

Evidence-first cooperative diagnosis: five agents exchange self-grounding evidence over one shared protocol; a deterministic rules engine ranks root-cause hypotheses and abstains when evidence genuinely doesn't support a conclusion.

## 3. Architecture

Components (as in code): `ingest.py` (schema detect) → `EvidenceStore` (`evidence.py`) → **Performance, Infrastructure, Connectivity** agents (`agents/`) → `Orchestrator` → **Correlation** agent (`rules.yaml` scoring) → targeted `checks.py` loop → **Reporting** agent (`report.py`) → `logs/<incident_id>.jsonl`.

## 4. Message format

One real `Envelope` (`logs/INC-581b38d1.jsonl`, trimmed):
```json
{"msg_id":"msg-861c5634d9","type":"findings","sender":"performance_agent",
 "recipient":"orchestrator",
 "findings":[{"claim":"high_link_utilization","device":"SW_CAMPUS_1",
              "evidence_refs":["obs-2078924a8e"],"grounding":"observed"}]}
```

## 5. Demo

`python -m netcoop run <scenario>.json` renders a live colored agent-message timeline, hypothesis scores, checks performed, and the final report. `python -m netcoop demo` runs a fixed guided walkthrough across several bundled scenarios.

## 6. Handling bad data

A record with a missing unit, unrecognized field, naive timestamp, unsupported schema version, or malformed JSON is rejected with a specific reason — the rest of the file still loads (`scenarios/inputs/broken_*.json`). The orchestrator never crashes; failures become an `error` Envelope plus a degraded report.

## 7. Results

*(Verbatim from `eval/results/results.md`.)* **Dataset:** 13 scenarios, entirely **synthetic data** (fixed random seed, no production campus network data).

| Metric | NetCoop pipeline | Naive baseline |
|---|---|---|
| Root-cause accuracy | 100% | 100% |
| Correct abstention rate | 100% | 0% |
| False confident diagnoses | 0/4 (0%) | 4/4 (100%) |
| Overall status accuracy | 100% | 69% |

## 8. Limitations

*(Copied from `README.md` → "Known limitations".)*
- Evaluation dataset is small and entirely synthetic (13 hand-designed scenarios).
- `rules.yaml` thresholds are reasonable defaults, not calibrated against a larger labeled dataset.
- Only one hypothesis pair is exercised end-to-end by a bundled scenario's targeted check.
- No connection to a real network; no automatic remediation; no MCP/HITL/durable pause-resume.

---

### ⚠ Flags — statements not independently verifiable by me from code/results alone

- **Slide 3 "targeted checks loop"**: confirmed in `README.md`'s orchestration-flow pseudocode and `checks.py`'s existence, but I did not execute the loop myself — its runtime behavior is taken from the README description, not from running a check live.
- **Slide 5 "Demo" wording**: `cli.py` confirms a `demo` subcommand and a fixed walkthrough exist; I did not run `python -m netcoop demo` myself in this session to confirm its on-screen output matches the description.
- **Slide 7 "Mean latency (s): 0.0071 / 0.0001"** (omitted from the slide for brevity/word limit) and the **9 diagnosed / 4 abstention scenario split**: both verbatim in `eval/results/results.md`, but I did not re-run `eval/run_eval.py` myself — the numbers are copied from the committed results file, not freshly regenerated.
- All other slide content (problem, architecture component names, Envelope fields, bad-data handling, limitations) is copied or directly derived from current file contents I read in this session: `README.md`, `netcoop/protocol.py`, `logs/INC-581b38d1.jsonl`, `eval/results/results.md`, `netcoop/cli.py`.
