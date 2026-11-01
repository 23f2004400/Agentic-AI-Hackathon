import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL_MD = ROOT / "docs" / "PROTOCOL.md"


def _gen_protocol_tables():
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import gen_protocol_tables
    return gen_protocol_tables


# ---- scripts/gen_protocol_tables.py -----------------------------------------

def test_render_table_covers_every_model_field():
    gpt = _gen_protocol_tables()
    from netcoop.protocol import Envelope, Observation

    for model in (Envelope, Observation):
        table = gpt.render_table(model)
        for field_name in model.model_fields:
            assert f"`{field_name}`" in table, f"{field_name} missing from generated table"

def test_render_table_marks_required_fields_correctly():
    gpt = _gen_protocol_tables()
    from netcoop.protocol import Observation
    table = gpt.render_table(Observation)
    lines = {line.split("|")[1].strip(): line for line in table.splitlines() if line.startswith("| `")}
    assert "| yes |" in lines["`device`"]       # required
    assert "| no |" in lines["`original_unit`"]  # has a default

def test_render_table_never_leaves_an_unescaped_pipe_inside_a_cell():
    # regression test: a union type like "number | string | boolean" joined with
    # " | " would silently break the markdown table by splitting into extra cells.
    gpt = _gen_protocol_tables()
    from netcoop.protocol import Observation
    table = gpt.render_table(Observation)
    for line in table.splitlines()[2:]:  # skip header + separator row
        if not line.strip():
            continue
        cells = line.strip().strip("|").split("|")
        assert len(cells) == 5, f"row has {len(cells)} cells, expected 5: {line!r}"

def test_write_into_protocol_md_is_idempotent_and_matches_live_schema():
    gpt = _gen_protocol_tables()
    from netcoop.protocol import Envelope, Observation

    before = PROTOCOL_MD.read_text(encoding="utf-8")
    try:
        gpt.write_into_protocol_md()
        after = PROTOCOL_MD.read_text(encoding="utf-8")
        assert after == before  # already up to date -> no-op
        assert gpt.render_table(Envelope) in after
        assert gpt.render_table(Observation) in after
    finally:
        PROTOCOL_MD.write_text(before, encoding="utf-8")

def test_protocol_md_has_no_raw_pipe_inside_generated_table_rows():
    # end-to-end check on the actual committed doc, not just the generator's output
    text = PROTOCOL_MD.read_text(encoding="utf-8")
    start = text.index("<!-- GENERATED:OBSERVATION_FIELDS -->")
    end = text.index("<!-- END GENERATED:OBSERVATION_FIELDS -->")
    block = text[start:end]
    for line in block.splitlines():
        if line.startswith("| `"):
            assert len(line.strip().strip("|").split("|")) == 5
