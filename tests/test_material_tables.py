import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.defaults import DEFAULT_MATERIALS, MATERIAL
from liero_core.material import (index_info, load_material_table, material_table_to_js,
                                 parse_material_text, animated_from_entry_names,
                                 indices_to_anim_pairs)

ROOM_MATERIAL_INIT = Path("/home/qmdev/liero/dock/room/_material_init.js")


def test_load_json_table(tmp_path):
    p = tmp_path / "mats.json"
    p.write_text(json.dumps(DEFAULT_MATERIALS))
    assert load_material_table(p) == DEFAULT_MATERIALS


def test_load_js_snippet(tmp_path):
    p = tmp_path / "paste.js"
    arr = ",".join(str(v) for v in DEFAULT_MATERIALS)
    p.write_text(f"// from mapsettings.js\nmaterials: [{arr}],\n")
    assert load_material_table(p) == DEFAULT_MATERIALS


def test_load_rejects_bad_values(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps([7] * 256))  # 7 is not a known material bitmask
    with pytest.raises(ValueError):
        load_material_table(p)


def test_custom_table_changes_info():
    table = list(DEFAULT_MATERIALS)
    table[200] = MATERIAL["WORM"]
    info = index_info(200, table)
    assert info.material_name == "WORM"
    assert info.protected is True  # worm indices of the custom table are protected
    assert index_info(200).protected is False  # ...but not in the default table


def test_js_expression_no_undef():
    table = [MATERIAL["ROCK"] if m == MATERIAL["UNDEF"] else m for m in DEFAULT_MATERIALS]
    assert material_table_to_js(table) == "defaultMaterials.map(noUndef)"


def test_js_expression_replace():
    table = list(DEFAULT_MATERIALS)
    for i in (141, 142, 155):
        table[i] = MATERIAL["ROCK"]
    expr = material_table_to_js(table)
    assert expr == "defaultMaterials.map(replaceMatIndexBy(MATERIAL.ROCK,141,142,155))"


def test_js_expression_fallback_literal():
    table = [MATERIAL["DIRT"]] * 256
    expr = material_table_to_js(table)
    assert expr.startswith("[") and expr.endswith("]")
    assert load_material_table_from_text(expr) == table


def load_material_table_from_text(text):
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(text)
        name = f.name
    try:
        return load_material_table(name)
    finally:
        Path(name).unlink()


@pytest.mark.skipif(not ROOM_MATERIAL_INIT.exists(), reason="room script not present")
def test_room_script_default_materials_match():
    assert load_material_table(ROOM_MATERIAL_INIT) == DEFAULT_MATERIALS


# --- room-script expression parsing ------------------------------------------

def test_parse_expression_no_undef():
    table = parse_material_text("materials: defaultMaterials.map(noUndef),")
    undef_idxs = [i for i, m in enumerate(DEFAULT_MATERIALS) if m == MATERIAL["UNDEF"]]
    assert all(table[i] == MATERIAL["ROCK"] for i in undef_idxs)


def test_parse_expression_chain_with_range():
    text = ("materials: defaultMaterials.map(noUndef)"
            ".map(replaceMatIndexBy(MATERIAL.BG,..._range(189,208))),")
    table = parse_material_text(text)
    assert all(table[i] == MATERIAL["BG"] for i in range(189, 209))
    assert table[188] == MATERIAL["ROCK"]  # noUndef applied, not in range


def test_parse_expression_numeric_material():
    table = parse_material_text("defaultMaterials.map(replaceMatIndexBy(4,141,142,155))")
    assert table[141] == table[155] == MATERIAL["ROCK"]


def test_parse_expression_rejects_unknown_helper():
    with pytest.raises(ValueError):
        parse_material_text("defaultMaterials.map(randomizeEverything)")


def test_js_roundtrip_with_range_folding():
    table = list(DEFAULT_MATERIALS)
    for i in range(189, 209):
        table[i] = MATERIAL["BG"]
    table[5] = MATERIAL["DIRT"]
    expr = material_table_to_js(table)
    assert "..._range(189,208)" in expr
    assert parse_material_text(expr) == table
