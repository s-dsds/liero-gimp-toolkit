"""Binary palette formats used by classic Liero and WebLiero.

Supported sources:
- ``.lpl``        raw 768-byte RGB palette (LieroKit/wledit). Stored 8-bit;
                  6-bit VGA dumps (all values <= 63) are detected and scaled.
- ``.wlsprt``     WebLiero sprite file. Optional 768-byte palette at offset 9.
- ``LIERO.EXE``   decompressed Liero 1.33 executable. Palette, material table
                  and colorAnim ranges live at fixed offsets (the same ones
                  OpenLiero's tc_tool uses).
- ``.lev``        classic 504x350 level. The POWERLEVEL variant appends a
                  10-byte magic plus a 6-bit palette after the pixel data.

All readers return :class:`liero_core.palette.Palette` with 8-bit colors.
6-bit values are scaled with ``v * 4`` to stay byte-identical with community
tools (wltools, wledit, lev2png).
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple

from .palette import Palette, Color, read_gpl

DATA_DIR = Path(__file__).resolve().parent / "data"

WLSPRT_MAGIC = b"WLSPRT"
WLSPRT_PALETTE_OFFSET = 9  # magic(6) + version(2) + has-palette flag(1)

LEV_WIDTH, LEV_HEIGHT = 504, 350
LEV_PIXELS = LEV_WIDTH * LEV_HEIGHT
POWERLEVEL_MAGIC = b"POWERLEVEL"

# Offsets in the decompressed LIERO.EXE 1.33 (135856 bytes), as read by
# OpenLiero's common_exereader.cpp.
EXE_SIZE_133 = 135856
EXE_PALETTE_OFFSET = 132774
EXE_COLOR_ANIM_OFFSET = 0x1AF0C
EXE_MATERIAL_PLANES_OFFSET = 0x1C2E0  # 5 bitplanes x 32 bytes
EXE_WORM_PLANE_OFFSET = 0x1AEA8       # 6th bitplane (worm), 32 bytes


def _scale6(v: int) -> int:
    return (v & 63) * 4


def _colors_from_bytes(raw: bytes, six_bit: bool) -> List[Color]:
    if six_bit:
        raw = bytes(_scale6(b) for b in raw)
    return [tuple(raw[i:i + 3]) for i in range(0, 768, 3)]


def _palette_to_bytes(palette: Palette, six_bit: bool = False) -> bytes:
    colors = palette.padded256().colors
    flat = bytearray()
    for r, g, b in colors:
        if six_bit:
            flat += bytes((r >> 2, g >> 2, b >> 2))
        else:
            flat += bytes((r, g, b))
    return bytes(flat)


def default_palette() -> Palette:
    """The classic Liero 1.33 palette bundled with the toolkit."""
    raw = (DATA_DIR / "default.lpl").read_bytes()
    return Palette("liero-default", _colors_from_bytes(raw, six_bit=False))


# --- .lpl ------------------------------------------------------------------

def read_lpl(path: str | Path) -> Palette:
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) != 768:
        raise ValueError(f"Not a .lpl palette (expected 768 bytes, got {len(raw)}): {path}")
    six_bit = max(raw) <= 63
    return Palette(path.stem, _colors_from_bytes(raw, six_bit))


def write_lpl(path: str | Path, palette: Palette) -> None:
    Path(path).write_bytes(_palette_to_bytes(palette))


# --- .wlsprt ----------------------------------------------------------------

def _check_wlsprt(raw: bytes, path: Path) -> None:
    if raw[:6] != WLSPRT_MAGIC:
        raise ValueError(f"Not a WLSPRT file: {path}")
    version = int.from_bytes(raw[6:8], "little")
    if version != 0:
        raise ValueError(f"Unsupported WLSPRT version {version}: {path}")


def read_wlsprt_palette(path: str | Path) -> Palette:
    """Palette embedded in a WebLiero sprite file (default palette if absent)."""
    path = Path(path)
    raw = path.read_bytes()
    _check_wlsprt(raw, path)
    if not raw[8]:
        pal = default_palette()
        return Palette(path.stem, pal.colors)
    block = raw[WLSPRT_PALETTE_OFFSET:WLSPRT_PALETTE_OFFSET + 768]
    if len(block) != 768:
        raise ValueError(f"Truncated WLSPRT palette: {path}")
    return Palette(path.stem, _colors_from_bytes(block, six_bit=False))


def read_wlsprt_sprites(path: str | Path, max_sprites: int | None = None):
    """Extract sprites from a WLSPRT file as (width, height, index_bytes)."""
    path = Path(path)
    raw = path.read_bytes()
    _check_wlsprt(raw, path)
    off = WLSPRT_PALETTE_OFFSET + (768 if raw[8] else 0)
    nsprites = int.from_bytes(raw[off:off + 2], "little")
    off += 2
    sprites = []
    for _ in range(nsprites):
        if off + 8 > len(raw):
            break
        w = int.from_bytes(raw[off:off + 2], "little", signed=True)
        h = int.from_bytes(raw[off + 2:off + 4], "little", signed=True)
        if w <= 0 or h <= 0:
            break
        data = raw[off + 8:off + 8 + w * h]
        if len(data) < w * h:
            break
        sprites.append((w, h, data))
        off += 8 + w * h
        if max_sprites is not None and len(sprites) >= max_sprites:
            break
    return sprites


def _crop_sprite(w: int, h: int, data: bytes, background: int = 0):
    """Crop a sprite to its non-background bounding box (None if empty)."""
    xmin, xmax, ymin, ymax = w, -1, h, -1
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        for x, v in enumerate(row):
            if v != background:
                if x < xmin:
                    xmin = x
                if x > xmax:
                    xmax = x
                if y < ymin:
                    ymin = y
                ymax = y
    if xmax < 0:
        return None
    cw, ch = xmax - xmin + 1, ymax - ymin + 1
    out = bytearray(cw * ch)
    for y in range(ch):
        src = (ymin + y) * w + xmin
        out[y * cw:(y + 1) * cw] = data[src:src + cw]
    return cw, ch, bytes(out)


def _shrink_sprite(w: int, h: int, data: bytes, max_side: int):
    """Integer-stride nearest downscale for oversized sprites."""
    step = max(1, (max(w, h) + max_side - 1) // max_side)
    if step == 1:
        return w, h, data
    nw, nh = max(1, w // step), max(1, h // step)
    out = bytearray(nw * nh)
    for y in range(nh):
        src = (y * step) * w
        for x in range(nw):
            out[y * nw + x] = data[src + x * step]
    return nw, nh, bytes(out)


def wlsprt_sheet(path: str | Path, sheet_width: int = 320,
                 background: int = 0, max_side: int = 96):
    """Lay WLSPRT sprites out as one compact indexed sheet.

    Sprites are cropped to content; empty and single-color sprites (no
    palette information) are skipped; oversized ones are downscaled.
    Returns (width, height, indices).
    """
    pad = 1
    sprites = []
    for w, h, data in read_wlsprt_sprites(path):
        cropped = _crop_sprite(w, h, data, background)
        if cropped is None:
            continue
        w, h, data = cropped
        if len(set(data)) <= 1:
            continue  # solid color: useless for palette preview
        sprites.append(_shrink_sprite(w, h, data, max_side))
    if not sprites:
        raise ValueError(f"No drawable sprites in WLSPRT file: {path}")
    rows = []
    row, row_w, row_h = [], 0, 0
    for w, h, data in sprites:
        if row and row_w + w + pad > sheet_width:
            rows.append((row, row_h))
            row, row_w, row_h = [], 0, 0
        row.append((row_w, w, h, data))
        row_w += w + pad
        row_h = max(row_h, h)
    if row:
        rows.append((row, row_h))
    height = sum(h + pad for _, h in rows)
    sheet = bytearray([background]) * (sheet_width * height)
    y = 0
    for row, row_h in rows:
        for x0, w, h, data in row:
            for yy in range(h):
                dst = (y + yy) * sheet_width + x0
                sheet[dst:dst + w] = data[yy * w:(yy + 1) * w]
        y += row_h + pad
    return sheet_width, height, bytes(sheet)


def write_wlsprt_palette(src: str | Path, palette: Palette, dest: str | Path | None = None) -> None:
    """Replace (or insert) the palette of a WLSPRT file, keeping sprites intact."""
    src = Path(src)
    raw = src.read_bytes()
    _check_wlsprt(raw, src)
    sprites = raw[WLSPRT_PALETTE_OFFSET + 768:] if raw[8] else raw[WLSPRT_PALETTE_OFFSET:]
    out = raw[:8] + b"\x01" + _palette_to_bytes(palette) + sprites
    Path(dest or src).write_bytes(out)


# --- LIERO.EXE ---------------------------------------------------------------

def _check_exe(raw: bytes, path: Path) -> None:
    if len(raw) < EXE_PALETTE_OFFSET + 768:
        raise ValueError(
            f"File too small for a decompressed Liero 1.33 exe ({len(raw)} bytes). "
            f"A PKLITE-compressed LIERO.EXE must be decompressed first: {path}")


def read_exe_palette(path: str | Path) -> Palette:
    path = Path(path)
    raw = path.read_bytes()
    _check_exe(raw, path)
    block = raw[EXE_PALETTE_OFFSET:EXE_PALETTE_OFFSET + 768]
    return Palette(path.stem, _colors_from_bytes(block, six_bit=True))


def read_exe_color_anim(path: str | Path) -> List[Tuple[int, int]]:
    """The 4 animated (from, to) index ranges stored in the exe."""
    raw = Path(path).read_bytes()
    _check_exe(raw, Path(path))
    block = raw[EXE_COLOR_ANIM_OFFSET:EXE_COLOR_ANIM_OFFSET + 8]
    return [(block[i], block[i + 1]) for i in range(0, 8, 2)]


def read_exe_materials(path: str | Path) -> List[int]:
    """The 256-entry material table stored in the exe as 6 bitplanes.

    Bit order matches the classic material values: dirt=1, dirt2=2, rock=4,
    background=8, seeshadow=16 (always combined with background), worm=32.
    """
    raw = Path(path).read_bytes()
    _check_exe(raw, Path(path))
    materials = [0] * 256
    for plane in range(5):
        off = EXE_MATERIAL_PLANES_OFFSET + plane * 32
        bits = raw[off:off + 32]
        for j in range(256):
            materials[j] |= ((bits[j >> 3] >> (j & 7)) & 1) << plane
    bits = raw[EXE_WORM_PLANE_OFFSET:EXE_WORM_PLANE_OFFSET + 32]
    for j in range(256):
        materials[j] |= ((bits[j >> 3] >> (j & 7)) & 1) << 5
    return materials


def read_lev_pixels(path: str | Path):
    """Pixel indices of a .lev map: (width, height, index_bytes)."""
    raw = Path(path).read_bytes()
    if len(raw) < LEV_PIXELS:
        raise ValueError(f"Not a .lev file (smaller than {LEV_PIXELS} bytes): {path}")
    return LEV_WIDTH, LEV_HEIGHT, raw[:LEV_PIXELS]


# --- .lev (POWERLEVEL) -------------------------------------------------------

def read_lev_palette(path: str | Path) -> Palette:
    """Palette of a POWERLEVEL .lev (default palette for plain levels)."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < LEV_PIXELS:
        raise ValueError(f"Not a .lev file (smaller than {LEV_PIXELS} bytes): {path}")
    tail = raw[LEV_PIXELS:]
    if tail[:10] == POWERLEVEL_MAGIC and len(tail) >= 10 + 768:
        return Palette(path.stem, _colors_from_bytes(tail[10:10 + 768], six_bit=True))
    pal = default_palette()
    return Palette(path.stem, pal.colors)


def write_lev_palette(src: str | Path, palette: Palette, dest: str | Path | None = None) -> None:
    """Turn a .lev into a POWERLEVEL with the given palette (pixels untouched)."""
    src = Path(src)
    raw = src.read_bytes()
    if len(raw) < LEV_PIXELS:
        raise ValueError(f"Not a .lev file (smaller than {LEV_PIXELS} bytes): {src}")
    out = raw[:LEV_PIXELS] + POWERLEVEL_MAGIC + _palette_to_bytes(palette, six_bit=True)
    Path(dest or src).write_bytes(out)


# --- dispatcher --------------------------------------------------------------

def load_palette(path: str | Path) -> Palette:
    """Load a 256-color palette from any supported source file."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".gpl":
        return read_gpl(path).padded256()
    if suffix == ".png":
        from .palette import load_indexed_png_palette
        return load_indexed_png_palette(path)
    if suffix == ".lpl":
        return read_lpl(path)
    if suffix == ".wlsprt":
        return read_wlsprt_palette(path)
    if suffix == ".lev":
        return read_lev_palette(path)
    if suffix == ".exe" or path.name.upper() == "LIERO.EXE":
        return read_exe_palette(path)
    # Fall back to magic sniffing for unknown extensions.
    head = path.read_bytes()[:16]
    if head[:6] == WLSPRT_MAGIC:
        return read_wlsprt_palette(path)
    if head[:12] == b"GIMP Palette":
        return read_gpl(path).padded256()
    if path.stat().st_size == 768:
        return read_lpl(path)
    raise ValueError(f"Unsupported palette source: {path}")
