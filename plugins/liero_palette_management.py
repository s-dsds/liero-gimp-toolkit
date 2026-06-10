#!/usr/bin/env python3
"""GIMP 3 plug-in: Liero Palette Management.

Procedures (menu Image > Liero > Palette):
- Import Palette...      any supported source -> a GIMP palette, optionally
                         applied to the active indexed image.
- Export by Material...  palette file or active image -> one .gpl per material
                         plus the full palette.
- Validate Image Palette report on the active image's colormap.

Supported palette sources: .gpl, indexed .png (loaded through GIMP itself),
.lpl, .wlsprt, POWERLEVEL .lev, decompressed LIERO.EXE.
"""
import sys
import traceback
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    gi.require_version('Gegl', '0.4')
    from gi.repository import Gimp, GimpUi, Gegl, Gio, GLib, GObject
except Exception:
    Gimp = GimpUi = Gegl = Gio = GLib = GObject = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from liero_core.palette import Palette
from liero_core.formats import load_palette
from liero_core.material import index_info, indices_for_material, load_material_table
from liero_core.defaults import MATERIAL

PROC_IMPORT = 'python-fu-liero-palette-import'
PROC_EXPORT = 'python-fu-liero-palette-export-by-material'
PROC_VALIDATE = 'python-fu-liero-palette-validate'


def palette_from_gimp_palette(gimp_palette, name=None):
    """Copy a Gimp.Palette into a pure-python Palette."""
    colors = []
    for color in gimp_palette.get_colors():
        r, g, b, _a = color.get_rgba()
        colors.append((round(r * 255), round(g * 255), round(b * 255)))
    return Palette(name or gimp_palette.get_name(), colors).padded256()


def load_palette_for_gimp(path: Path) -> Palette:
    """Like liero_core.formats.load_palette, but indexed PNGs go through GIMP
    (the Flatpak GIMP python has no Pillow)."""
    if path.suffix.lower() == '.png':
        image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, Gio.File.new_for_path(str(path)))
        try:
            if image.get_base_type() != Gimp.ImageBaseType.INDEXED:
                raise ValueError(f"PNG is not indexed/palette mode: {path}")
            return palette_from_gimp_palette(image.get_palette(), name=path.stem)
        finally:
            image.delete()
    return load_palette(path).padded256()


def make_gimp_palette(pal: Palette):
    """Create a new GIMP palette resource from a 256-color Palette."""
    gimp_palette = Gimp.Palette.new(pal.name)
    color = Gegl.Color.new('black')
    for i, (r, g, b) in enumerate(pal.colors[:256]):
        info = index_info(i)
        color.set_rgba(r / 255.0, g / 255.0, b / 255.0, 1.0)
        gimp_palette.add_entry(f"{i:03d} {info.material_name}", color)
    return gimp_palette


def export_material_palettes(pal: Palette, output_dir: Path, table=None):
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    pal.to_gpl(output_dir / f"{pal.name}-full.gpl")
    written.append(f"{pal.name}-full.gpl")
    for mat_name, mat_value in MATERIAL.items():
        idxs = indices_for_material(mat_value, table)
        colors = [pal.colors[i] for i in idxs]
        out = output_dir / f"{pal.name}-{mat_name}.gpl"
        Palette(f"{pal.name}-{mat_name}", colors).to_gpl(out)
        written.append(out.name)
    return written


def validation_report(pal: Palette, table=None) -> str:
    dupes = pal.unique_report()
    lines = [f"Palette: {pal.name}"]
    if table is not None:
        lines.append("Using custom WLE material table.")
    counts = {}
    for i in range(256):
        counts.setdefault(index_info(i, table).material_name, []).append(i)
    lines.append("Material slots: " + ", ".join(
        f"{name}={len(idxs)}" for name, idxs in sorted(counts.items())))
    protected = [i for i in range(256) if index_info(i, table).protected]
    animated = [i for i in range(256) if index_info(i, table).animated]
    lines.append(f"Protected indices: {len(protected)} (worm + animated)")
    lines.append(f"Animated indices: {animated}")
    if dupes:
        lines.append(f"Duplicate RGB values: {len(dupes)}")
        for color, idxs in sorted(dupes.items())[:10]:
            lines.append(f"  #%02x%02x%02x at {idxs}" % color)
        if len(dupes) > 10:
            lines.append(f"  ... and {len(dupes) - 10} more")
    else:
        lines.append("No duplicate RGB values.")
    return "\n".join(lines)


if Gimp is not None:
    class LieroPaletteManagement(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return [PROC_IMPORT, PROC_EXPORT, PROC_VALIDATE]

        def do_create_procedure(self, name):
            if name == PROC_IMPORT:
                proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run_import, None)
                proc.set_menu_label('Import Palette...')
                proc.set_documentation(
                    'Import a Liero palette as a GIMP palette.',
                    'Sources: .gpl, indexed .png, .lpl, .wlsprt, POWERLEVEL .lev, decompressed LIERO.EXE. '
                    'Optionally applies the palette to the active indexed image.',
                    name)
                proc.add_file_argument('file', 'Palette _file',
                                       'Palette source (.gpl/.png/.lpl/.wlsprt/.lev/LIERO.EXE)',
                                       Gimp.FileChooserAction.OPEN, False, None,
                                       GObject.ParamFlags.READWRITE)
                proc.add_string_argument('palette-name', 'Palette _name',
                                         'Name for the new GIMP palette (empty: derive from file)',
                                         '', GObject.ParamFlags.READWRITE)
                proc.add_boolean_argument('apply-to-image', '_Apply to image',
                                          'Also set the active indexed image colormap',
                                          False, GObject.ParamFlags.READWRITE)
            elif name == PROC_EXPORT:
                proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run_export, None)
                proc.set_menu_label('Export by Material...')
                proc.set_documentation(
                    'Split a Liero palette into per-material GPL palettes.',
                    'Uses the selected palette file, or the active indexed image when no file is chosen.',
                    name)
                proc.add_file_argument('file', 'Palette _file (optional)',
                                       'Palette source; leave unset to use the active image colormap',
                                       Gimp.FileChooserAction.OPEN, True, None,
                                       GObject.ParamFlags.READWRITE)
                proc.add_file_argument('output-dir', '_Output folder',
                                       'Folder receiving the .gpl files',
                                       Gimp.FileChooserAction.SELECT_FOLDER, False, None,
                                       GObject.ParamFlags.READWRITE)
                proc.add_file_argument('materials-file', '_Materials table (optional)',
                                       'Custom WLE material table (JSON or JS array file)',
                                       Gimp.FileChooserAction.OPEN, True, None,
                                       GObject.ParamFlags.READWRITE)
            else:
                proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run_validate, None)
                proc.set_menu_label('Validate Image Palette...')
                proc.set_documentation(
                    'Report on the active image colormap using classic Liero material semantics.',
                    'Shows material slot counts, protected/animated indices and duplicate colors. '
                    'A custom WLE material table can replace the classic one.',
                    name)
                proc.add_file_argument('materials-file', '_Materials table (optional)',
                                       'Custom WLE material table (JSON or JS array file)',
                                       Gimp.FileChooserAction.OPEN, True, None,
                                       GObject.ParamFlags.READWRITE)
            proc.set_image_types('*')
            if name == PROC_VALIDATE:
                proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            else:
                proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE
                                          | Gimp.ProcedureSensitivityMask.NO_IMAGE
                                          | Gimp.ProcedureSensitivityMask.NO_DRAWABLES)
            proc.add_menu_path('<Image>/Liero/Palette')
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            return proc

        # --- helpers ---------------------------------------------------------

        def _dialog(self, procedure, config, title):
            GimpUi.init(procedure.get_name())
            dialog = GimpUi.ProcedureDialog.new(procedure, config, title)
            dialog.fill(None)
            ok = dialog.run()
            dialog.destroy()
            return ok

        def _error(self, procedure, message):
            Gimp.message(message)
            return procedure.new_return_values(
                Gimp.PDBStatusType.EXECUTION_ERROR,
                GLib.Error(message))

        @staticmethod
        def _materials_table(config):
            gfile = config.get_property('materials-file')
            if gfile is None:
                return None
            return load_material_table(Path(gfile.get_path()))

        # --- procedures ------------------------------------------------------

        def run_import(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode == Gimp.RunMode.INTERACTIVE:
                    if not self._dialog(procedure, config, 'Import Liero Palette'):
                        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
                gfile = config.get_property('file')
                if gfile is None:
                    return self._error(procedure, 'No palette file selected.')
                path = Path(gfile.get_path())
                pal = load_palette_for_gimp(path)
                name = config.get_property('palette-name').strip() or f"Liero {path.stem}"
                pal = Palette(name, pal.colors)
                gimp_palette = make_gimp_palette(pal)
                applied = ''
                if config.get_property('apply-to-image'):
                    if image is not None and image.get_base_type() == Gimp.ImageBaseType.INDEXED:
                        image.set_palette(gimp_palette)
                        Gimp.displays_flush()
                        applied = ' and applied it to the image colormap'
                    else:
                        applied = ' (NOT applied: active image is not in indexed mode)'
                Gimp.message(f"Imported palette '{name}' ({len(pal.colors)} colors){applied}.")
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                return self._error(procedure, f"Liero palette import failed: {exc}")

        def run_export(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode == Gimp.RunMode.INTERACTIVE:
                    if not self._dialog(procedure, config, 'Export Liero Palettes by Material'):
                        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
                gfile = config.get_property('file')
                outdir = config.get_property('output-dir')
                if outdir is None:
                    return self._error(procedure, 'No output folder selected.')
                if gfile is not None:
                    path = Path(gfile.get_path())
                    pal = load_palette_for_gimp(path)
                elif image is not None and image.get_base_type() == Gimp.ImageBaseType.INDEXED:
                    pal = palette_from_gimp_palette(image.get_palette(),
                                                    name=Path(image.get_name() or 'image').stem)
                else:
                    return self._error(
                        procedure,
                        'Choose a palette file, or run on an indexed image to use its colormap.')
                written = export_material_palettes(pal, Path(outdir.get_path()),
                                                   table=self._materials_table(config))
                Gimp.message(f"Wrote {len(written)} palettes to {outdir.get_path()}:\n" + "\n".join(written))
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                return self._error(procedure, f"Liero palette export failed: {exc}")

        def run_validate(self, procedure, run_mode, image, drawables, config, data):
            try:
                if image is None or image.get_base_type() != Gimp.ImageBaseType.INDEXED:
                    return self._error(procedure, 'Validate needs an indexed image (Image > Mode > Indexed).')
                if run_mode == Gimp.RunMode.INTERACTIVE:
                    if not self._dialog(procedure, config, 'Validate Liero Image Palette'):
                        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
                pal = palette_from_gimp_palette(image.get_palette(),
                                                name=Path(image.get_name() or 'image').stem)
                Gimp.message(validation_report(pal, table=self._materials_table(config)))
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                return self._error(procedure, f"Liero palette validation failed: {exc}")

    Gimp.main(LieroPaletteManagement.__gtype__, sys.argv)
