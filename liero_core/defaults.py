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
    0, 0, 0, 0, 24, 24, 24, 24, 8, 8, 8, 8, 0, 0, 0, 0,
    0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]

DEFAULT_COLOR_ANIM = [129, 132, 133, 136, 152, 159, 168, 171]

PREFERRED_REPLACEMENT_INDICES = list(range(188, 236))

PROTECTED_BY_DEFAULT = sorted(set(DEFAULT_COLOR_ANIM) | {
    i for i, material in enumerate(DEFAULT_MATERIALS) if material == MATERIAL["WORM"]
})

if len(DEFAULT_MATERIALS) != 256:
    raise RuntimeError(f"DEFAULT_MATERIALS must have 256 entries, got {len(DEFAULT_MATERIALS)}")
