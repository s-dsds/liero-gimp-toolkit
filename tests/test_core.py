import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.defaults import DEFAULT_MATERIALS, MATERIAL, DEFAULT_COLOR_ANIM, DEFAULT_COLOR_ANIM_RANGES, ANIMATED_INDICES, expand_color_anim
from liero_core.material import classify_name, indices_for_material, index_info
from liero_core.quantizer import median_cut, choose_palette_slots, remap_pixels_to_indices


def test_defaults_len():
    assert len(DEFAULT_MATERIALS) == 256
    # classic colorAnim: 4 (from, to) range pairs as in LIERO.EXE / WebLiero classic mod.json
    assert DEFAULT_COLOR_ANIM == [129,131,133,136,152,159,168,171]
    assert DEFAULT_COLOR_ANIM_RANGES == [(129,131),(133,136),(152,159),(168,171)]
    assert len(ANIMATED_INDICES) == 3 + 4 + 8 + 4
    assert expand_color_anim([10, 12]) == [10, 11, 12]


def test_animated_index_info():
    assert index_info(130).animated is True   # inside 129-131 range
    assert index_info(132).animated is False  # between ranges
    assert index_info(155).animated is True


def test_classify():
    assert classify_name('rock #2') == MATERIAL['ROCK']
    assert classify_name('background dirt') == MATERIAL['BG_DIRT']
    assert classify_name('shoot-through detail') == MATERIAL['UNDEF']


def test_indices():
    assert 19 in indices_for_material(MATERIAL['ROCK'])
    assert index_info(188).preferred_replacement_candidate is True


def test_quantizer_basic():
    pixels = [(10,10,10), (12,10,11), (200,190,180), (201,190,181)]
    reps = median_cut(pixels, 2)
    assert len(reps) == 2
    base = [(0,0,0)] * 256
    allowed, newpal, table = choose_palette_slots(reps, base, MATERIAL['ROCK'])
    assert len(allowed) == 2
    out = remap_pixels_to_indices(pixels, newpal, allowed)
    assert all(i in allowed for i in out)
