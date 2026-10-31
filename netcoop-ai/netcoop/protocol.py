"""NetCoop shared message protocol (v1).

Design rule (the core differentiator): a claim only counts as *observed* if it
cites raw observation IDs that exist in the EvidenceStore. Anything an agent
asserts on top of that is *inferred*, and an inference never becomes evidence
just because another agent repeats it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.0"
SUPPORTED_VERSIONS = {"1.0"}


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


class MsgType(str, Enum):
    TASK = "task"                  # orchestrator -> agent
    FINDINGS = "findings"          # agent -> orchestrator
    CHECK_REQUEST = "check_request"
    CHECK_RESULT = "check_result"
    ERROR = "error"
    REPORT = "report"


class Observation(BaseModel):
    """A single normalized telemetry measurement. Immutable once stored."""
    model_config = ConfigDict(frozen=True, extra="forbid")

    obs_id: str = Field(default_factory=lambda: _id("obs"))
    device: str
    metric: str                    # canonical metric name, e.g. "latency"
    value: float | str | bool
    unit: str | None               # canonical unit, e.g. "ms"; None for unitless
    timestamp: datetime
    source: str                    # which input feed / adapter produced it
    raw_ref: str                   # pointer back to the original record (file:line or record id)
    original_unit: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (refusing to guess a timezone)")
        return v.astimezone(timezone.utc)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=lambda: _id("fnd"))
    claim: str                     # short machine-readable claim, e.g. "high_latency"
    device: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)   # obs_ids or finding_ids
    # Set by the EvidenceStore, never trusted from the sender:
    grounding: Literal["observed", "inferred", "unsupported"] = "unsupported"
    detail: str = ""


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str = Field(default_factory=lambda: _id("msg"))
    incident_id: str
    schema_version: str = SCHEMA_VERSION
    sender: str
    recipient: str
    type: MsgType
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    findings: list[Finding] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)       # evidence the sender lacked
    assumptions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    @field_validator("schema_version")
    @classmethod
    def _supported(cls, v: str) -> str:
        if v not in SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported schema_version {v!r}; supported: {sorted(SUPPORTED_VERSIONS)}")
        return v
