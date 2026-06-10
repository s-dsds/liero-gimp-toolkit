"""Classic Liero/WebLiero default material metadata.

MATERIAL.UNDEF is a valid shoot-through material, not an unused slot.
The preferred replacement candidate pool is tracked separately.

Note: the material list provided in the project discussion contained 264 values;
this module uses the first 256 palette entries. The extra values were trailing
zeros and do not affect indices 0-255.
"""

MATERIAL = {
    "UNDEF": 0,
    "DIRT": 1,
    "DIRT_2": 2,
    "ROCK": 4,
    "BG": 8,
    "BG_DIRT": 9,
    "BG_DIRT_2": 10,
    "BG_SEESHADOW": 24,
    "WORM": 32,
}

MATERIAL_NAMES = {v: k for k, v in MATERIAL.items()}

# Verified byte-for-byte against both LIERO.EXE 1.33 (bitplanes at 0x1C2E0 /
# 0x1AEA8) and wgetch's WebLiero material reference; the two agree. The
# originally transcribed table had the 160-171 and 176-184 blocks shifted by 4.
DEFAULT_MATERIALS = [
    0, 9, 10, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1,
    1, 1, 1, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 32, 32,
    32, 32, 32, 32, 32, 32, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 4, 4, 4, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 9, 9, 9,
    0, 0, 1, 1, 1, 4, 4, 4, 1, 1, 1, 4, 4, 4, 2, 2,
    2, 2, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 4, 4, 4, 0, 0,
    0, 0, 8, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    24, 24, 24, 24, 8, 8, 8, 8, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]

# colorAnim is a flat list of (from, to) PAIRS, not individual indices: each
# consecutive pair delimits an inclusive range of animated palette entries.
# Values confirmed against LIERO.EXE 1.33 (offset 0x1AF0C) and WebLiero's
# classic mod.json. 4 ranges -> 19 animated indices.
DEFAULT_COLOR_ANIM = [129, 131, 133, 136, 152, 159, 168, 171]

DEFAULT_COLOR_ANIM_RANGES = list(zip(DEFAULT_COLOR_ANIM[0::2], DEFAULT_COLOR_ANIM[1::2]))


def expand_color_anim(flat_pairs):
    """Expand a WebLiero-style colorAnim pair list into sorted indices."""
    ranges = zip(flat_pairs[0::2], flat_pairs[1::2])
    return sorted({i for a, b in ranges for i in range(a, b + 1)})


ANIMATED_INDICES = expand_color_anim(DEFAULT_COLOR_ANIM)

PREFERRED_REPLACEMENT_INDICES = list(range(188, 236))

# Only worm indices are protected: changing them breaks worm damage/custom
# colors. Animated indices are NOT protected — colorAnim is mod-configurable.
PROTECTED_BY_DEFAULT = sorted(
    i for i, material in enumerate(DEFAULT_MATERIALS) if material == MATERIAL["WORM"])

if len(DEFAULT_MATERIALS) != 256:
    raise RuntimeError(f"DEFAULT_MATERIALS must have 256 entries, got {len(DEFAULT_MATERIALS)}")
