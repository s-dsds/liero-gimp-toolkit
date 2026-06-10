#!/usr/bin/env python3
"""GIMP 3 plug-in draft: Liero Palette Lab.

Floating GTK UI concept for palette manipulation with preview. This draft includes
safe color transform functions and the plug-in shell; the live preview image
plumbing should be filled in after testing against your GIMP 3.2 build.
"""
from __future__ import annotations
import colorsys, sys
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gimp, GimpUi, Gtk, GLib
except Exception:
    Gimp = GimpUi = Gtk = GLib = None

PLUGIN_DIR = Path(__file__).resolve().parent
CORE_DIR = PLUGIN_DIR.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from liero_core.material import indices_for_material
from liero_core.defaults import MATERIAL, PROTECTED_BY_DEFAULT


def clamp8(x: float) -> int:
    return max(0, min(255, round(x)))


def adjust_rgb(rgb, hue_degrees=0.0, saturation=1.0, brightness=0.0, contrast=1.0):
    r, g, b = [v / 255.0 for v in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + hue_degrees / 360.0) % 1.0
    s = max(0.0, min(1.0, s * saturation))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    vals = []
    for v in (r * 255.0, g * 255.0, b * 255.0):
        v = (v - 127.5) * contrast + 127.5 + brightness
        vals.append(clamp8(v))
    return tuple(vals)


def adjusted_palette(colors, indices, **kwargs):
    out = list(colors)
    for i in indices:
        if i in PROTECTED_BY_DEFAULT:
            continue
        out[i] = adjust_rgb(out[i], **kwargs)
    return out


if Gimp is not None:
    class LieroPaletteLab(Gimp.PlugIn):
        def do_query_procedures(self):
            return ['python-fu-liero-palette-lab']

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('*')
            proc.set_menu_label('Liero Palette Lab...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation('Manipulate material palette ranges with preview.', 'First iteration UI shell.', name)
            proc.set_attribution('AB Tasty AI / generated starter', 'OpenAI', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            if run_mode == Gimp.RunMode.INTERACTIVE:
                GimpUi.init('python-fu-liero-palette-lab')
                dialog = Gtk.Dialog(title='Liero Palette Lab v0.1')
                dialog.add_button('_Close', Gtk.ResponseType.CLOSE)
                box = dialog.get_content_area()
                box.add(Gtk.Label(label='First iteration: palette transform core is present; live preview UI is next.'))
                dialog.show_all()
                dialog.run()
                dialog.destroy()
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    Gimp.main(LieroPaletteLab.__gtype__, sys.argv)
