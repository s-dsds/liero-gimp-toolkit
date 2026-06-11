"""Byte-exact color conversion between liero_core and GIMP.

GIMP-dependent: import only from plug-in code.

GeglColor.set_rgba()/get_rgba() interpret values as LINEAR RGB, while palette
files and colormaps are sRGB bytes — using them corrupts colors (read: too
dark; write: too bright). Always go through Babl "R'G'B' u8" bytes instead.
"""
from __future__ import annotations

import gi
gi.require_version('Gegl', '0.4')
gi.require_version('Babl', '0.1')
from gi.repository import Gegl, GLib, Babl  # noqa: E402

_FORMAT_NAME = "R'G'B' u8"


def _fmt():
    return Babl.format(_FORMAT_NAME)


def color_from_rgb8(rgb) -> "Gegl.Color":
    """sRGB byte triple -> GeglColor (byte-exact)."""
    color = Gegl.Color.new('black')
    color.set_bytes(_fmt(), GLib.Bytes.new(bytes(int(v) for v in rgb[:3])))
    return color


def rgb8_from_color(color) -> tuple:
    """GeglColor -> sRGB byte triple (byte-exact)."""
    data = bytes(color.get_bytes(_fmt()).get_data())
    return (data[0], data[1], data[2])


def make_gimp_palette(name, colors, table=None, animated=None):
    """Create a GIMP palette resource; entry names carry material and ANIM.

    e.g. ``129 UNDEF ANIM`` — the toolkit recovers the material table and the
    animated set from these names later.
    """
    gi.require_version('Gimp', '3.0')
    from gi.repository import Gimp
    from .material import index_info
    from .defaults import ANIMATED_INDICES

    animated = ANIMATED_INDICES if animated is None else animated
    gimp_palette = Gimp.Palette.new(name)
    for i, rgb in enumerate(colors[:256]):
        info = index_info(i, table)
        suffix = ' ANIM' if i in animated else ''
        gimp_palette.add_entry(f"{i:03d} {info.material_name}{suffix}",
                               color_from_rgb8(rgb))
    return gimp_palette
