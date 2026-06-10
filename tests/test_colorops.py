import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.colorops import adjust_rgb, adjusted_palette, clamp8


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
