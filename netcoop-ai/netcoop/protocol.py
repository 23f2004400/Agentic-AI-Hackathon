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

    obs_id: str = Field(default_factory=lambda: _id("obs"),
                         description="Unique ID for this observation, auto-generated (obs-<hex>).")
    device: str = Field(description="Device identifier exactly as reported by the source feed.")
    metric: str = Field(description="Canonical metric name from netcoop/metrics.py, e.g. 'latency'.")
    value: float | str | bool = Field(
        description="The measurement. float for gauge/count metrics, str for categorical "
                    "(e.g. device_status), bool only if a source ever reports one.")
    unit: str | None = Field(
        description="Canonical unit after conversion (e.g. 'ms', 'pct'), or null for unitless "
                    "count/categorical metrics. Never guessed: ingestion rejects an ambiguous unit "
                    "instead of defaulting one.")
    timestamp: datetime = Field(
        description="When the measurement was taken. Must be timezone-aware; stored normalized to UTC.")
    source: str = Field(description="Which input feed/adapter produced this observation (e.g. a filename).")
    raw_ref: str = Field(
        description="Pointer back to the exact original record, e.g. 'file.json#2.cpu_utilization_pct'. "
                    "This is what EvidenceStore.trace() ultimately resolves a claim down to.")
    original_unit: str | None = Field(
        default=None, description="The unit as reported by the source before conversion to `unit`, for audit.")

    @field_validator("timestamp")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware (refusing to guess a timezone)")
        return v.astimezone(timezone.utc)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str = Field(default_factory=lambda: _id("fnd"),
                             description="Unique ID for this finding, auto-generated (fnd-<hex>).")
    claim: str = Field(description="Short machine-readable claim, e.g. 'high_latency' or "
                                    "'root_cause:hardware_failure'.")
    device: str | None = Field(default=None, description="The device this claim is about, if any.")
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="obs_ids and/or finding_ids this claim cites as support. What the sender puts "
                    "here is advisory only -- EvidenceStore.ground() re-resolves grounding from it.")
    grounding: Literal["observed", "inferred", "unsupported"] = Field(
        default="unsupported",
        description="observed: cites >=1 real obs_id. inferred: cites only already-grounded findings. "
                    "unsupported: everything else. Always set by EvidenceStore.ground(), never trusted "
                    "from the sender even if they set a different value here.")
    detail: str = Field(default="", description="Human-readable explanation of the claim.")


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    msg_id: str = Field(default_factory=lambda: _id("msg"),
                         description="Unique ID for this message, auto-generated (msg-<hex>). A reply "
                                     "reusing a msg_id already seen this incident is treated as malformed.")
    incident_id: str = Field(description="Identifies which incident this message belongs to.")
    schema_version: str = Field(default=SCHEMA_VERSION,
                                 description="Protocol version this message was built against. "
                                             "Rejected at construction time if not in SUPPORTED_VERSIONS.")
    sender: str = Field(description="Name of the sending agent/component, e.g. 'performance_agent'.")
    recipient: str = Field(description="Name of the intended recipient, e.g. 'orchestrator'.")
    type: MsgType = Field(description="What kind of message this is; see MsgType.")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
                                  description="UTC timestamp this Envelope was constructed.")
    findings: list[Finding] = Field(default_factory=list, description="Findings this message carries, if any.")
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Type-specific structured data, e.g. {'devices': [...]} for a task, "
                    "{'devices': {...ranking...}} for correlation findings, or a full Report dict for a report.")
    missing: list[str] = Field(
        default_factory=list,
        description="Evidence the sender knows it lacks, as '<device>:<metric>' strings. Distinct from "
                    "silently omitting a metric -- this says 'I looked, it wasn't there.'")
    assumptions: list[str] = Field(
        default_factory=list,
        description="Caveats the sender is explicitly flagging, e.g. 'scope limited to reporting device' "
                    "or a conflicting-readings note. Surfaced in the final report's limitations.")
    errors: list[str] = Field(default_factory=list, description="Error messages, for type=error envelopes.")

    @field_validator("schema_version")
    @classmethod
    def _supported(cls, v: str) -> str:
        if v not in SUPPORTED_VERSIONS:
            raise ValueError(f"unsupported schema_version {v!r}; supported: {sorted(SUPPORTED_VERSIONS)}")
        return v
