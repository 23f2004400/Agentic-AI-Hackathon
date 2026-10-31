from datetime import datetime, timedelta, timezone
import pytest
from pydantic import ValidationError
from netcoop.protocol import Envelope, Finding, MsgType, Observation
from netcoop.evidence import EvidenceStore
from netcoop.units import normalize, UnitError

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc)

def obs(**kw):
    base = dict(device="r1", metric="latency", value=250.0, unit="ms", timestamp=T0, source="feedA", raw_ref="a.json#1")
    base.update(kw); return Observation(**base)

def test_unsupported_schema_version_rejected():
    with pytest.raises(ValidationError):
        Envelope(incident_id="i1", sender="x", recipient="y", type=MsgType.TASK, schema_version="9.9")

def test_unknown_field_rejected():
    with pytest.raises(ValidationError):
        Envelope(incident_id="i1", sender="x", recipient="y", type=MsgType.TASK, surprise=1)

def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        obs(timestamp=datetime(2026, 10, 31, 12, 0))

def test_duplicate_and_unknown_device_rejected():
    s = EvidenceStore(known_devices={"r1"})
    assert s.add_observation(obs())
    assert not s.add_observation(obs(raw_ref="a.json#2"))      # same reading again
    assert not s.add_observation(obs(device="ghost"))
    assert [r for _, r in s.rejected] == ["duplicate observation", "unknown device 'ghost'"]

def test_repeated_claim_does_not_become_evidence():
    s = EvidenceStore()
    o = obs(); s.add_observation(o)
    f1 = s.ground(Finding(claim="high_latency", evidence_refs=[o.obs_id]))
    rumor = s.ground(Finding(claim="link_down", evidence_refs=[], grounding="observed"))  # sender lies
    echo = s.ground(Finding(claim="link_down", evidence_refs=[rumor.finding_id]))
    built = s.ground(Finding(claim="congestion", evidence_refs=[f1.finding_id]))
    assert f1.grounding == "observed"
    assert rumor.grounding == "unsupported"
    assert echo.grounding == "unsupported"
    assert built.grounding == "inferred"
    assert s.trace(built.finding_id) == ["a.json#1"]

def test_staleness():
    s = EvidenceStore(max_age=timedelta(minutes=5)); o = obs(); s.add_observation(o)
    assert not s.is_stale(o.obs_id, T0 + timedelta(minutes=4))
    assert s.is_stale(o.obs_id, T0 + timedelta(minutes=6))

def test_units():
    assert normalize("latency", 0.25, "s") == (250.0, "ms")
    assert normalize("throughput", 500, "Kbps") == (0.5, "Mbps")
    assert normalize("packet_loss", 0.05, "ratio") == (5.0, "pct")
    for args in [("latency", 1, None), ("latency", 1, "furlongs"), ("packet_loss", 5, "ratio"), ("foo", 1, "ms")]:
        with pytest.raises(UnitError):
            normalize(*args)
