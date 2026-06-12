import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from liero_core.defaults import DEFAULT_MATERIALS, MATERIAL, PREFERRED_REPLACEMENT_INDICES
from liero_core.formats import default_palette
from liero_core.material import indices_for_material
from liero_core.quantizer import build_remap_lut, plan_quantization


def test_plan_reuses_close_existing_colors():
    base = default_palette().colors
    rock_idx = indices_for_material(MATERIAL['ROCK'])[0]
    pixels = [base[rock_idx]] * 50  # exactly an existing rock color
    palette, table, assignments = plan_quantization(
        {MATERIAL['ROCK']: pixels}, {MATERIAL['ROCK']: 1}, base)
    assert assignments[MATERIAL['ROCK']] == [rock_idx]
    assert palette[rock_idx] == base[rock_idx]
    assert table == DEFAULT_MATERIALS  # nothing reassigned


def test_plan_allocates_from_replacement_pool():
    base = default_palette().colors
    # a color far from any existing BG color
    pixels = [(0, 255, 255)] * 50
    palette, table, assignments = plan_quantization(
        {MATERIAL['BG']: pixels}, {MATERIAL['BG']: 1}, base)
    slot = assignments[MATERIAL['BG']][0]
    assert slot in PREFERRED_REPLACEMENT_INDICES
    assert palette[slot] == (0, 255, 255)
    assert table[slot] == MATERIAL['BG']


def test_plan_never_touches_worm():
    base = default_palette().colors
    worm_idxs = set(indices_for_material(MATERIAL['WORM']))
    pixels = [(i, i, i) for i in range(0, 250, 2)] * 5
    palette, table, assignments = plan_quantization(
        {MATERIAL['ROCK']: pixels}, {MATERIAL['ROCK']: 30}, base)
    assert not (set(assignments[MATERIAL['ROCK']]) & worm_idxs)
    for i in worm_idxs:
        assert palette[i] == base[i]


def test_plan_multiple_materials_no_slot_clash():
    base = default_palette().colors
    mp = {
        MATERIAL['ROCK']: [(10, 200, 10)] * 100,
        MATERIAL['DIRT']: [(200, 10, 200)] * 60,
    }
    counts = {MATERIAL['ROCK']: 2, MATERIAL['DIRT']: 2}
    palette, table, assignments = plan_quantization(mp, counts, base)
    rock = set(assignments[MATERIAL['ROCK']])
    dirt = set(assignments[MATERIAL['DIRT']])
    assert rock and dirt and not (rock & dirt)
    for i in rock:
        assert table[i] == MATERIAL['ROCK']


def test_plan_multi_material_selector_reuses_both():
    base = default_palette().colors
    dirt_idx = indices_for_material(MATERIAL['DIRT'])[0]
    dirt2_idx = indices_for_material(MATERIAL['DIRT_2'])[0]
    key = 'group:DIRTS'
    pixels = [base[dirt_idx]] * 30 + [base[dirt2_idx]] * 30
    palette, table, assignments = plan_quantization(
        {key: pixels}, {key: 2}, base,
        selectors={key: (MATERIAL['DIRT'], {MATERIAL['DIRT'], MATERIAL['DIRT_2']})})
    allowed = set(assignments[key])
    assert dirt_idx in allowed and dirt2_idx in allowed
    # reused slots keep their original material tags
    assert table[dirt_idx] == MATERIAL['DIRT']
    assert table[dirt2_idx] == MATERIAL['DIRT_2']


def test_plan_multi_material_new_slots_get_primary():
    base = default_palette().colors
    key = 'group:BGS'
    palette, table, assignments = plan_quantization(
        {key: [(0, 255, 255)] * 20}, {key: 1}, base,
        selectors={key: (MATERIAL['BG'], {MATERIAL['BG'], MATERIAL['BG_SEESHADOW']})})
    slot = assignments[key][0]
    assert slot in PREFERRED_REPLACEMENT_INDICES
    assert table[slot] == MATERIAL['BG']


def test_find_isolated_pixels():
    from liero_core.quantizer import find_isolated_pixels
    # 8x4 canvas: rock indices everywhere (19), one BG index (130) at (3,1),
    # and a 2x1 BG pair at (6,2)-(7,2) which is NOT isolated
    W, H = 8, 4
    idx = bytearray([19] * (W * H))
    idx[1 * W + 3] = 130
    idx[2 * W + 6] = 130
    idx[2 * W + 7] = 130
    bad = find_isolated_pixels(bytes(idx), W, H)
    assert bad == [(3, 1)]


def test_find_isolated_pixels_same_material_different_index():
    from liero_core.quantizer import find_isolated_pixels
    # two different ROCK indices touching: same material, not isolated
    W, H = 3, 1
    rocks = indices_for_material(MATERIAL['ROCK'])
    idx = bytes([rocks[0], rocks[1], rocks[0]])
    assert find_isolated_pixels(idx, W, H) == []


def test_build_remap_lut():
    base = default_palette().colors
    palette, table, assignments = plan_quantization(
        {MATERIAL['ROCK']: [(10, 200, 10), (12, 198, 12)] * 10},
        {MATERIAL['ROCK']: 1}, base)
    allowed = assignments[MATERIAL['ROCK']]
    lut = build_remap_lut([(10, 200, 10), (12, 198, 12)], palette, allowed)
    assert set(lut.values()) <= set(allowed)
    assert len(lut) == 2
