#!/usr/bin/env python
"""Demonstrates the core differentiator: EvidenceStore.ground() decides a
Finding's grounding from what's actually in the store, never from what the
sender claims -- so a rogue agent's unsupported claim, and a second agent
that merely echoes it, can never move a diagnosis.

Step 1: run the real, unmodified Orchestrator on scenarios/inputs/congestion.json
and record the top hypothesis and score.

Step 2: run it again with a subclass (defined only in this script) that, right
after the real analysis agents report in, injects two fake findings:
  - rogue_agent: claims root_cause:dns_dhcp_failure, self-reports
    grounding="observed", cites NO evidence_refs.
  - echo_agent: cites only rogue_agent's finding_id as its "evidence" --
    an echo of a rumor, not an independent observation.
Both are handed to the same EvidenceStore.ground() every real finding goes
through. Prints a table of claimed vs. assigned grounding, then the top
hypothesis/score again to show it is unchanged.

Composes only netcoop's existing public API (Orchestrator, EvidenceStore,
Finding, Envelope); nothing under netcoop/ is modified.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from netcoop.evidence import EvidenceStore  # noqa: E402
from netcoop.orchestrator import Orchestrator  # noqa: E402
from netcoop.protocol import Finding  # noqa: E402
from netcoop.report import Report  # noqa: E402

SCENARIO = ROOT / "scenarios" / "inputs" / "congestion.json"

console = Console()


class RogueInjectingOrchestrator(Orchestrator):
    """Same pipeline as Orchestrator, but after each round of real agent
    dispatch, injects two fake findings from agents that were never asked to
    analyze anything. Only defined here, as a subclass, in this script --
    netcoop/orchestrator.py itself is untouched."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.injected: list[tuple[Finding, Finding]] = []  # (as-sent, as-graded) pairs

    def _dispatch_agents(self, incident_id: str, devices: list[str], store: EvidenceStore):
        findings, sources, assumptions, had_errors = super()._dispatch_agents(incident_id, devices, store)
        if not devices:
            return findings, sources, assumptions, had_errors
        device = devices[0]

        rogue = Finding(
            claim="root_cause:dns_dhcp_failure", device=device, evidence_refs=[],
            grounding="observed",
            detail="rogue_agent unilaterally asserts dns_dhcp_failure with no supporting evidence",
        )
        rogue_graded = store.ground(rogue)

        echo = Finding(
            claim="echo:dns_dhcp_failure", device=device, evidence_refs=[rogue_graded.finding_id],
            grounding="observed",
            detail="echo_agent repeats rogue_agent's claim, citing it as its only evidence",
        )
        echo_graded = store.ground(echo)

        self.injected.append((rogue, rogue_graded))
        self.injected.append((echo, echo_graded))
        return findings + [rogue_graded, echo_graded], sources, assumptions, had_errors


def top_hypothesis(report: Report):
    if not report.hypotheses:
        raise RuntimeError(f"report {report.incident_id} produced no hypotheses: status={report.status}")
    return report.hypotheses[0]


def print_grounding_table(injected: list[tuple[Finding, Finding]]) -> None:
    table = Table(title="Rogue findings: claimed vs. EvidenceStore-assigned grounding")
    table.add_column("finding")
    table.add_column("sender")
    table.add_column("claimed grounding")
    table.add_column("assigned grounding")
    senders = ["rogue_agent", "echo_agent"]
    for sender, (raw, graded) in zip(senders, injected):
        table.add_row(raw.claim, sender, raw.grounding, graded.grounding)
    console.print(table)


def main() -> int:
    console.rule("Step 1 -- clean pipeline")
    clean_run = Orchestrator()
    report1 = clean_run.run(SCENARIO, incident_id="DEMO-grounding-clean")
    top1 = top_hypothesis(report1)
    console.print(f"Top hypothesis: [bold]{top1.hypothesis}[/] ({top1.label}), score={top1.score}")

    console.rule("Step 2 -- inject rogue_agent + echo_agent findings")
    rogue_run = RogueInjectingOrchestrator()
    report2 = rogue_run.run(SCENARIO, incident_id="DEMO-grounding-rogue")
    top2 = top_hypothesis(report2)

    print_grounding_table(rogue_run.injected)
    console.print(f"Top hypothesis: [bold]{top2.hypothesis}[/] ({top2.label}), score={top2.score}")

    assert len(rogue_run.injected) == 2, "expected exactly one rogue finding and one echo finding"
    assert all(graded.grounding == "unsupported" for _, graded in rogue_run.injected), (
        "rogue_agent's and echo_agent's findings must both ground as 'unsupported'"
    )
    assert top2.hypothesis == top1.hypothesis and top2.score == top1.score, (
        "injecting unsupported findings must not change the top hypothesis or its score"
    )
    console.print(
        "[bold green]PASS[/] -- both injected findings graded 'unsupported'; "
        "top hypothesis/score unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
