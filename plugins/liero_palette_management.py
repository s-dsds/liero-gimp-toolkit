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
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gimp, GimpUi, Gegl, Gio, GLib, GObject, Gtk, Gdk
except Exception:
    Gimp = GimpUi = Gegl = Gio = GLib = GObject = Gtk = Gdk = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import json

from liero_core.palette import Palette
from liero_core.formats import load_palette
from liero_core.material import index_info, indices_for_material, load_material_table, material_table_to_js
from liero_core.defaults import MATERIAL, DEFAULT_MATERIALS

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


def make_gimp_palette(pal: Palette, table=None):
    """Create a new GIMP palette resource from a 256-color Palette.

    Entry names carry the per-index material (e.g. ``042 ROCK``)."""
    gimp_palette = Gimp.Palette.new(pal.name)
    color = Gegl.Color.new('black')
    for i, (r, g, b) in enumerate(pal.colors[:256]):
        info = index_info(i, table)
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
    CELL = 30  # swatch size in px; grid is 16x16 cells

    MATERIAL_BADGE = {
        MATERIAL['UNDEF']: '',
        MATERIAL['DIRT']: 'D',
        MATERIAL['DIRT_2']: 'D2',
        MATERIAL['ROCK']: 'R',
        MATERIAL['BG']: 'B',
        MATERIAL['BG_DIRT']: 'BD',
        MATERIAL['BG_DIRT_2']: 'B2',
        MATERIAL['BG_SEESHADOW']: 'S',
        MATERIAL['WORM']: 'W',
    }

    class PaletteMaterialEditor:
        """Palette grid with on-the-fly material editing.

        Click: select. Ctrl+click: toggle. Shift+click: range.
        Double-click: edit the color. Badges show the material of each index;
        a dot marks animated indices.
        """

        def __init__(self, colors, table, name, apply_default, can_apply):
            self.colors = list(colors[:256])
            self.table = list(table[:256])
            self.selected = set()
            self.last_click = 0

            self.dialog = GimpUi.Dialog(title='Liero Palette Editor')
            self.dialog.add_button('_Cancel', Gtk.ResponseType.CANCEL)
            self.dialog.add_button('Create _Palette', Gtk.ResponseType.OK)
            self.dialog.set_default_response(Gtk.ResponseType.OK)

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12,
                           margin=12)
            self.dialog.get_content_area().add(hbox)

            self.area = Gtk.DrawingArea()
            self.area.set_size_request(CELL * 16, CELL * 16)
            self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK
                                 | Gdk.EventMask.POINTER_MOTION_MASK)
            self.area.connect('draw', self._on_draw)
            self.area.connect('button-press-event', self._on_press)
            self.area.connect('motion-notify-event', self._on_motion)
            frame = Gtk.Frame()
            frame.add(self.area)
            hbox.pack_start(frame, False, False, 0)

            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hbox.pack_start(side, True, True, 0)

            self.info = Gtk.Label(xalign=0)
            self.info.set_line_wrap(True)
            self.info.set_size_request(260, -1)
            side.pack_start(self.info, False, False, 0)
            side.pack_start(Gtk.Separator(), False, False, 4)

            self.material_combo = Gtk.ComboBoxText()
            for mat_name in MATERIAL:
                self.material_combo.append(str(MATERIAL[mat_name]), mat_name)
            self.material_combo.set_active(0)
            side.pack_start(self.material_combo, False, False, 0)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            assign = Gtk.Button(label='Assign to selection')
            assign.connect('clicked', self._on_assign)
            btn_box.pack_start(assign, True, True, 0)
            select = Gtk.Button(label='Select material')
            select.connect('clicked', self._on_select_material)
            btn_box.pack_start(select, True, True, 0)
            side.pack_start(btn_box, False, False, 0)

            clear = Gtk.Button(label='Clear selection')
            clear.connect('clicked', self._on_clear)
            side.pack_start(clear, False, False, 0)
            side.pack_start(Gtk.Separator(), False, False, 4)

            side.pack_start(Gtk.Label(label='GIMP palette name:', xalign=0), False, False, 0)
            self.name_entry = Gtk.Entry(text=name)
            side.pack_start(self.name_entry, False, False, 0)
            self.apply_check = Gtk.CheckButton(label='Apply to image colormap')
            self.apply_check.set_active(apply_default and can_apply)
            self.apply_check.set_sensitive(can_apply)
            side.pack_start(self.apply_check, False, False, 0)

            save_mats = Gtk.Button(label='Save materials…')
            save_mats.connect('clicked', self._on_save_materials)
            side.pack_start(save_mats, False, False, 0)

            tip = Gtk.Label(xalign=0)
            tip.set_markup('<small>Click: select · Ctrl: toggle · Shift: range\n'
                           'Double-click: edit color · dot = animated</small>')
            side.pack_end(tip, False, False, 0)

            self._update_info()

        # -- drawing ----------------------------------------------------------

        def _on_draw(self, widget, cr):
            for i, (r, g, b) in enumerate(self.colors):
                x, y = (i % 16) * CELL, (i // 16) * CELL
                cr.set_source_rgb(r / 255.0, g / 255.0, b / 255.0)
                cr.rectangle(x, y, CELL, CELL)
                cr.fill()
                lum = 0.299 * r + 0.587 * g + 0.114 * b
                fg = (0, 0, 0) if lum > 128 else (1, 1, 1)
                badge = MATERIAL_BADGE.get(self.table[i], '?')
                if badge:
                    cr.set_source_rgb(*fg)
                    cr.set_font_size(9)
                    cr.move_to(x + 2, y + CELL - 3)
                    cr.show_text(badge)
                if index_info(i, self.table).animated:
                    cr.set_source_rgb(*fg)
                    cr.arc(x + CELL - 5, y + 5, 2.2, 0, 6.2832)
                    cr.fill()
                if i in self.selected:
                    cr.set_line_width(2)
                    cr.set_source_rgb(1, 1, 1)
                    cr.rectangle(x + 1, y + 1, CELL - 2, CELL - 2)
                    cr.stroke()
                    cr.set_line_width(1)
                    cr.set_source_rgb(0, 0, 0)
                    cr.rectangle(x + 2.5, y + 2.5, CELL - 5, CELL - 5)
                    cr.stroke()
            return False

        # -- events -----------------------------------------------------------

        @staticmethod
        def _event_index(event):
            col = min(15, max(0, int(event.x) // CELL))
            row = min(15, max(0, int(event.y) // CELL))
            return int(row * 16 + col)

        def _on_press(self, widget, event):
            idx = self._event_index(event)
            if event.type == Gdk.EventType._2BUTTON_PRESS:
                self._edit_color(idx)
            elif event.state & Gdk.ModifierType.CONTROL_MASK:
                self.selected.symmetric_difference_update({idx})
            elif event.state & Gdk.ModifierType.SHIFT_MASK:
                lo, hi = sorted((self.last_click, idx))
                self.selected.update(range(lo, hi + 1))
            else:
                self.selected = {idx}
            self.last_click = idx
            self._update_info(idx)
            self.area.queue_draw()
            return True

        def _on_motion(self, widget, event):
            self._update_info(self._event_index(event))
            return False

        # -- actions ----------------------------------------------------------

        def _on_assign(self, _btn):
            value = int(self.material_combo.get_active_id())
            for i in self.selected:
                self.table[i] = value
            self._update_info()
            self.area.queue_draw()

        def _on_select_material(self, _btn):
            value = int(self.material_combo.get_active_id())
            self.selected = {i for i, m in enumerate(self.table) if m == value}
            self._update_info()
            self.area.queue_draw()

        def _on_clear(self, _btn):
            self.selected = set()
            self._update_info()
            self.area.queue_draw()

        def _edit_color(self, idx):
            chooser = Gtk.ColorChooserDialog(title=f'Color for index {idx}',
                                             transient_for=self.dialog)
            chooser.set_use_alpha(False)
            r, g, b = self.colors[idx]
            chooser.set_rgba(Gdk.RGBA(r / 255.0, g / 255.0, b / 255.0, 1.0))
            if chooser.run() == Gtk.ResponseType.OK:
                rgba = chooser.get_rgba()
                self.colors[idx] = (round(rgba.red * 255), round(rgba.green * 255),
                                    round(rgba.blue * 255))
            chooser.destroy()
            self._update_info(idx)
            self.area.queue_draw()

        def _on_save_materials(self, _btn):
            chooser = Gtk.FileChooserDialog(title='Save material table',
                                            transient_for=self.dialog,
                                            action=Gtk.FileChooserAction.SAVE)
            chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
            chooser.add_button('_Save', Gtk.ResponseType.OK)
            chooser.set_do_overwrite_confirmation(True)
            chooser.set_current_name('materials.json')
            for pattern, label in (('*.json', 'JSON array (*.json)'),
                                   ('*.js', 'WLE room-script expression (*.js)')):
                flt = Gtk.FileFilter()
                flt.set_name(label)
                flt.add_pattern(pattern)
                chooser.add_filter(flt)
            if chooser.run() == Gtk.ResponseType.OK:
                out = Path(chooser.get_filename())
                if out.suffix.lower() == '.js':
                    out.write_text("materials: " + material_table_to_js(self.table) + ",\n")
                else:
                    out.write_text(json.dumps(self.table))
                Gimp.message(f"Saved material table to {out}")
            chooser.destroy()

        def _update_info(self, idx=None):
            lines = []
            if idx is not None:
                info = index_info(idx, self.table)
                r, g, b = self.colors[idx]
                flags = []
                if info.animated:
                    flags.append('animated')
                if info.protected:
                    flags.append('protected')
                if info.preferred_replacement_candidate:
                    flags.append('replacement-candidate')
                lines.append(f"Index {idx}  #{r:02x}{g:02x}{b:02x}  {info.material_name}"
                             + (f"  [{', '.join(flags)}]" if flags else ''))
            else:
                lines.append('Hover a swatch for details.')
            lines.append(f"Selected: {len(self.selected)}")
            self.info.set_text("\n".join(lines))

        def run(self):
            self.dialog.show_all()
            response = self.dialog.run()
            result = None
            if response == Gtk.ResponseType.OK:
                result = {
                    'colors': list(self.colors),
                    'table': list(self.table),
                    'name': self.name_entry.get_text().strip(),
                    'apply': self.apply_check.get_active(),
                }
            self.dialog.destroy()
            return result

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
                proc.add_file_argument('materials-file', '_Materials table (optional)',
                                       'Custom WLE material table to start from (JSON or JS array file)',
                                       Gimp.FileChooserAction.OPEN, True, None,
                                       GObject.ParamFlags.READWRITE)
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
                table = self._materials_table(config) or list(DEFAULT_MATERIALS)
                name = config.get_property('palette-name').strip() or f"Liero {path.stem}"
                apply_flag = config.get_property('apply-to-image')
                can_apply = (image is not None
                             and image.get_base_type() == Gimp.ImageBaseType.INDEXED)
                if run_mode == Gimp.RunMode.INTERACTIVE:
                    editor = PaletteMaterialEditor(pal.colors, table, name, apply_flag, can_apply)
                    result = editor.run()
                    if result is None:
                        return procedure.new_return_values(Gimp.PDBStatusType.CANCEL, GLib.Error())
                    pal = Palette(result['name'] or name, result['colors'])
                    table = result['table']
                    apply_flag = result['apply']
                else:
                    pal = Palette(name, pal.colors)
                gimp_palette = make_gimp_palette(pal, table)
                applied = ''
                if apply_flag:
                    if can_apply:
                        image.set_palette(gimp_palette)
                        Gimp.displays_flush()
                        applied = ' and applied it to the image colormap'
                    else:
                        applied = ' (NOT applied: active image is not in indexed mode)'
                Gimp.message(f"Imported palette '{pal.name}' ({len(pal.colors)} colors){applied}.")
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
