#!/usr/bin/env python3
"""GIMP 3 plug-in: Liero material-aware quantization.

Thin shell around liero_core.quantize_gimp: scans layer groups named by
material (rock, dirt, background dirt, worm, ...), quantizes each material's
pixels into the base palette (reusing close same-material colors, allocating
new ones from the 188-235 replacement pool), and produces a new indexed image
plus the forked palette.
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

PROC_QUANTIZE = 'python-fu-liero-material-quantize'

if Gimp is not None:
    class LieroMaterialQuantize(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return [PROC_QUANTIZE]

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('RGB*')
            proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            proc.set_menu_label('Quantize by Material...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation(
                'Quantize RGB layer groups into a forked Liero palette.',
                'Scans layer/group names for materials, quantizes each material '
                'into the base palette (reusing close same-material colors, '
                'allocating from the 188-235 pool), and creates a new indexed '
                'image plus the forked palette with material entry names.',
                name)
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode != Gimp.RunMode.INTERACTIVE:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CALLING_ERROR,
                        GLib.Error('Quantize by Material is interactive only.'))
                if image is None:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error('no image'))
                GimpUi.init(PROC_QUANTIZE)
                from liero_core.quantize_gimp import QuantizeDialog
                QuantizeDialog(image).run()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                Gimp.message(f"Quantize by Material failed: {exc}")
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))

    Gimp.main(LieroMaterialQuantize.__gtype__, sys.argv)
