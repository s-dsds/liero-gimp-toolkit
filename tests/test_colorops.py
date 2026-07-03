import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.colorops import (
    adjust_rgb,
    adjusted_palette,
    adjusted_palette_f,
    clamp8,
    gradient_palette_f,
    oklab_to_oklch,
    oklab_to_srgb,
    quantize,
    retarget_hue_f,
    selection_mean_hue,
    similar_color_indices,
    srgb_to_oklab,
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


def test_similar_colors_ramp_and_duplicates():
    # mirror of the classic palette case: a tan ramp, its duplicate, a
    # parallel brown ramp, plus junk that must stay out
    colors = [(0, 0, 0)] * 16
    colors[1:5] = [(120, 72, 52), (156, 120, 88), (196, 168, 124), (236, 216, 160)]  # ramp
    colors[5:8] = [(156, 120, 88), (196, 168, 124), (236, 216, 160)]  # duplicate ramp
    colors[8] = (124, 84, 48)     # parallel muted brown -> in
    colors[9] = (252, 84, 84)     # vivid red -> out (hue + saturation)
    colors[10] = (200, 100, 0)    # vivid orange -> out (saturation gap)
    colors[11] = (128, 128, 128)  # gray -> out
    sel = similar_color_indices(colors, 1)
    assert {1, 2, 3, 4, 5, 6, 7, 8} <= sel
    assert not ({9, 10, 11} & sel)


def test_similar_colors_grays():
    colors = [
        (128, 128, 128),  # 0 mid gray
        (150, 150, 150),  # 1 close gray
        (20, 20, 20),     # 2 near-black (lightness too far)
        (128, 128, 255),  # 3 saturated blue
    ]
    sel = similar_color_indices(colors, 0)
    assert sel == {0, 1}


def test_detect_ramp_breaks_on_hue_jump():
    from liero_core.colorops import detect_ramp
    colors = [(148, 176, 0),   # olive (different hue family)
              (120, 72, 52), (156, 120, 88), (196, 168, 124),  # tan ramp
              (20, 200, 20)]   # green jump
    assert detect_ramp(colors, 2) == [1, 2, 3]


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


# --- OKLab / retexture -----------------------------------------------------

def test_oklab_roundtrip():
    for c in [(0, 0, 0), (255, 255, 255), (120, 72, 52), (76, 76, 76),
              (168, 84, 24), (80, 104, 248)]:
        back = oklab_to_srgb(srgb_to_oklab(c))
        assert all(abs(a - b) < 0.5 for a, b in zip(c, back))


def test_oklab_lightness_monotonic():
    # OKLab L increases with a brighter gray
    assert srgb_to_oklab((60, 60, 60))[0] < srgb_to_oklab((160, 160, 160))[0]


def test_gradient_oklab_keeps_endpoints():
    colors = to_float([(20, 20, 20), (255, 240, 0)])
    colors = colors + to_float([(255, 255, 255)])  # 3 entries
    out = gradient_palette_f(colors, [0, 1, 2], space='oklab')
    # endpoints identical to rgb interpolation, midpoint differs
    assert quantize([out[0]])[0] == (20, 20, 20)
    assert quantize([out[2]])[0] == (255, 255, 255)
    rgb_mid = quantize([gradient_palette_f(colors, [0, 1, 2])[1]])[0]
    assert quantize([out[1]])[0] != rgb_mid


def test_retarget_hue_moves_ramp_to_target():
    # a brown ramp retargeted to ~240 deg (blue) lands its mean hue near target
    ramp = to_float([(120, 72, 52), (156, 120, 88), (196, 168, 124)])
    out = retarget_hue_f(ramp, [0, 1, 2], target_hue=240.0)
    hues = [oklab_to_oklch(srgb_to_oklab(c))[2] for c in out]
    # circular mean near 240
    import math
    mx = sum(math.cos(math.radians(h)) for h in hues)
    my = sum(math.sin(math.radians(h)) for h in hues)
    mean = math.degrees(math.atan2(my, mx)) % 360.0
    assert abs(((mean - 240.0) + 180) % 360 - 180) < 8


def test_retarget_preserves_lightness():
    ramp = to_float([(120, 72, 52), (156, 120, 88), (196, 168, 124)])
    before_L = [srgb_to_oklab(c)[0] for c in ramp]
    out = retarget_hue_f(ramp, [0, 1, 2], target_hue=200.0, coherence=1.0)
    after_L = [srgb_to_oklab(c)[0] for c in out]
    assert all(abs(a - b) < 1e-6 for a, b in zip(before_L, after_L))


def test_retarget_coherence_collapses_spread():
    ramp = to_float([(180, 40, 40), (40, 180, 40), (40, 40, 180)])  # wild hues
    out = retarget_hue_f(ramp, [0, 1, 2], target_hue=120.0, coherence=1.0)
    hues = [oklab_to_oklch(srgb_to_oklab(c))[2] for c in out]
    assert all(abs(((h - 120.0) + 180) % 360 - 180) < 1.0 for h in hues)


def test_retarget_gray_needs_tint():
    gray = to_float([(76, 76, 76), (116, 116, 116), (156, 156, 156)])
    # without tint a gray ramp is unchanged (no hue to rotate)
    same = quantize(retarget_hue_f(gray, [0, 1, 2], target_hue=30.0))
    assert same == quantize(gray)
    # with tint it gains chroma toward the target hue
    tinted = retarget_hue_f(gray, [0, 1, 2], target_hue=30.0, tint=1.0)
    assert any(max(c) - min(c) > 3 for c in quantize(tinted))


def test_retarget_respects_locked():
    ramp = to_float([(120, 72, 52), (156, 120, 88), (196, 168, 124)])
    out = retarget_hue_f(ramp, [0, 1, 2], target_hue=240.0, locked=[1])
    assert quantize([out[1]])[0] == (156, 120, 88)


def test_selection_mean_hue():
    ramp = to_float([(120, 72, 52), (156, 120, 88), (196, 168, 124)])
    mean = selection_mean_hue(ramp, [0, 1, 2])
    # brown/tan family sits around 60-70 deg in OKLCh
    assert mean is not None and 40 < mean < 90
    # all-gray selection has no hue
    assert selection_mean_hue(to_float([(80, 80, 80)] * 3), [0, 1, 2]) is None
