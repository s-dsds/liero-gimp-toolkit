"""OpenLiero MODERNLV level writer/reader.

Authoritative against the OpenLiero authoring tools (``tools/lev_gen.py`` writer
and ``tools/lev_extract.py`` reader, commit f0d49e3) — NOT the prose in
``docs/modern-level-authoring.md``, which only sketches the format and is wrong
on a few points (see notes below). POWERLEVEL palettes and classic
material-only levels are intentionally out of scope here: every level this
module emits carries a MODERNLV display layer.

On-disk layout (no POWERLEVEL block):

    [OLLEVEL2 header — present only when (w, h) != (504, 350)]
        b"OLLEVEL2"        8 bytes
        version            1 byte   (0)
        width              2 bytes  little-endian uint16
        height             2 bytes  little-endian uint16
    material_id            w*h      raw palette indices (0-255)
    b"MODERNLV"            8 bytes
    display_data           w*h*4    ARGB32 LE; see the animation note
    display_valid          w*h      1 = authored or animated, 0 = palette fallback
    ramp_count             1 byte
    if ramp_count > 0:
        per ramp:
            shift          1 byte
            color_count    2 bytes  little-endian uint16
            colors         color_count*4 bytes  ARGB32 LE (alpha forced 0xFF)
        display_anim       w*h      ramp index per pixel (1-based); 0 = none

Subtleties the tools enforce that the markdown omits:
- ``display_anim`` exists ONLY when ``ramp_count > 0``.
- For an animated pixel, ``display_data`` does not hold a colour — it holds the
  phase offset (anim green channel) packed as a uint32; the colour comes from
  the ramp. ``display_valid`` is forced to 1 for animated pixels.
- A pixel is animated iff its anim RGBA has ``alpha > 0 and red > 0``
  (red = ramp index 1-based, green = phase). Ramp index must be <= ramp_count.
"""
from __future__ import annotations
import json
import struct
from pathlib import Path
from typing import List, Optional, Dict, Tuple

from .defaults import DEFAULT_MATERIALS, MATERIAL
from .material import indices_for_material

LEGACY_W, LEGACY_H = 504, 350
MAX_DIM = 4096
SIZED_MAGIC = b"OLLEVEL2"
MODERNLV_MAGIC = b"MODERNLV"
POWERLEVEL_MAGIC = b"POWERLEVEL"


def argb32(r: int, g: int, b: int, a: int = 0xFF) -> int:
    """Pack RGB(A) into an ARGB32 value (alpha defaults to opaque)."""
    return ((a & 0xFF) << 24) | ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)


def is_sized(width: int, height: int) -> bool:
    """True if the level needs an OLLEVEL2 header (anything but legacy 504x350)."""
    return (width, height) != (LEGACY_W, LEGACY_H)


def validate_ramps(ramps: List[Dict]) -> None:
    """Raise ValueError unless ramps match what lev_gen.py accepts."""
    if not (1 <= len(ramps) <= 255):
        raise ValueError("need 1-255 ramps")
    for idx, ramp in enumerate(ramps):
        if "shift" not in ramp or "colors" not in ramp:
            raise ValueError(f"ramp {idx}: must have 'shift' and 'colors'")
        if not (0 <= int(ramp["shift"]) <= 255):
            raise ValueError(f"ramp {idx}: shift must be 0-255")
        if not (1 <= len(ramp["colors"]) <= 4096):
            raise ValueError(f"ramp {idx}: 1-4096 colors required")


def _ramp_colors_bytes(ramp: Dict) -> bytes:
    out = bytearray()
    for hx in ramp["colors"]:
        hx = str(hx).lstrip("#")
        r8, g8, b8 = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        out += struct.pack("<I", argb32(r8, g8, b8))
    return bytes(out)


def build_level(
    width: int,
    height: int,
    material: bytes,
    display_rgba: bytes,
    ramps: Optional[List[Dict]] = None,
    anim_rgba: Optional[bytes] = None,
) -> bytes:
    """Serialize a MODERNLV OpenLiero level, byte-identical to lev_gen.py.

    material      -- w*h raw palette indices (an indexed image's pixel buffer).
    display_rgba  -- w*h*4 RGBA bytes; alpha>0 pixels are authored.
    ramps         -- optional list of {"shift": int, "colors": ["#RRGGBB", ...]}.
    anim_rgba     -- optional w*h*4 RGBA bytes; R=ramp (1-based), G=phase.
    """
    cells = width * height
    if not (1 <= width <= MAX_DIM and 1 <= height <= MAX_DIM):
        raise ValueError(f"size {width}x{height} outside 1-{MAX_DIM} range")
    if len(material) != cells:
        raise ValueError(f"material is {len(material)} bytes, expected {cells}")
    if len(display_rgba) != cells * 4:
        raise ValueError(f"display is {len(display_rgba)} bytes, expected {cells * 4}")
    if anim_rgba is not None:
        if not ramps:
            raise ValueError("anim requires ramps")
        if len(anim_rgba) != cells * 4:
            raise ValueError(f"anim is {len(anim_rgba)} bytes, expected {cells * 4}")
    ramps = ramps or []
    if ramps:
        validate_ramps(ramps)

    # display_data / display_valid from the RGBA display layer.
    dd = bytearray(cells * 4)
    dv = bytearray(cells)
    for i in range(cells):
        if display_rgba[i * 4 + 3] > 0:  # alpha
            struct.pack_into(
                "<I", dd, i * 4,
                argb32(display_rgba[i * 4], display_rgba[i * 4 + 1], display_rgba[i * 4 + 2]),
            )
            dv[i] = 1

    # display_anim from the anim layer; animated pixels repack phase into dd.
    da = bytearray(cells)
    if anim_rgba is not None:
        for i in range(cells):
            r, g, a = anim_rgba[i * 4], anim_rgba[i * 4 + 1], anim_rgba[i * 4 + 3]
            if a > 0 and r > 0:
                if r > len(ramps):
                    x, y = i % width, i // width
                    raise ValueError(
                        f"anim pixel ({x},{y}): ramp index {r} exceeds ramp count {len(ramps)}"
                    )
                dv[i] = 1
                da[i] = r
                struct.pack_into("<I", dd, i * 4, g)  # phase offset replaces colour

    out = bytearray()
    if is_sized(width, height):
        out += SIZED_MAGIC
        out += bytes([0])  # version
        out += struct.pack("<H", width)
        out += struct.pack("<H", height)
    out += material
    out += MODERNLV_MAGIC
    out += dd
    out += dv
    if ramps:
        out += bytes([len(ramps)])
        for ramp in ramps:
            out += bytes([int(ramp["shift"]) & 0xFF])
            out += struct.pack("<H", len(ramp["colors"]))
            out += _ramp_colors_bytes(ramp)
        out += da
    else:
        out += b"\x00"  # ramp_count = 0, no display_anim follows
    return bytes(out)


def write_level(path, width, height, material, display_rgba, ramps=None, anim_rgba=None) -> None:
    """build_level(...) written to disk."""
    data = build_level(width, height, material, display_rgba, ramps, anim_rgba)
    Path(path).write_bytes(data)


def extract_level(data: bytes) -> Dict:
    """Parse a MODERNLV level, mirroring lev_extract.py.

    Returns a dict with: width, height, material (bytes), display_data (bytes or
    None), display_valid (bytes or None), ramps (list), display_anim (bytes or
    None). A POWERLEVEL block, if present, is skipped over.
    """
    if data[:8] == SIZED_MAGIC:
        if len(data) < 13:
            raise ValueError("OLLEVEL2 header truncated")
        width = data[9] | (data[10] << 8)
        height = data[11] | (data[12] << 8)
        if not (1 <= width <= MAX_DIM and 1 <= height <= MAX_DIM):
            raise ValueError(f"OLLEVEL2 dimensions {width}x{height} invalid")
        body = 13
    else:
        width, height = LEGACY_W, LEGACY_H
        body = 0

    cells = width * height
    if len(data) < body + cells:
        raise ValueError(f"file too small: {len(data)} bytes (need {body + cells})")

    result: Dict = {
        "width": width, "height": height,
        "material": data[body:body + cells],
        "display_data": None, "display_valid": None,
        "ramps": [], "display_anim": None,
    }
    rest = data[body + cells:]
    pos = 0
    while pos < len(rest):
        if rest[pos:pos + 10] == POWERLEVEL_MAGIC:
            pos += 10 + 768  # skip palette; out of scope here
        elif rest[pos:pos + 8] == MODERNLV_MAGIC:
            pos += 8
            dd = rest[pos:pos + cells * 4]
            dv = rest[pos + cells * 4:pos + cells * 5]
            ramp_count = rest[pos + cells * 5]
            pos += cells * 5 + 1
            ramps: List[Dict] = []
            for _ in range(ramp_count):
                shift = rest[pos]
                color_count = struct.unpack_from("<H", rest, pos + 1)[0]
                pos += 3
                colors = []
                for ci in range(color_count):
                    argb = struct.unpack_from("<I", rest, pos + ci * 4)[0]
                    colors.append(f"#{(argb >> 16) & 0xFF:02X}{(argb >> 8) & 0xFF:02X}{argb & 0xFF:02X}")
                pos += color_count * 4
                ramps.append({"shift": int(shift), "colors": colors})
            da = None
            if ramp_count > 0:
                da = rest[pos:pos + cells]
                pos += cells
            result.update(display_data=dd, display_valid=dv, ramps=ramps, display_anim=da)
        else:
            break
    return result


# --- preview engine ---------------------------------------------------------
# Render what OpenLiero shows, frame-accurate, so the GIMP plug-in can preview a
# level (and its animation) without launching the game. Mirrors
# Level::AppearanceAt / Level::ResolveDisplayAt in src/game/level.hpp.

# Colours for the material-mask view, keyed by the material flag bits
# (Material::kDirt=1 .. kWormM=32). Matches the toolkit's material families.
_MASK_COLORS: List[Tuple[int, Tuple[int, int, int]]] = [
    (MATERIAL["WORM"], (255, 235, 60)),    # 32 worm barrier -> yellow
    (MATERIAL["ROCK"], (120, 130, 145)),   # 4  rock         -> slate grey
    (MATERIAL["DIRT_2"], (150, 95, 40)),   # 2  dirt variant -> mid brown
    (MATERIAL["DIRT"], (139, 69, 19)),     # 1  dirt         -> saddle brown
    (MATERIAL["BG_SEESHADOW"] & ~MATERIAL["BG"], (90, 120, 170)),  # 16 see-shadow tint
    (MATERIAL["BG"], (210, 225, 245)),     # 8  background   -> pale sky
]
_MASK_SOLID = (60, 60, 60)  # flags == 0: solid to worms, shots pass


def material_flag_color(flags: int) -> Tuple[int, int, int]:
    """RGB for a material flag set (highest-priority flag wins)."""
    for bit, rgb in _MASK_COLORS:
        if flags & bit:
            return rgb
    return _MASK_SOLID


def material_mask_rgb(material_id, materials_table=None) -> bytes:
    """W*H*3 RGB bytes colouring each index by its material category."""
    table = materials_table or DEFAULT_MATERIALS
    out = bytearray(len(material_id) * 3)
    for i, idx in enumerate(material_id):
        out[i * 3:i * 3 + 3] = bytes(material_flag_color(table[idx]))
    return out


def _ramps_to_rgb(ramps: List[Dict]) -> List[Tuple[int, List[Tuple[int, int, int]]]]:
    parsed = []
    for ramp in ramps:
        cols = []
        for hx in ramp["colors"]:
            hx = str(hx).lstrip("#")
            cols.append((int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)))
        parsed.append((int(ramp["shift"]) & 0xFF, cols))
    return parsed


def render_frame_rgb(level: Dict, palette_rgb: bytes, cycles: int = 0) -> bytes:
    """W*H*3 RGB of the modern-mode appearance at frame `cycles`.

    `level` is an extract_level() dict. `palette_rgb` is 256*3 bytes used for the
    palette-fallback path (pixels with display_valid==0), i.e. pal[material_id].
    Animated pixels cycle exactly as Level::ResolveDisplayAt does.
    """
    w, h = level["width"], level["height"]
    cells = w * h
    mat = level["material"]
    dd = level["display_data"]
    dv = level["display_valid"]
    da = level["display_anim"]
    ramps = _ramps_to_rgb(level.get("ramps") or [])
    out = bytearray(cells * 3)
    for i in range(cells):
        if dv and dv[i]:
            ddv = int.from_bytes(dd[i * 4:i * 4 + 4], "little")
            a = da[i] if da else 0
            if a == 0 or a > len(ramps) or not ramps[a - 1][1]:
                r, g, b = (ddv >> 16) & 0xFF, (ddv >> 8) & 0xFF, ddv & 0xFF
            else:
                shift, cols = ramps[a - 1]
                inc = (cycles >> shift) if shift < 32 else 0
                r, g, b = cols[(ddv + inc) % len(cols)]  # ddv = per-pixel phase offset
        else:
            pi = mat[i]
            r, g, b = palette_rgb[pi * 3], palette_rgb[pi * 3 + 1], palette_rgb[pi * 3 + 2]
        out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = r, g, b
    return bytes(out)


# --- build material/anim maps from layer coverage ---------------------------
# Lets the GIMP plug-in derive a material mask (and animation map) on the fly
# from the named top-level layers/groups of an RGB image, instead of needing a
# separate indexed image.

def canonical_index_for_material(material: int, materials_table=None) -> int:
    """A representative palette index whose material == `material`.

    Physics-equivalent to any other index of that material; for the common
    materials these are the OpenLiero/lev_gen canonical indices (dirt=12,
    rock=19, worm=30, open space=160).
    """
    idxs = indices_for_material(material, materials_table)
    return idxs[0] if idxs else 0


def compose_material_mask(width: int, height: int, layers, default_index: int = 160) -> bytes:
    """Material index map from layer coverage.

    `layers` is an iterable of (coverage, index) in TOP-to-BOTTOM order;
    `coverage` is width*height bytes (nonzero = covered). The topmost covered
    layer wins; uncovered pixels get `default_index` (160 = open space).
    """
    n = width * height
    out = bytearray([default_index & 0xFF]) * n
    claimed = bytearray(n)
    for cov, idx in layers:
        if len(cov) != n:
            raise ValueError("coverage size mismatch")
        idx &= 0xFF
        for i in range(n):
            if cov[i] and not claimed[i]:
                out[i] = idx
                claimed[i] = 1
    return bytes(out)


def _nearest_color_index(rgb, cols) -> int:
    """Index of the ramp colour closest to `rgb` (squared RGB distance)."""
    bi, bd = 0, None
    for k, (r, g, b) in enumerate(cols):
        d = (r - rgb[0]) ** 2 + (g - rgb[1]) ** 2 + (b - rgb[2]) ** 2
        if bd is None or d < bd:
            bd, bi = d, k
    return bi


def build_anim_rgba(width: int, height: int, layers, default_phase: int = 0, *,
                    phase_mode: str = 'sync', display_rgba: bytes = None,
                    ramps: List[Dict] = None) -> bytes:
    """Animation RGBA (R=ramp 1-based, G=phase, A=255 where animated) from layer
    coverage. `layers` is (coverage, ramp_index) TOP-to-BOTTOM; topmost covered
    layer wins (a non-animated higher layer still blocks a lower animated one).
    ramp_index <= 0 means "covered but not animated".

    `phase_mode` sets the per-pixel phase offset (the Green channel):
      'sync'   -- all `default_phase` (pixels cycle together)
      'color'  -- the ramp index of the pixel's own display colour, so frame 0
                  reproduces the painted art, then flows (needs display_rgba +
                  ramps)
      'wave'   -- (x + y), a diagonal rolling stagger
      'random' -- deterministic per-pixel hash (shimmer)
    """
    n = width * height
    out = bytearray(n * 4)
    claimed = bytearray(n)
    ph0 = default_phase & 0xFF
    ramp_cols = ([cols for _shift, cols in _ramps_to_rgb(ramps)]
                 if (phase_mode == 'color' and ramps) else None)
    for cov, ramp in layers:
        if len(cov) != n:
            raise ValueError("coverage size mismatch")
        for i in range(n):
            if cov[i] and not claimed[i]:
                claimed[i] = 1
                if ramp <= 0:
                    continue
                if phase_mode == 'wave':
                    ph = ((i % width) + (i // width)) & 0xFF
                elif phase_mode == 'random':
                    ph = (i * 2654435761) & 0xFF  # Knuth multiplicative hash
                elif phase_mode == 'color' and ramp_cols and ramp - 1 < len(ramp_cols) \
                        and ramp_cols[ramp - 1] and display_rgba is not None:
                    rgb = (display_rgba[i * 4], display_rgba[i * 4 + 1], display_rgba[i * 4 + 2])
                    ph = _nearest_color_index(rgb, ramp_cols[ramp - 1]) & 0xFF
                else:
                    ph = ph0
                out[i * 4] = ramp & 0xFF
                out[i * 4 + 1] = ph
                out[i * 4 + 3] = 255
    return bytes(out)


def save_ramps_json(path, ramps: List[Dict]) -> None:
    """Write ramps to a lev_gen-compatible ramps.json (validated first)."""
    validate_ramps(ramps)
    Path(path).write_text(json.dumps(ramps, indent=2))


def load_ramps_json(path) -> List[Dict]:
    """Read and validate a ramps.json."""
    ramps = json.loads(Path(path).read_text())
    validate_ramps(ramps)
    return ramps


# --- fast incremental animation preview -------------------------------------
# Re-rendering the whole frame every tick is O(w*h) in pure Python and freezes
# the UI on a real level. Instead: render one static base, then each tick only
# rewrite the (usually few) animated pixels.

def animation_cells(level: Dict):
    """List of (pixel_index, ramp_colors, shift, phase_offset) for animated px."""
    da = level.get('display_anim')
    if not da:
        return []
    dd = level['display_data']
    ramps = _ramps_to_rgb(level.get('ramps') or [])
    cells = []
    for i, a in enumerate(da):
        if a and a <= len(ramps):
            shift, cols = ramps[a - 1]
            if cols:
                phase = int.from_bytes(dd[i * 4:i * 4 + 4], 'little')
                cells.append((i, cols, shift, phase))
    return cells


def render_anim_frame(base_rgb: bytes, cells, cycles: int) -> bytes:
    """Static `base_rgb` with only the animated `cells` updated for `cycles`.

    Equivalent to render_frame_rgb(level, pal, cycles) but O(animated) per call.
    """
    out = bytearray(base_rgb)
    for i, cols, shift, phase in cells:
        inc = (cycles >> shift) if shift < 32 else 0
        r, g, b = cols[(phase + inc) % len(cols)]
        out[i * 3], out[i * 3 + 1], out[i * 3 + 2] = r, g, b
    return bytes(out)


def ordered_unique_colors(rgba: bytes, max_colors: int = 64) -> List[str]:
    """Distinct opaque colours in an RGBA buffer, ordered by luminance (so a
    ramp built from them cycles smoothly), capped to `max_colors` by even
    sampling. Returns '#RRGGBB' strings — ideal ramp colours from a layer.
    """
    seen = set()
    for i in range(0, len(rgba), 4):
        if rgba[i + 3] > 0:
            seen.add((rgba[i], rgba[i + 1], rgba[i + 2]))
    colors = sorted(seen, key=lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2])
    if not colors:
        return []
    if len(colors) > max_colors:
        step = len(colors) / max_colors
        colors = [colors[int(k * step)] for k in range(max_colors)]
    return ['#%02X%02X%02X' % c for c in colors]
