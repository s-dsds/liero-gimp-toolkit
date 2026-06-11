import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.defaults import DEFAULT_MATERIALS, MATERIAL
from liero_core.formats import LEV_PIXELS, read_lev_pixels, read_wlsprt_sprites, wlsprt_sheet
from liero_core.material import (materials_from_entry_names, animated_from_entry_names,
                                 indices_to_anim_pairs)

SAMPLE_WLSPRT = Path("/home/qmdev/liero/wltools/wltools/bin/release/chickenfixed.wlsprt")
SAMPLE_LEV = Path("/home/qmdev/liero/maps/ladofdef.lev")


def test_lev_pixels(tmp_path):
    lev = tmp_path / "m.lev"
    lev.write_bytes(bytes(range(256)) * (LEV_PIXELS // 256 + 1))
    w, h, data = read_lev_pixels(lev)
    assert (w, h) == (504, 350)
    assert len(data) == LEV_PIXELS


def test_wlsprt_sprites_and_sheet(tmp_path):
    # two sprites: 2x2 and 3x1
    body = (2).to_bytes(2, "little")
    body += (2).to_bytes(2, "little") + (2).to_bytes(2, "little") + b"\0\0\0\0" + bytes([1, 2, 3, 4])
    body += (3).to_bytes(2, "little") + (1).to_bytes(2, "little") + b"\0\0\0\0" + bytes([5, 6, 7])
    f = tmp_path / "s.wlsprt"
    f.write_bytes(b"WLSPRT\x00\x00\x00" + body)
    sprites = read_wlsprt_sprites(f)
    assert [(w, h) for w, h, _ in sprites] == [(2, 2), (3, 1)]
    assert sprites[0][2] == bytes([1, 2, 3, 4])
    w, h, sheet = wlsprt_sheet(f, sheet_width=10)
    assert w == 10 and h >= 2
    assert sheet[0] == 1 and sheet[1] == 2 and sheet[10] == 3 and sheet[11] == 4
    assert sheet[3] == 5 and sheet[4] == 6 and sheet[5] == 7  # second sprite after 1px pad


def test_materials_from_entry_names():
    names = [f"{i:03d} {'ROCK' if DEFAULT_MATERIALS[i] == 4 else 'UNDEF'}" for i in range(256)]
    table = materials_from_entry_names(names)
    assert table is not None
    assert table[19] == MATERIAL["ROCK"]
    assert table[0] == MATERIAL["UNDEF"]


def test_materials_from_entry_names_rejects_foreign_palette():
    assert materials_from_entry_names(["Untitled"] * 256) is None


def test_animated_from_entry_names():
    names = [f"{i:03d} UNDEF" for i in range(256)]
    names[129] = "129 UNDEF ANIM"
    names[130] = "130 ROCK ANIM"
    assert animated_from_entry_names(names) == {129, 130}
    assert animated_from_entry_names([f"{i:03d} UNDEF" for i in range(256)]) is None
    # materials parsing is not confused by the ANIM token
    table = materials_from_entry_names(names)
    assert table[130] == MATERIAL["ROCK"]


def test_indices_to_anim_pairs():
    assert indices_to_anim_pairs({129, 130, 131, 133, 134, 135, 136}) == \
        [129, 131, 133, 136]
    assert indices_to_anim_pairs({5}) == [5, 5]
    assert indices_to_anim_pairs(set()) == []


@pytest.mark.skipif(not SAMPLE_WLSPRT.exists(), reason="wltools samples absent")
def test_real_wlsprt_sheet():
    w, h, sheet = wlsprt_sheet(SAMPLE_WLSPRT)
    assert w == 320 and h > 50
    assert len(sheet) == w * h


@pytest.mark.skipif(not SAMPLE_LEV.exists(), reason="sample lev absent")
def test_real_lev_pixels():
    w, h, data = read_lev_pixels(SAMPLE_LEV)
    assert len(data) == w * h
