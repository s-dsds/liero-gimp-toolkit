import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.palette import Palette
from liero_core.formats import (
    LEV_PIXELS,
    POWERLEVEL_MAGIC,
    default_palette,
    load_palette,
    read_exe_color_anim,
    read_exe_materials,
    read_exe_palette,
    read_lev_palette,
    read_lpl,
    read_wlsprt_palette,
    write_lev_palette,
    write_lpl,
    write_wlsprt_palette,
)
from liero_core.defaults import DEFAULT_MATERIALS, DEFAULT_COLOR_ANIM_RANGES

LIERO_EXE = Path("/home/qmdev/liero/lierov133winxp/LIERO.EXE")
SAMPLE_WLSPRT = Path("/home/qmdev/liero/wltools/wltools/bin/release/chickenfixed.wlsprt")


def rainbow():
    return Palette("rainbow", [(i, 255 - i, (i * 3) % 256) for i in range(256)])


def test_default_palette():
    pal = default_palette()
    assert len(pal.colors) == 256
    assert pal.colors[1] == (108, 56, 0)  # classic dirt brown


def test_lpl_roundtrip(tmp_path):
    p = tmp_path / "x.lpl"
    write_lpl(p, rainbow())
    assert p.stat().st_size == 768
    assert read_lpl(p).colors == rainbow().colors


def test_lpl_six_bit_detection(tmp_path):
    p = tmp_path / "vga.lpl"
    p.write_bytes(bytes([63, 0, 32] * 256))
    pal = read_lpl(p)
    assert pal.colors[0] == (252, 0, 128)


def test_wlsprt_palette_roundtrip(tmp_path):
    sprites = (2).to_bytes(2, "little") + b"\x01\x00\x01\x00\x00\x00\x00\x00\x07"
    src = tmp_path / "a.wlsprt"
    src.write_bytes(b"WLSPRT\x00\x00\x00" + sprites)  # no palette block
    pal = read_wlsprt_palette(src)
    assert pal.colors == default_palette().colors  # falls back to default

    write_wlsprt_palette(src, rainbow())
    raw = src.read_bytes()
    assert raw[8] == 1
    assert read_wlsprt_palette(src).colors == rainbow().colors
    assert raw[9 + 768:] == sprites  # sprite data untouched

    # replacing an existing palette keeps the size stable
    write_wlsprt_palette(src, default_palette())
    assert src.read_bytes()[9 + 768:] == sprites


def test_wlsprt_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.wlsprt"
    bad.write_bytes(b"NOTSPRT" + b"\x00" * 100)
    with pytest.raises(ValueError):
        read_wlsprt_palette(bad)


def test_lev_powerlevel_roundtrip(tmp_path):
    lev = tmp_path / "map.lev"
    lev.write_bytes(bytes(LEV_PIXELS))
    assert read_lev_palette(lev).colors == default_palette().colors

    write_lev_palette(lev, rainbow())
    raw = lev.read_bytes()
    assert raw[LEV_PIXELS:LEV_PIXELS + 10] == POWERLEVEL_MAGIC
    got = read_lev_palette(lev)
    # 6-bit storage quantizes to multiples of 4
    expected = [(r >> 2 << 2, g >> 2 << 2, b >> 2 << 2) for r, g, b in rainbow().colors]
    assert got.colors == expected


def test_load_palette_dispatch(tmp_path):
    p = tmp_path / "x.lpl"
    write_lpl(p, rainbow())
    assert load_palette(p).colors == rainbow().colors
    gpl = tmp_path / "x.gpl"
    rainbow().to_gpl(gpl)
    assert load_palette(gpl).colors == rainbow().colors
    junk = tmp_path / "junk.xyz"
    junk.write_bytes(b"not a palette at all")
    with pytest.raises(ValueError):
        load_palette(junk)


# --- integration against real local files (skipped elsewhere) ---------------

needs_exe = pytest.mark.skipif(not LIERO_EXE.exists(), reason="local LIERO.EXE not present")


@needs_exe
def test_exe_palette_matches_bundled_default():
    assert read_exe_palette(LIERO_EXE).colors == default_palette().colors


@needs_exe
def test_exe_materials_match_defaults():
    assert read_exe_materials(LIERO_EXE) == DEFAULT_MATERIALS


@needs_exe
def test_exe_color_anim_matches_defaults():
    assert read_exe_color_anim(LIERO_EXE) == DEFAULT_COLOR_ANIM_RANGES


@pytest.mark.skipif(not SAMPLE_WLSPRT.exists(), reason="wltools samples not present")
def test_real_wlsprt_palette():
    pal = read_wlsprt_palette(SAMPLE_WLSPRT)
    assert len(pal.colors) == 256
