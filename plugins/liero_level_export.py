#!/usr/bin/env python3
"""GIMP 3 plug-in: export an OpenLiero MODERNLV level.

Thin shell around liero_core.level_export_gimp. The active RGB image is the
display/map; you pick a separate indexed image as the material mask (e.g. the
Quantize-by-Material output), optionally add a ramps.json + anim layer, preview
(display / material mask / live animation), and write a .lev.
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

PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

PROC_EXPORT = 'python-fu-liero-level-export'

if Gimp is not None:
    class LieroLevelExport(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return [PROC_EXPORT]

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('RGB*')
            proc.set_menu_label('Export OpenLiero Level...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation(
                'Export an OpenLiero MODERNLV .lev from this RGB map plus an '
                'indexed material mask.',
                'The active RGB image is the display/map; pick a separate '
                'indexed image as the material mask, optionally add ramps + an '
                'anim layer, preview, and write a .lev (verified byte-identical '
                'to OpenLiero lev_gen.py).',
                name)
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode != Gimp.RunMode.INTERACTIVE:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CALLING_ERROR,
                        GLib.Error('Export OpenLiero Level is interactive only.'))
                if image is None:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error('no image'))
                GimpUi.init(PROC_EXPORT)
                from liero_core.level_export_gimp import LevelExportDialog
                LevelExportDialog(image).run()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                Gimp.message(f"Export OpenLiero Level failed: {exc}")
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))

    Gimp.main(LieroLevelExport.__gtype__, sys.argv)
