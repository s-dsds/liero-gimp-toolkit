from __future__ import annotations
from collections import Counter
from typing import Iterable, Sequence
from .palette import Color, nearest_color_index
from .defaults import DEFAULT_MATERIALS, PREFERRED_REPLACEMENT_INDICES, PROTECTED_BY_DEFAULT
from .material import indices_for_material


def _dist2(a: Color, b: Color) -> int:
    return sum((int(x) - int(y)) ** 2 for x, y in zip(a, b))


def median_cut(colors: Iterable[Color], k: int) -> list[Color]:
    """Small deterministic median-cut quantizer suitable for first iterations."""
    weighted = Counter(colors)
    if not weighted or k <= 0:
        return []
    boxes = [list(weighted.items())]
    while len(boxes) < k:
        # split box with largest weighted channel range
        best = None
        best_score = -1
        for bi, box in enumerate(boxes):
            if len(box) <= 1:
                continue
            ranges = []
            for ch in range(3):
                vals = [c[ch] for c, _ in box]
                ranges.append(max(vals) - min(vals))
            score = max(ranges) * sum(w for _, w in box)
            if score > best_score:
                best = bi
                best_score = score
        if best is None:
            break
        box = boxes.pop(best)
        ch = max(range(3), key=lambda c: max(col[c] for col, _ in box) - min(col[c] for col, _ in box))
        box.sort(key=lambda item: item[0][ch])
        total = sum(w for _, w in box)
        acc = 0
        split = 0
        for i, (_, w) in enumerate(box):
            acc += w
            if acc >= total / 2:
                split = max(1, i + 1)
                break
        boxes.append(box[:split])
        boxes.append(box[split:])
    out = []
    for box in boxes[:k]:
        total = sum(w for _, w in box)
        if total == 0:
            continue
        out.append(tuple(round(sum(c[ch] * w for c, w in box) / total) for ch in range(3)))
    return out


def choose_palette_slots(
    representatives: Sequence[Color],
    base_palette: Sequence[Color],
    target_material: int,
    material_table: list[int] | None = None,
    protected: set[int] | None = None,
    max_reuse_error: int = 18 * 18 * 3,
) -> tuple[list[int], list[Color], list[int]]:
    """Assign representative colors to palette indices.

    Returns (allowed_indices, new_palette, new_material_table).
    Reuses same-material colors if close; otherwise allocates from 188-235 first.
    """
    table = list(material_table or DEFAULT_MATERIALS)
    protected = set(PROTECTED_BY_DEFAULT if protected is None else protected)
    new_palette = list(base_palette[:256])
    while len(new_palette) < 256:
        new_palette.append((0, 0, 0))

    same_material = [i for i in indices_for_material(target_material, table) if i not in protected]
    replacement_pool = [i for i in PREFERRED_REPLACEMENT_INDICES if i not in protected]
    used: set[int] = set()
    allowed: list[int] = []

    for color in representatives:
        chosen = None
        if same_material:
            nearest = min((i for i in same_material if i not in used), key=lambda i: _dist2(color, new_palette[i]), default=None)
            if nearest is not None and _dist2(color, new_palette[nearest]) <= max_reuse_error:
                chosen = nearest
        if chosen is None:
            chosen = next((i for i in replacement_pool if i not in used), None)
        if chosen is None:
            chosen = next((i for i in same_material if i not in used), None)
        if chosen is None:
            raise RuntimeError(f"No free palette slot for material {target_material}")
        new_palette[chosen] = color
        table[chosen] = target_material
        used.add(chosen)
        allowed.append(chosen)
    return allowed, new_palette, table


def remap_pixels_to_indices(pixels: Iterable[Color], palette: Sequence[Color], allowed_indices: Sequence[int]) -> list[int]:
    return [nearest_color_index(c, palette, allowed_indices) for c in pixels]
