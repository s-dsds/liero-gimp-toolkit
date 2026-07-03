"""Color transforms for palette manipulation (Palette Lab).

The pipeline works in float space (0-255 floats per channel) so chained or
committed adjustments don't accumulate 8-bit rounding errors; quantize with
:func:`quantize` only for display or export.
"""
from __future__ import annotations
import colorsys
import math
from typing import Iterable, List, Sequence, Tuple

from .palette import Color

FColor = Tuple[float, float, float]

# OKLab chroma of a moderately saturated color; used as the reference level
# when ``tint`` injects color into near-gray ramps (rock, shadow).
REF_CHROMA = 0.12


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
                       locked: Iterable[int] = (), space: str = 'rgb') -> List[FColor]:
    """Re-ramp the selected indices as a gradient between the endpoints.

    The selection is processed in index order; the first and last selected
    colors are kept as endpoints and everything in between is interpolated.
    This is the classic fix for a ramp that lost its contrast ("flattened").

    ``space`` selects the interpolation space: ``'rgb'`` (linear channel lerp,
    the original behavior) or ``'oklab'`` (perceptually even lightness/hue —
    the endpoints are identical either way, only the intermediate rungs move).
    """
    idxs = sorted(set(indices))
    out = list(colors)
    if len(idxs) < 3:
        return out
    start = colors[idxs[0]]
    end = colors[idxs[-1]]
    if space == 'oklab':
        start_lab = srgb_to_oklab(start)
        end_lab = srgb_to_oklab(end)
    locked = set(locked)
    span = len(idxs) - 1
    for pos, i in enumerate(idxs):
        if i in locked:
            continue
        t = pos / span
        if space == 'oklab':
            lab = tuple(s + (e - s) * t for s, e in zip(start_lab, end_lab))
            out[i] = oklab_to_srgb(lab)
        else:
            out[i] = tuple(s + (e - s) * t for s, e in zip(start, end))
    return out


# --- OKLab / OKLCh perceptual color space (Björn Ottosson) -----------------
# All helpers take and return FColor (0-255 float RGB) to match the pipeline;
# OKLab is (L, a, b) with L in ~0..1, OKLCh is (L, C, hue_degrees).

def _srgb_to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    s = 12.92 * c if c <= 0.0031308 else 1.055 * (max(c, 0.0) ** (1 / 2.4)) - 0.055
    return s * 255.0


def _cbrt(x: float) -> float:
    return math.copysign(abs(x) ** (1 / 3), x)


def srgb_to_oklab(rgb: FColor) -> Tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(v) for v in rgb)
    l = _cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = _cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = _cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    )


def oklab_to_srgb(lab: Tuple[float, float, float]) -> FColor:
    """OKLab back to float sRGB. May exceed 0..255 if out of gamut — the
    caller clamps at display/export time via :func:`quantize`."""
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (
        _linear_to_srgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        _linear_to_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        _linear_to_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s),
    )


def oklab_to_oklch(lab: Tuple[float, float, float]) -> Tuple[float, float, float]:
    L, a, b = lab
    return (L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360.0)


def oklch_to_oklab(lch: Tuple[float, float, float]) -> Tuple[float, float, float]:
    L, C, h = lch
    rad = math.radians(h)
    return (L, C * math.cos(rad), C * math.sin(rad))


def _chroma_weighted_mean_hue(lchs: Sequence[Tuple[float, float, float]]) -> float | None:
    """Circular mean of hues weighted by chroma. None if all near-gray."""
    x = sum(C * math.cos(math.radians(h)) for _L, C, h in lchs)
    y = sum(C * math.sin(math.radians(h)) for _L, C, h in lchs)
    if math.hypot(x, y) < 1e-6:
        return None
    return math.degrees(math.atan2(y, x)) % 360.0


def _wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def retarget_hue_f(colors: Sequence[FColor], indices: Iterable[int],
                   target_hue: float, coherence: float = 0.0, tint: float = 0.0,
                   locked: Iterable[int] = ()) -> List[FColor]:
    """Retexture the selected ramp toward ``target_hue`` (degrees, OKLCh).

    The ramp is rotated rigidly so its chroma-weighted mean hue lands on
    ``target_hue`` — each rung keeps its OKLab lightness (ramp shape intact)
    and its hue offset from the ramp mean, so a natural ramp stays natural in
    the new hue family.

    ``coherence`` 0..1 collapses that hue spread toward the target (0 = keep
    the original spread, just shifted; 1 = every rung exactly ``target_hue``).

    ``tint`` 0..1 raises low-chroma rungs toward :data:`REF_CHROMA` so a gray
    ramp (rock, shadow) actually takes on the color; vivid rungs are left
    alone. With tint 0 a fully gray ramp is unchanged (no hue to rotate).
    """
    out = list(colors)
    locked = set(locked)
    sel = [i for i in sorted(set(indices))
           if i not in locked and 0 <= i < len(out)]
    if not sel:
        return out
    lchs = [oklab_to_oklch(srgb_to_oklab(out[i])) for i in sel]
    mean_hue = _chroma_weighted_mean_hue(lchs)
    if mean_hue is None:  # fully gray ramp: shift is a no-op, rely on tint
        mean_hue = target_hue
    for i, (L, C, h) in zip(sel, lchs):
        new_h = target_hue + _wrap180(h - mean_hue) * (1.0 - coherence)
        new_C = C + tint * max(0.0, REF_CHROMA - C)
        out[i] = oklab_to_srgb(oklch_to_oklab((L, new_C, new_h)))
    return out


def _hls(rgb: Color):
    r, g, b = [v / 255.0 for v in rgb]
    return colorsys.rgb_to_hls(r, g, b)


def _hue_diff(a: float, b: float) -> float:
    d = abs(a - b)
    return min(d, 1.0 - d)


def _colors_close(a: Color, b: Color, hue_tol: float = 14 / 360.0,
                  sat_tol: float = 0.25, light_tol: float = 0.16,
                  sat_floor: float = 0.12) -> bool:
    """Perceptual 'same family' test: close hue AND saturation AND lightness.

    Grays (low saturation) only match other grays of similar lightness — a
    muted tan never matches a vivid orange (saturation gap), nor a red
    (hue gap), nor a near-black (lightness gap).
    """
    ha, la, sa = _hls(a)
    hb, lb, sb = _hls(b)
    a_gray, b_gray = sa < sat_floor, sb < sat_floor
    if a_gray != b_gray:
        return False
    if abs(la - lb) > light_tol:
        return False
    if a_gray:
        return True
    return _hue_diff(ha, hb) <= hue_tol and abs(sa - sb) <= sat_tol


def detect_ramp(colors: Sequence[Color], index: int,
                max_step: float = 100.0, hue_step_tol: float = 18 / 360.0) -> list:
    """The consecutive index run forming a gradient/ramp around ``index``.

    Neighbors belong to the ramp while the color step stays small and the hue
    doesn't jump (hue drift along a ramp is normal; a jump means a new ramp).
    """
    def continuous(i, j):
        a, b = colors[i], colors[j]
        if sum((x - y) ** 2 for x, y in zip(a, b)) > max_step ** 2:
            return False
        ha, _la, sa = _hls(a)
        hb, _lb, sb = _hls(b)
        if min(sa, sb) >= 0.12 and _hue_diff(ha, hb) > hue_step_tol:
            return False
        return True

    lo = hi = index
    while lo - 1 >= 0 and continuous(lo - 1, lo):
        lo -= 1
    while hi + 1 < len(colors) and continuous(hi, hi + 1):
        hi += 1
    return list(range(lo, hi + 1))


def similar_color_indices(colors: Sequence[Color], index: int) -> set:
    """Indices similar to ``colors[index]``, ramp-aware.

    First the ramp containing the index is detected (consecutive gradient),
    then every palette color close to ANY ramp color joins the family — so
    duplicate ramps and parallel ramps of the same tone are found, while
    vivid/other-hue colors stay out.
    """
    ramp = detect_ramp(colors, index)
    ramp_colors = [colors[i] for i in ramp]
    out = set(ramp)
    for i, c in enumerate(colors):
        if i in out:
            continue
        if any(_colors_close(c, rc) for rc in ramp_colors):
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
