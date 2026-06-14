"""Tests for the OpenLiero MODERNLV writer/reader.

These pin the byte layout to what OpenLiero's own tools/lev_gen.py produces and
tools/lev_extract.py reads back (commit f0d49e3).
"""
import struct

import pytest

from liero_core import openliero as ol


def _rgba(pixels):
    """Flatten a list of (r,g,b,a) tuples to bytes."""
    out = bytearray()
    for px in pixels:
        out += bytes(px)
    return bytes(out)


def test_golden_byte_layout_no_ramps():
    # 2x1 level: index 12 then 19; px0 authored red, px1 transparent.
    data = ol.build_level(
        2, 1,
        material=bytes([12, 19]),
        display_rgba=_rgba([(255, 0, 0, 255), (0, 0, 0, 0)]),
    )
    expected = (
        b"OLLEVEL2" + b"\x00" + struct.pack("<H", 2) + struct.pack("<H", 1)  # header
        + b"\x0c\x13"                                                        # material
        + b"MODERNLV"
        + b"\x00\x00\xff\xff" + b"\x00\x00\x00\x00"                          # display_data (ARGB32 LE)
        + b"\x01\x00"                                                        # display_valid
        + b"\x00"                                                            # ramp_count = 0
    )
    assert data == expected


def test_legacy_size_has_no_header():
    cells = ol.LEGACY_W * ol.LEGACY_H
    data = ol.build_level(
        ol.LEGACY_W, ol.LEGACY_H,
        material=bytes(cells),
        display_rgba=bytes(cells * 4),  # all transparent
    )
    assert data[:8] != ol.SIZED_MAGIC          # headerless legacy file
    assert data[:8] == bytes(8)                # material starts immediately (index 0s)
    # material + MODERNLV magic + dd + dv + ramp_count
    assert len(data) == cells + 8 + cells * 4 + cells + 1
    got = ol.extract_level(data)
    assert (got["width"], got["height"]) == (ol.LEGACY_W, ol.LEGACY_H)


def test_roundtrip_display_ramps_anim():
    w, h = 4, 3
    cells = w * h
    material = bytes(range(cells))  # arbitrary distinct indices
    # px0 authored (10,20,30); everything else transparent at the display layer.
    disp = [(0, 0, 0, 0)] * cells
    disp[0] = (10, 20, 30, 255)
    # mixed-case hex on input to confirm the reader normalises to upper-case.
    ramps = [
        {"shift": 3, "colors": ["#1a3a6a", "#2A4A7A"]},
        {"shift": 1, "colors": ["#ff0000"]},
    ]
    anim = [(0, 0, 0, 0)] * cells
    anim[5] = (1, 7, 0, 255)     # ramp 1, phase 7
    anim[8] = (2, 200, 0, 255)   # ramp 2, phase 200

    data = ol.build_level(w, h, material, _rgba(disp), ramps, _rgba(anim))
    got = ol.extract_level(data)

    assert got["width"], got["height"] == (w, h)
    assert got["material"] == material

    dv = got["display_valid"]
    assert dv[0] == 1            # authored
    assert dv[5] == 1 and dv[8] == 1   # animated pixels are forced valid
    assert dv[1] == 0           # untouched

    # non-animated authored pixel keeps its colour
    argb0 = struct.unpack_from("<I", got["display_data"], 0)[0]
    assert ((argb0 >> 16) & 0xFF, (argb0 >> 8) & 0xFF, argb0 & 0xFF) == (10, 20, 30)

    # ramps round-trip, upper-cased
    assert got["ramps"] == [
        {"shift": 3, "colors": ["#1A3A6A", "#2A4A7A"]},
        {"shift": 1, "colors": ["#FF0000"]},
    ]

    # display_anim holds ramp indices; phase is packed into display_data low byte
    da = got["display_anim"]
    assert da[5] == 1 and da[8] == 2
    assert da[0] == 0
    assert struct.unpack_from("<I", got["display_data"], 5 * 4)[0] & 0xFF == 7
    assert struct.unpack_from("<I", got["display_data"], 8 * 4)[0] & 0xFF == 200


def test_anim_requires_ramps():
    with pytest.raises(ValueError, match="anim requires ramps"):
        ol.build_level(2, 1, bytes(2), bytes(8), ramps=None, anim_rgba=bytes(8))


def test_anim_ramp_index_out_of_range():
    ramps = [{"shift": 0, "colors": ["#000000"]}]  # only ramp 1 exists
    anim = _rgba([(2, 0, 0, 255), (0, 0, 0, 0)])    # references ramp 2
    with pytest.raises(ValueError, match="exceeds ramp count"):
        ol.build_level(2, 1, bytes(2), bytes(8), ramps=ramps, anim_rgba=anim)


def _pal_with(idx_colors):
    pal = bytearray(256 * 3)
    for idx, (r, g, b) in idx_colors.items():
        pal[idx * 3:idx * 3 + 3] = bytes((r, g, b))
    return bytes(pal)


def test_render_frame_animation_matches_engine():
    # 3x1: px0 static authored, px1 palette fallback, px2 animated.
    w, h = 3, 1
    disp = [(50, 60, 70, 255), (0, 0, 0, 0), (0, 0, 0, 0)]
    ramps = [{"shift": 0, "colors": ["#FF0000", "#00FF00", "#0000FF"]}]  # R,G,B
    anim = [(0, 0, 0, 0), (0, 0, 0, 0), (1, 1, 0, 255)]  # px2: ramp 1, phase offset 1
    data = ol.build_level(w, h, bytes([12, 19, 168]), _rgba(disp), ramps, _rgba(anim))
    level = ol.extract_level(data)
    pal = _pal_with({19: (11, 22, 33)})

    def px(buf, i):
        return (buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2])

    f0 = ol.render_frame_rgb(level, pal, cycles=0)
    f1 = ol.render_frame_rgb(level, pal, cycles=1)
    f2 = ol.render_frame_rgb(level, pal, cycles=2)
    # static authored pixel never changes
    assert px(f0, 0) == px(f1, 0) == (50, 60, 70)
    # palette fallback uses pal[material_id]
    assert px(f0, 1) == (11, 22, 33)
    # animated: cols[(phase_offset 1 + cycles) % 3]
    assert px(f0, 2) == (0, 255, 0)   # cols[1] green
    assert px(f1, 2) == (0, 0, 255)   # cols[2] blue
    assert px(f2, 2) == (255, 0, 0)   # cols[0] red (wrapped)


def test_render_frozen_when_shift_ge_32():
    w, h = 1, 1
    ramps = [{"shift": 32, "colors": ["#010203", "#0A0B0C"]}]
    anim = [(1, 0, 0, 255)]  # ramp 1, phase 0
    data = ol.build_level(w, h, bytes([0]), _rgba([(9, 9, 9, 255)]), ramps, _rgba(anim))
    level = ol.extract_level(data)
    pal = bytes(256 * 3)
    a = ol.render_frame_rgb(level, pal, cycles=0)
    b = ol.render_frame_rgb(level, pal, cycles=10_000)
    assert a == b  # shift>=32 => frozen, no UB
    assert (a[0], a[1], a[2]) == (1, 2, 3)  # cols[0]


def test_material_mask_colors():
    assert ol.material_flag_color(1) == (139, 69, 19)    # dirt
    assert ol.material_flag_color(4) == (120, 130, 145)  # rock
    assert ol.material_flag_color(32) == (255, 235, 60)  # worm
    assert ol.material_flag_color(0) == (60, 60, 60)     # solid, no flags
    assert ol.material_flag_color(24) == (90, 120, 170)  # bg+see-shadow -> see-shadow tint
    mask = ol.material_mask_rgb(bytes([12, 19, 30, 0]))
    assert mask[0:3] == bytes((139, 69, 19))   # 12 dirt
    assert mask[3:6] == bytes((120, 130, 145)) # 19 rock
    assert mask[6:9] == bytes((255, 235, 60))  # 30 worm
    assert mask[9:12] == bytes((60, 60, 60))   # 0 solid


def test_canonical_index_for_material():
    from liero_core.defaults import MATERIAL
    assert ol.canonical_index_for_material(MATERIAL["DIRT"]) == 12
    assert ol.canonical_index_for_material(MATERIAL["ROCK"]) == 19
    assert ol.canonical_index_for_material(MATERIAL["WORM"]) == 30
    assert ol.canonical_index_for_material(MATERIAL["BG_SEESHADOW"]) == 160  # open space


def test_compose_material_mask_topmost_wins():
    # 2x1, layers TOP-to-BOTTOM: A covers px0 (rock 19), B covers both (dirt 12).
    cov_a = bytes([1, 0])
    cov_b = bytes([1, 1])
    mask = ol.compose_material_mask(2, 1, [(cov_a, 19), (cov_b, 12)], default_index=160)
    assert mask == bytes([19, 12])         # topmost A wins px0; B fills px1
    # uncovered -> default
    mask2 = ol.compose_material_mask(2, 1, [(bytes([1, 0]), 4)], default_index=160)
    assert mask2 == bytes([4, 160])


def test_build_anim_rgba_from_layers():
    # 3x1: top layer covers px0 not animated (blocks); bottom covers px0,1 ramp 2.
    top = bytes([1, 0, 0])
    bottom = bytes([1, 1, 0])
    anim = ol.build_anim_rgba(3, 1, [(top, 0), (bottom, 2)], default_phase=5)
    # px0 claimed by non-animated top -> stays 0
    assert anim[0:4] == bytes([0, 0, 0, 0])
    # px1 animated by bottom -> R=ramp2, G=phase5, A=255
    assert anim[4:8] == bytes([2, 5, 0, 255])
    # px2 uncovered
    assert anim[8:12] == bytes([0, 0, 0, 0])


def test_ramps_json_roundtrip(tmp_path):
    ramps = [{"shift": 3, "colors": ["#1A3A6A", "#2A4A7A"]}]
    p = tmp_path / "ramps.json"
    ol.save_ramps_json(p, ramps)
    assert ol.load_ramps_json(p) == ramps
    import pytest as _pt
    with _pt.raises(ValueError):
        ol.save_ramps_json(p, [{"shift": 0, "colors": []}])  # empty colors invalid


def test_size_bounds_enforced():
    with pytest.raises(ValueError, match="outside"):
        ol.build_level(0, 10, b"", b"")
    with pytest.raises(ValueError, match="outside"):
        ol.build_level(ol.MAX_DIM + 1, 1, bytes(ol.MAX_DIM + 1), bytes((ol.MAX_DIM + 1) * 4))


def test_incremental_anim_matches_full_render():
    w, h = 3, 1
    disp = [(50, 60, 70, 255), (0, 0, 0, 0), (0, 0, 0, 0)]
    ramps = [{"shift": 0, "colors": ["#FF0000", "#00FF00", "#0000FF"]}]
    anim = [(0, 0, 0, 0), (0, 0, 0, 0), (1, 1, 0, 255)]
    data = ol.build_level(w, h, bytes([12, 19, 168]), _rgba(disp), ramps, _rgba(anim))
    level = ol.extract_level(data)
    pal = _pal_with({19: (11, 22, 33)})
    base = ol.render_frame_rgb(level, pal, 0)
    cells = ol.animation_cells(level)
    assert len(cells) == 1
    for cyc in (0, 1, 2, 5, 17):
        assert ol.render_anim_frame(base, cells, cyc) == ol.render_frame_rgb(level, pal, cyc)


def test_ordered_unique_colors():
    rgba = _rgba([(10, 10, 10, 255), (200, 200, 200, 255), (99, 99, 99, 0)])
    assert ol.ordered_unique_colors(rgba) == ['#0A0A0A', '#C8C8C8']  # luminance order, transparent skipped
    assert ol.ordered_unique_colors(_rgba([(0, 0, 0, 0)])) == []


def test_build_anim_phase_modes():
    cov = bytes([1, 1])  # one layer covers both px of a 2x1
    ramps = [{"shift": 0, "colors": ["#000000", "#FFFFFF"]}]
    disp = _rgba([(0, 0, 0, 255), (255, 255, 255, 255)])
    # 'color': px0 black -> ramp index 0, px1 white -> ramp index 1
    a = ol.build_anim_rgba(2, 1, [(cov, 1)], phase_mode='color', display_rgba=disp, ramps=ramps)
    assert a[1] == 0 and a[5] == 1
    # 'sync': default_phase everywhere
    a2 = ol.build_anim_rgba(2, 1, [(cov, 1)], default_phase=7, phase_mode='sync')
    assert a2[1] == 7 and a2[5] == 7
    # 'wave': phase = x + y
    a3 = ol.build_anim_rgba(2, 1, [(cov, 1)], phase_mode='wave')
    assert a3[1] == 0 and a3[5] == 1
    # 'random': deterministic hash
    a4 = ol.build_anim_rgba(2, 1, [(cov, 1)], phase_mode='random')
    assert a4[1] == (0 * 2654435761) & 0xFF and a4[5] == (1 * 2654435761) & 0xFF
    # every mode marks both px animated (alpha 255, ramp 1)
    for buf in (a, a2, a3, a4):
        assert buf[0] == 1 and buf[3] == 255 and buf[4] == 1 and buf[7] == 255


def test_powerlevel_palette_roundtrip():
    pal = [(0, 0, 0)] * 256
    pal[168] = (16, 32, 48); pal[171] = (200, 210, 220)
    data = ol.build_level(2, 1, bytes([168, 169]), _rgba([(0, 0, 0, 0), (0, 0, 0, 0)]), palette=pal)
    assert b'POWERLEVEL' in data and data.index(b'POWERLEVEL') < data.index(b'MODERNLV')
    lvl = ol.extract_level(data)
    assert lvl['palette'][168] == (16, 32, 48)        # already on the 6-bit grid
    assert lvl['palette'][171] == (200, 208, 220)     # 210 -> 6bit -> 208


def test_quantize_band_colors():
    cols = [(0, 0, 0), (255, 255, 255), (0, 0, 0), (100, 100, 100)]
    assert ol.quantize_band_colors(cols, 4) == [(0, 0, 0), (100, 100, 100), (255, 255, 255)]
    assert len(ol.quantize_band_colors(cols, 2)) == 2


def test_fit_palette_anim_band():
    cov = bytes([1, 0, 1])
    disp = _rgba([(0, 0, 0, 255), (0, 0, 0, 0), (255, 255, 255, 255)])
    reps, idx = ol.fit_palette_anim_band(cov, disp, lo=168, hi=171)
    assert len(reps) == 4              # padded to band width
    assert idx[0] == 168 and idx[2] == 169 and idx[1] == 0


def test_palette_anim_cycling_matches_engine():
    w, h = 4, 1
    mat = bytes([168, 169, 170, 171])
    pal = [(0, 0, 0)] * 256
    for k, v in enumerate((8, 20, 28, 40)):
        pal[168 + k] = (v, 0, 0)
    data = ol.build_level(w, h, mat, _rgba([(0, 0, 0, 0)] * 4), palette=pal)
    lvl = ol.extract_level(data)
    palrgb = bytearray(256 * 3)
    for i, (r, g, b) in enumerate(lvl['palette']):
        palrgb[i * 3:i * 3 + 3] = bytes((r, g, b))
    pcells = ol.palette_anim_cells(lvl)
    assert len(pcells) == 4
    base = ol.render_frame_rgb(lvl, bytes(palrgb), 0)
    assert base[0] == 8                                  # px0 idx168 -> pal[168]
    assert ol.render_frame_incremental(base, [], pcells, bytes(palrgb), 0) == base
    f8 = ol.render_frame_incremental(base, [], pcells, bytes(palrgb), 8)  # dist=1
    assert f8[0] == 40                                   # px0 now shows pal[171]
    assert f8[9] == 28                                   # px3 now shows pal[170]


def test_per_ramp_phase_overrides_global():
    r_sync = [{"shift": 0, "colors": ["#000000"], "phase": "sync"}]
    r_rand = [{"shift": 0, "colors": ["#000000"], "phase": "random"}]
    a_sync = ol.build_anim_rgba(1, 1, [(bytes([1]), 1)], default_phase=7, ramps=r_sync)
    a_rand = ol.build_anim_rgba(1, 1, [(bytes([1]), 1)], default_phase=7, ramps=r_rand)
    assert a_sync[1] == 7                              # sync -> default phase
    assert a_rand[1] == (0 * 2654435761) & 0xFF        # random hash of px 0


def test_band_ops():
    a = bytes([1, 0, 1, 0])
    b = bytes([1, 1, 0, 0])
    assert ol.band_and(a, b) == bytes([1, 0, 0, 0])
    assert ol.band_or(a, b) == bytes([1, 1, 1, 0])
    # band_select: a where mask!=0 else b
    mask = bytes([1, 0, 1, 0])
    x = bytes([10, 20, 30, 40])
    y = bytes([50, 60, 70, 80])
    assert ol.band_select(mask, x, y) == bytes([10, 60, 30, 80])
