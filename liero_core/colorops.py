"""Color transforms for palette manipulation (Palette Lab)."""
from __future__ import annotations
import colorsys
from typing import Iterable, Sequence

from .palette import Color


def clamp8(x: float) -> int:
    return max(0, min(255, round(x)))


def adjust_rgb(rgb: Color, hue_degrees: float = 0.0, saturation: float = 1.0,
               brightness: float = 0.0, contrast: float = 1.0) -> Color:
    """Hue shift (degrees), saturation multiplier, then brightness offset and
    contrast factor around mid-gray."""
    r, g, b = [v / 255.0 for v in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + hue_degrees / 360.0) % 1.0
    s = max(0.0, min(1.0, s * saturation))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    out = []
    for v in (r * 255.0, g * 255.0, b * 255.0):
        v = (v - 127.5) * contrast + 127.5 + brightness
        out.append(clamp8(v))
    return tuple(out)


def adjusted_palette(colors: Sequence[Color], indices: Iterable[int],
                     locked: Iterable[int] = (), **kwargs) -> list[Color]:
    """Apply adjust_rgb to ``indices`` of ``colors``, skipping ``locked``."""
    out = list(colors)
    locked = set(locked)
    for i in indices:
        if i in locked or not 0 <= i < len(out):
            continue
        out[i] = adjust_rgb(out[i], **kwargs)
    return out
