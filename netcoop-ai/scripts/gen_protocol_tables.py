#!/usr/bin/env python
"""Generates the Envelope/Observation field tables in docs/PROTOCOL.md from
Envelope.model_json_schema() / Observation.model_json_schema(), so the docs
can't silently drift from the actual protocol: if a field is renamed, added,
removed, or its description changes, re-running this script regenerates the
tables to match.

This only renders markdown table fragments to stdout (or, with --write,
replaces the generated block in docs/PROTOCOL.md between the
<!-- GENERATED:X --> / <!-- END GENERATED:X --> markers) -- it does not
author the surrounding prose.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from netcoop.protocol import Envelope, Observation  # noqa: E402

PROTOCOL_MD = ROOT / "docs" / "PROTOCOL.md"

# Fields whose "unit" is meaningful telemetry metadata, not protocol plumbing.
UNIT_FIELDS = {"unit", "original_unit", "value"}


def _type_str(prop: dict) -> str:
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in prop:
        parts = []
        for sub in prop["anyOf"]:
            if sub.get("type") == "null":
                parts.append("null")
            elif "$ref" in sub:
                parts.append(sub["$ref"].rsplit("/", 1)[-1])
            elif sub.get("type") == "array":
                parts.append(f"{_type_str(sub.get('items', {}))}[]")
            else:
                parts.append(sub.get("type", "any"))
        return " / ".join(parts)  # not " | ": an unescaped pipe would break the markdown table cell
    if prop.get("type") == "array":
        return f"{_type_str(prop.get('items', {}))}[]"
    if prop.get("format") == "date-time":
        return "datetime"
    return prop.get("type", "any")


def render_table(model) -> str:
    schema = model.model_json_schema()
    required = set(schema.get("required", []))
    lines = ["| Field | Type | Unit | Required | Meaning |", "|---|---|---|---|---|"]
    for name, prop in schema["properties"].items():
        type_str = _type_str(prop)
        meaning = prop.get("description", "").replace("\n", " ")
        unit_note = "value's own unit" if name in UNIT_FIELDS else "—"
        req = "yes" if name in required else "no"
        lines.append(f"| `{name}` | `{type_str}` | {unit_note} | {req} | {meaning} |")
    return "\n".join(lines)


def render_all() -> str:
    return (
        "<!-- GENERATED:ENVELOPE_FIELDS -->\n"
        "#### Envelope fields\n\n" + render_table(Envelope) + "\n"
        "<!-- END GENERATED:ENVELOPE_FIELDS -->\n\n"
        "<!-- GENERATED:OBSERVATION_FIELDS -->\n"
        "#### Observation fields\n\n" + render_table(Observation) + "\n"
        "<!-- END GENERATED:OBSERVATION_FIELDS -->"
    )


def write_into_protocol_md() -> None:
    text = PROTOCOL_MD.read_text(encoding="utf-8")
    for block_name, model in (("ENVELOPE_FIELDS", Envelope), ("OBSERVATION_FIELDS", Observation)):
        title = "Envelope fields" if model is Envelope else "Observation fields"
        replacement = (
            f"<!-- GENERATED:{block_name} -->\n#### {title}\n\n{render_table(model)}\n"
            f"<!-- END GENERATED:{block_name} -->"
        )
        pattern = re.compile(
            rf"<!-- GENERATED:{block_name} -->.*?<!-- END GENERATED:{block_name} -->", re.DOTALL,
        )
        if not pattern.search(text):
            raise SystemExit(f"Markers for {block_name} not found in {PROTOCOL_MD}")
        text = pattern.sub(replacement, text)
    PROTOCOL_MD.write_text(text, encoding="utf-8")
    print(f"Regenerated field tables in {PROTOCOL_MD}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write_into_protocol_md()
    else:
        print(render_all())
