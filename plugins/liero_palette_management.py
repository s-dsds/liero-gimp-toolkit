#!/usr/bin/env python3
"""GIMP 3 plug-in draft: Liero Palette Management.

This is intentionally a first iteration. The reusable core is tested outside GIMP;
this script demonstrates the GIMP-side shape and may need small adjustments for
your local GIMP 3.2 Python GI environment.
"""

import sys
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    from gi.repository import Gimp, GimpUi, Gio, GLib
except Exception:
    Gimp = GimpUi = Gio = GLib = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from liero_core.palette import read_gpl, load_indexed_png_palette, Palette
from liero_core.material import index_info, indices_for_material
from liero_core.defaults import MATERIAL


def export_material_palettes(source_path: str, output_dir: str) -> None:
    path = Path(source_path)
    pal = load_indexed_png_palette(path) if path.suffix.lower() == '.png' else read_gpl(path).padded256()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, mat in MATERIAL.items():
        idxs = indices_for_material(mat)
        Palette(f"{pal.name}-{name}", [pal.colors[i] for i in idxs]).to_gpl(out / f"{pal.name}-{name}.gpl")


if Gimp is not None:
    class LieroPaletteManagement(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return ['python-fu-liero-palette-export-by-material']

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('*')
            proc.set_menu_label('Export Liero Palettes by Material...')
            proc.add_menu_path('<Image>/Liero/Palette')
            proc.set_documentation('Split a classic Liero palette into material palettes.', 'Exports GPL files by hardcoded Liero material table.', name)
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            # File/folder args vary across GI builds; keep the first iteration simple.
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            # Placeholder UI: use CLI for v0.1. Next iteration should add GimpUi.ProcedureDialog fields.
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    Gimp.main(LieroPaletteManagement.__gtype__, sys.argv)
