"""Pydantic model for the final diagnostic report: the required fields from
the problem statement (page 5) plus the fields CLAUDE.md adds for evidence
traceability and reproducibility.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: str
    value: float | str | bool
    unit: str | None
    interpretation: str
    obs_id: str
    raw_ref: str


class HypothesisScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    label: str
    score: float
    coverage: float
    support: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class CheckPerformed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    result: str


class RejectedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_ref: str
    reason: str


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    status: Literal["diagnosed", "insufficient_evidence", "invalid_input", "partial"]
    suspected_root_cause: str | None = None
    confidence: float
    confidence_category: Literal["high", "medium", "low"]
    confidence_method: str
    affected_devices: list[str] = Field(default_factory=list)
    supporting_evidence: list[SupportingEvidence] = Field(default_factory=list)
    alternative_causes: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    hypotheses: list[HypothesisScore] = Field(default_factory=list)
    checks_performed: list[CheckPerformed] = Field(default_factory=list)
    rejected_inputs: list[RejectedInput] = Field(default_factory=list)
    message_log: str
