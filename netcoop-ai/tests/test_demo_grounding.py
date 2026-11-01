"""Covers scripts/demo_grounding.py's claim: EvidenceStore.ground() grades a
rogue agent's unsupported root-cause claim, and a second agent's echo of it,
as 'unsupported' -- and neither can move the top hypothesis or its score."""
from scripts.demo_grounding import SCENARIO, RogueInjectingOrchestrator, top_hypothesis

from netcoop.orchestrator import Orchestrator


def test_rogue_and_echo_findings_are_unsupported_and_ranking_is_unchanged(tmp_path):
    clean = Orchestrator(logs_dir=tmp_path / "clean")
    report1 = clean.run(SCENARIO, incident_id="TEST-grounding-clean")
    top1 = top_hypothesis(report1)

    rogue_run = RogueInjectingOrchestrator(logs_dir=tmp_path / "rogue")
    report2 = rogue_run.run(SCENARIO, incident_id="TEST-grounding-rogue")
    top2 = top_hypothesis(report2)

    assert len(rogue_run.injected) == 2
    assert all(graded.grounding == "unsupported" for _, graded in rogue_run.injected)
    assert top2.hypothesis == top1.hypothesis
    assert top2.score == top1.score
