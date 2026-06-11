"""Color transforms for palette manipulation (Palette Lab).

The pipeline works in float space (0-255 floats per channel) so chained or
committed adjustments don't accumulate 8-bit rounding errors; quantize with
:func:`quantize` only for display or export.
"""
from __future__ import annotations
import colorsys
from typing import Iterable, List, Sequence, Tuple

from .palette import Color

FColor = Tuple[float, float, float]


def clamp8(x: float) -> int:
    return max(0, min(255, round(x)))


def quantize(colors: Sequence[FColor]) -> List[Color]:
    return [tuple(clamp8(v) for v in c) for c in colors]


def to_float(colors: Sequence[Color]) -> List[FColor]:
    return [tuple(float(v) for v in c) for c in colors]


def adjust_rgb_f(rgb: FColor, hue_degrees: float = 0.0, saturation: float = 1.0,
                 brightness: float = 0.0, contrast: float = 1.0,
                 temperature: float = 0.0, colorize: bool = False) -> FColor:
    """Adjust one float color.

    Relative mode (default): hue shift in degrees, saturation multiplier.
    Colorize mode: hue is absolute (-180..180 mapped onto the wheel) and
    saturation (clamped to 0..1) is absolute too — lightness is preserved, so
    grays gain color.
    Temperature: positive warms (R up, B down), negative cools. Applied before
    brightness/contrast.
    """
    r, g, b = [max(0.0, min(1.0, v / 255.0)) for v in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if colorize:
        h = (hue_degrees / 360.0) % 1.0
        s = max(0.0, min(1.0, saturation))
    else:
        h = (h + hue_degrees / 360.0) % 1.0
        s = max(0.0, min(1.0, s * saturation))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    out = []
    for v, temp_gain in ((r * 255.0, 0.6), (g * 255.0, 0.15), (b * 255.0, -0.6)):
        v = v + temperature * temp_gain
        v = (v - 127.5) * contrast + 127.5 + brightness
        out.append(max(0.0, min(255.0, v)))
    return tuple(out)


def adjust_rgb(rgb: Color, **kwargs) -> Color:
    """8-bit convenience wrapper around :func:`adjust_rgb_f`."""
    return tuple(clamp8(v) for v in adjust_rgb_f(tuple(float(x) for x in rgb), **kwargs))


def adjusted_palette_f(colors: Sequence[FColor], indices: Iterable[int],
                       locked: Iterable[int] = (), **kwargs) -> List[FColor]:
    """Apply adjust_rgb_f to ``indices`` of ``colors``, skipping ``locked``."""
    out = list(colors)
    locked = set(locked)
    for i in indices:
        if i in locked or not 0 <= i < len(out):
            continue
        out[i] = adjust_rgb_f(out[i], **kwargs)
    return out


def adjusted_palette(colors: Sequence[Color], indices: Iterable[int],
                     locked: Iterable[int] = (), **kwargs) -> List[Color]:
    """8-bit convenience wrapper around :func:`adjusted_palette_f`."""
    out = adjusted_palette_f(to_float(colors), indices, locked, **kwargs)
    return quantize(out)


def gradient_palette_f(colors: Sequence[FColor], indices: Iterable[int],
                       locked: Iterable[int] = ()) -> List[FColor]:
    """Re-ramp the selected indices as a linear gradient.

    The selection is processed in index order; the first and last selected
    colors are kept as endpoints and everything in between is interpolated.
    This is the classic fix for a ramp that lost its contrast ("flattened").
    """
    idxs = sorted(set(indices))
    out = list(colors)
    if len(idxs) < 3:
        return out
    start = colors[idxs[0]]
    end = colors[idxs[-1]]
    locked = set(locked)
    span = len(idxs) - 1
    for pos, i in enumerate(idxs):
        if i in locked:
            continue
        t = pos / span
        out[i] = tuple(s + (e - s) * t for s, e in zip(start, end))
    return out


def similar_color_indices(colors: Sequence[Color], target: Color,
                          hue_tol: float = 35 / 360.0, sat_floor: float = 0.15,
                          light_tol: float = 0.20) -> set:
    """Indices whose color belongs to the same family as ``target``.

    A saturated target selects all colors of a close hue (e.g. "all the
    blues", whatever their lightness — a gradient/ramp counts as one family).
    A gray target selects grays of similar lightness.
    """
    tr, tg, tb = [v / 255.0 for v in target]
    th, tl, ts = colorsys.rgb_to_hls(tr, tg, tb)
    out = set()
    for i, (r, g, b) in enumerate(colors):
        h, l, s = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
        if ts >= sat_floor:
            hue_diff = abs(h - th)
            hue_diff = min(hue_diff, 1.0 - hue_diff)
            if s >= sat_floor and hue_diff <= hue_tol:
                out.add(i)
        else:
            if s < sat_floor and abs(l - tl) <= light_tol:
                out.add(i)
    return out


def uniquify_palette(colors: Sequence[Color]) -> List[Color]:
    """Make every RGB value unique by minimally nudging duplicates.

    First occurrence keeps its exact value; later duplicates get the closest
    free RGB (smallest Chebyshev distance, ties broken by Manhattan distance).
    GIMP needs unique colormap entries to keep indexed pixels distinguishable.
    """
    seen = set()
    out: List[Color] = []
    for color in colors:
        c = tuple(int(v) for v in color)
        if c not in seen:
            seen.add(c)
            out.append(c)
            continue
        found = None
        for radius in range(1, 256):
            candidates = []
            offsets = range(-radius, radius + 1)
            for dr in offsets:
                for dg in offsets:
                    for db in offsets:
                        if max(abs(dr), abs(dg), abs(db)) != radius:
                            continue
                        cand = (c[0] + dr, c[1] + dg, c[2] + db)
                        if all(0 <= v <= 255 for v in cand) and cand not in seen:
                            candidates.append((abs(dr) + abs(dg) + abs(db), cand))
            if candidates:
                found = min(candidates)[1]
                break
        if found is None:  # pragma: no cover - >16M colors needed
            raise RuntimeError("No free RGB value left to uniquify palette")
        seen.add(found)
        out.append(found)
    return out
