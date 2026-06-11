#!/usr/bin/env python3
"""GIMP 3 plug-in: Liero Palette Studio (menu entry: Palette Studio...).

Thin shell around liero_core.studio.PaletteStudioDialog — the merged palette
editor/lab: sources (image colormap, GIMP palettes, palette files), material
and colorAnim editing, range adjustments with live multi-previews, save as
GIMP palette and/or apply to the image.
"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    from gi.repository import Gimp, GimpUi, GLib
except Exception:
    Gimp = GimpUi = GLib = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

PROC_LAB = 'python-fu-liero-palette-lab'

if Gimp is not None:
    class LieroPaletteLab(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return [PROC_LAB]

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('INDEXED')
            proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            proc.set_menu_label('Palette Studio...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation(
                'Edit and adjust Liero palettes with live previews.',
                'Sources: image colormap, GIMP palettes, palette files. Material '
                'and colorAnim editing, hue/saturation/brightness/contrast/'
                'temperature adjustments, gradient tool, multi-previews '
                '(.lev/.wlsprt/.png), apply to image and/or save as GIMP palette.',
                name)
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode != Gimp.RunMode.INTERACTIVE:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CALLING_ERROR,
                        GLib.Error('Palette Studio is interactive only.'))
                if image is None or image.get_base_type() != Gimp.ImageBaseType.INDEXED:
                    Gimp.message('Palette Studio needs an indexed image (Image > Mode > Indexed).')
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error('not indexed'))
                GimpUi.init(PROC_LAB)
                from liero_core.studio import PaletteStudioDialog
                PaletteStudioDialog(
                    image=image,
                    name_hint=f"{image.get_name() or 'image'} palette").run()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                Gimp.message(f"Palette Studio failed: {exc}")
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))

    Gimp.main(LieroPaletteLab.__gtype__, sys.argv)
