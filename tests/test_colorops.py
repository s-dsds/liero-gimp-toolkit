import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.colorops import (
    adjust_rgb,
    adjusted_palette,
    adjusted_palette_f,
    clamp8,
    gradient_palette_f,
    quantize,
    similar_color_indices,
    to_float,
    uniquify_palette,
)


def test_clamp8():
    assert clamp8(-3) == 0
    assert clamp8(300) == 255
    assert clamp8(127.6) == 128


def test_identity_transform():
    assert adjust_rgb((10, 200, 99)) == (10, 200, 99)


def test_brightness_contrast():
    assert adjust_rgb((100, 100, 100), brightness=20) == (120, 120, 120)
    # contrast pivots on 127.5: values below get darker, above get brighter
    r, g, b = adjust_rgb((100, 150, 200), contrast=2.0)
    assert r < 100 and g > 150 and b == 255


def test_hue_shift_full_circle():
    assert adjust_rgb((30, 60, 90), hue_degrees=360) == (30, 60, 90)


def test_hue_shift_changes_channel_order():
    # pure red shifted by 120 degrees becomes pure green
    assert adjust_rgb((255, 0, 0), hue_degrees=120) == (0, 255, 0)


def test_saturation_zero_is_gray():
    r, g, b = adjust_rgb((200, 50, 100), saturation=0.0)
    assert r == g == b


def test_adjusted_palette_respects_indices_and_locks():
    colors = [(50, 50, 50)] * 4
    out = adjusted_palette(colors, indices=[1, 2, 3], locked=[2], brightness=10)
    assert out[0] == (50, 50, 50)
    assert out[1] == (60, 60, 60)
    assert out[2] == (50, 50, 50)  # locked
    assert out[3] == (60, 60, 60)


def test_temperature_warms_grays():
    r, g, b = adjust_rgb((128, 128, 128), temperature=40)
    assert r > 128 and b < 128 and r - 128 > g - 128


def test_colorize_gives_grays_color():
    r, g, b = adjust_rgb((128, 128, 128), colorize=True, hue_degrees=0, saturation=0.8)
    assert not (r == g == b)
    # lightness preserved-ish
    assert abs((max(r, g, b) + min(r, g, b)) / 2 - 128) <= 2


def test_float_pipeline_no_flattening():
    # +10 then -10 brightness in float space returns exactly to start
    base = to_float([(100, 150, 200)])
    up = adjusted_palette_f(base, [0], brightness=10)
    down = adjusted_palette_f(up, [0], brightness=-10)
    assert quantize(down) == [(100, 150, 200)]


def test_gradient_palette():
    colors = to_float([(0, 0, 0), (90, 10, 10), (5, 5, 5), (60, 60, 60), (255, 255, 255)])
    out = quantize(gradient_palette_f(colors, [1, 2, 3]))
    assert out[1] == (90, 10, 10)              # start endpoint kept
    assert out[3] == (60, 60, 60)              # end endpoint kept
    assert out[2] == (75, 35, 35)              # midpoint interpolated
    assert out[0] == (0, 0, 0) and out[4] == (255, 255, 255)


def test_gradient_needs_three():
    colors = to_float([(0, 0, 0), (10, 10, 10)])
    assert gradient_palette_f(colors, [0, 1]) == colors


def test_similar_colors_hue_family():
    colors = [
        (40, 40, 200),    # 0 dark blue
        (120, 120, 255),  # 1 light blue (same hue family)
        (200, 40, 40),    # 2 red
        (128, 128, 128),  # 3 gray
        (60, 60, 90),     # 4 dull blue (still bluish)
        (8, 8, 12),       # 5 near-black with bluish tint -> excluded
        (250, 250, 255),  # 6 near-white -> excluded
    ]
    sel = similar_color_indices(colors, (40, 40, 200))
    assert 0 in sel and 1 in sel and 4 in sel
    assert 2 not in sel and 3 not in sel
    assert 5 not in sel and 6 not in sel


def test_similar_colors_grays():
    colors = [
        (128, 128, 128),  # 0 mid gray
        (150, 150, 150),  # 1 close gray
        (20, 20, 20),     # 2 near-black (lightness too far)
        (128, 128, 255),  # 3 saturated blue
    ]
    sel = similar_color_indices(colors, (128, 128, 128))
    assert sel == {0, 1}


def test_uniquify_palette():
    colors = [(10, 10, 10), (10, 10, 10), (10, 10, 10), (200, 0, 0)]
    out = uniquify_palette(colors)
    assert out[0] == (10, 10, 10)
    assert len(set(out)) == 4
    # nudges are minimal (Chebyshev distance 1 fits the first duplicates)
    assert max(abs(a - b) for a, b in zip(out[1], (10, 10, 10))) == 1
    assert out[3] == (200, 0, 0)


def test_uniquify_at_boundaries():
    out = uniquify_palette([(255, 255, 255)] * 3 + [(0, 0, 0)] * 2)
    assert len(set(out)) == 5
    assert all(0 <= v <= 255 for c in out for v in c)
