#!/usr/bin/env python3
"""GIMP 3 plug-in: Liero Palette Lab.

Adjust palette ranges (hue/saturation/brightness/contrast) of an indexed
image with a live preview. The Lab duplicates the image and shows the
duplicate in a new display; slider changes update only the duplicate's
colormap, so the preview is instant and the original stays untouched until
Apply.

Workflow: select indices on the grid (or by material / animated), move the
sliders, *Commit* to keep the adjustment and continue on another selection,
*Apply & Close* to write the result back to the original image.
"""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    gi.require_version('Gegl', '0.4')
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gimp, GimpUi, Gegl, GLib, Gtk
except Exception:
    Gimp = GimpUi = Gegl = GLib = Gtk = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from liero_core.colorops import adjusted_palette
from liero_core.defaults import MATERIAL, DEFAULT_MATERIALS, ANIMATED_INDICES
from liero_core.material import index_info

PROC_LAB = 'python-fu-liero-palette-lab'

SLIDERS = [
    # (key, label, min, max, step, default)
    ('hue_degrees', 'Hue shift', -180.0, 180.0, 1.0, 0.0),
    ('saturation', 'Saturation', 0.0, 3.0, 0.01, 1.0),
    ('brightness', 'Brightness', -128.0, 128.0, 1.0, 0.0),
    ('contrast', 'Contrast', 0.2, 3.0, 0.01, 1.0),
]


def colors_from_image(image):
    out = []
    for color in image.get_palette().get_colors():
        r, g, b, _a = color.get_rgba()
        out.append((round(r * 255), round(g * 255), round(b * 255)))
    while len(out) < 256:
        out.append((0, 0, 0))
    return out[:256]


if Gimp is not None:
    from liero_core.palette_grid import PaletteGrid

    class PaletteLabDialog:
        def __init__(self, image):
            self.image = image
            self.base = colors_from_image(image)      # committed state
            self.preview_colors = list(self.base)     # base + live sliders
            self._pushed = list(self.base)            # last colors sent to preview image
            self._flush_pending = False

            # note: GIMP 3.2 has no Image.set_name(); the duplicate shows as
            # "Untitled copy" which is fine for a throwaway preview
            self.preview_image = image.duplicate()
            self.display = Gimp.Display.new(self.preview_image)
            Gimp.displays_flush()

            self.grid = PaletteGrid(self.base, DEFAULT_MATERIALS,
                                    hover_cb=self._update_info,
                                    select_cb=self._on_select)

            self.dialog = GimpUi.Dialog(title='Liero Palette Lab')
            self.dialog.add_button('_Cancel', Gtk.ResponseType.CANCEL)
            self.dialog.add_button('_Apply & Close', Gtk.ResponseType.OK)
            self.dialog.set_default_response(Gtk.ResponseType.OK)

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin=12)
            self.dialog.get_content_area().add(hbox)
            frame = Gtk.Frame()
            frame.add(self.grid.widget)
            hbox.pack_start(frame, False, False, 0)

            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hbox.pack_start(side, True, True, 0)

            self.info = Gtk.Label(xalign=0)
            self.info.set_line_wrap(True)
            self.info.set_size_request(300, -1)
            side.pack_start(self.info, False, False, 0)
            side.pack_start(Gtk.Separator(), False, False, 4)

            # selection helpers
            sel_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            self.material_combo = Gtk.ComboBoxText()
            for mat_name in MATERIAL:
                self.material_combo.append(str(MATERIAL[mat_name]), mat_name)
            self.material_combo.set_active(0)
            sel_row.pack_start(self.material_combo, True, True, 0)
            sel_btn = Gtk.Button(label='Select material')
            sel_btn.connect('clicked', self._on_select_material)
            sel_row.pack_start(sel_btn, False, False, 0)
            side.pack_start(sel_row, False, False, 0)

            extra_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            for label, handler in (('Animated', self._on_select_animated),
                                   ('Invert', self._on_invert),
                                   ('All', self._on_select_all),
                                   ('Clear', self._on_clear)):
                btn = Gtk.Button(label=label)
                btn.connect('clicked', handler)
                extra_row.pack_start(btn, True, True, 0)
            side.pack_start(extra_row, False, False, 0)

            self.lock_worm = Gtk.CheckButton(label='Lock worm indices')
            self.lock_worm.set_active(True)
            self.lock_worm.connect('toggled', lambda _b: self._recompute())
            side.pack_start(self.lock_worm, False, False, 0)
            side.pack_start(Gtk.Separator(), False, False, 4)

            # sliders
            self.adjustments = {}
            for key, label, lo, hi, step, default in SLIDERS:
                side.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
                adj = Gtk.Adjustment(value=default, lower=lo, upper=hi,
                                     step_increment=step, page_increment=step * 10)
                scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
                scale.set_digits(2 if step < 1 else 0)
                scale.set_value_pos(Gtk.PositionType.RIGHT)
                adj.connect('value-changed', lambda _a: self._recompute())
                self.adjustments[key] = adj
                side.pack_start(scale, False, False, 0)

            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            commit = Gtk.Button(label='Commit adjustments')
            commit.connect('clicked', self._on_commit)
            btn_row.pack_start(commit, True, True, 0)
            reset_sliders = Gtk.Button(label='Reset sliders')
            reset_sliders.connect('clicked', self._on_reset_sliders)
            btn_row.pack_start(reset_sliders, True, True, 0)
            reset_all = Gtk.Button(label='Reset all')
            reset_all.connect('clicked', self._on_reset_all)
            btn_row.pack_start(reset_all, True, True, 0)
            side.pack_start(btn_row, False, False, 0)

            tip = Gtk.Label(xalign=0)
            tip.set_markup('<small>Sliders act on the selection (live preview on the '
                           'duplicate image).\nCommit, then select other indices to '
                           'chain adjustments.</small>')
            side.pack_end(tip, False, False, 0)

            self._update_info()

        # -- transform pipeline -------------------------------------------------

        def _locked(self):
            if self.lock_worm.get_active():
                return {i for i, m in enumerate(DEFAULT_MATERIALS) if m == MATERIAL['WORM']}
            return set()

        def _recompute(self):
            kwargs = {s[0]: self.adjustments[s[0]].get_value() for s in SLIDERS}
            self.preview_colors = adjusted_palette(
                self.base, self.grid.selected, locked=self._locked(), **kwargs)
            self.grid.colors = list(self.preview_colors)
            self.grid.queue_draw()
            self._update_info()
            if not self._flush_pending:
                self._flush_pending = True
                GLib.timeout_add(150, self._flush_preview)

        def _flush_preview(self):
            self._flush_pending = False
            try:
                pal = self.preview_image.get_palette()
                color = Gegl.Color.new('black')
                for i, rgb in enumerate(self.preview_colors):
                    if rgb != self._pushed[i]:
                        color.set_rgba(rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0)
                        pal.set_entry_color(i, color)
                        self._pushed[i] = rgb
                Gimp.displays_flush()
            except Exception:
                traceback.print_exc()
            return False  # one-shot timeout

        # -- selection helpers ---------------------------------------------------

        def _on_select(self, idx):
            self._recompute()

        def _on_select_material(self, _btn):
            self.grid.select_material(int(self.material_combo.get_active_id()))
            self._recompute()

        def _on_select_animated(self, _btn):
            self.grid.selected = set(ANIMATED_INDICES)
            self.grid.queue_draw()
            self._recompute()

        def _on_invert(self, _btn):
            self.grid.selected = set(range(256)) - self.grid.selected
            self.grid.queue_draw()
            self._recompute()

        def _on_select_all(self, _btn):
            self.grid.selected = set(range(256))
            self.grid.queue_draw()
            self._recompute()

        def _on_clear(self, _btn):
            self.grid.clear_selection()
            self._recompute()

        # -- buttons --------------------------------------------------------------

        def _reset_sliders_silent(self):
            for key, _label, _lo, _hi, _step, default in SLIDERS:
                self.adjustments[key].set_value(default)

        def _on_commit(self, _btn):
            self.base = list(self.preview_colors)
            self._reset_sliders_silent()
            self._recompute()

        def _on_reset_sliders(self, _btn):
            self._reset_sliders_silent()
            self._recompute()

        def _on_reset_all(self, _btn):
            self.base = colors_from_image(self.image)
            self._reset_sliders_silent()
            self._recompute()

        def _update_info(self, idx=None):
            lines = []
            if idx is not None:
                info = index_info(idx)
                r, g, b = self.grid.colors[idx]
                lines.append(f"Index {idx}  #{r:02x}{g:02x}{b:02x}  {info.material_name}"
                             + ('  [animated]' if info.animated else ''))
            else:
                lines.append('Hover a swatch for details.')
            lines.append(f"Selected: {len(self.grid.selected)}")
            self.info.set_text("\n".join(lines))

        # -- lifecycle -------------------------------------------------------------

        def run(self):
            self.dialog.show_all()
            response = self.dialog.run()
            applied = False
            try:
                if response == Gtk.ResponseType.OK:
                    final = list(self.preview_colors)
                    gimp_palette = Gimp.Palette.new(
                        f"{self.image.get_name() or 'image'} (Palette Lab)")
                    color = Gegl.Color.new('black')
                    for i, (r, g, b) in enumerate(final):
                        color.set_rgba(r / 255.0, g / 255.0, b / 255.0, 1.0)
                        gimp_palette.add_entry(f"{i:03d} {index_info(i).material_name}", color)
                    # keep the palette resource: applying also yields a palette
                    self.image.set_palette(gimp_palette)
                    applied = True
            finally:
                self.dialog.destroy()
                try:
                    self.display.delete()
                except Exception:
                    traceback.print_exc()
                Gimp.displays_flush()
            return applied


    class LieroPaletteLab(Gimp.PlugIn):
        def do_set_i18n(self, name):
            return False

        def do_query_procedures(self):
            return [PROC_LAB]

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('INDEXED')
            proc.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)
            proc.set_menu_label('Palette Lab...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation(
                'Adjust palette ranges of an indexed image with live preview.',
                'Hue/saturation/brightness/contrast on selected indices or materials; '
                'previews on a duplicate image, applies to the original on confirm.',
                name)
            proc.set_attribution('liero-gimp-toolkit', 'liero-gimp-toolkit', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            try:
                if run_mode != Gimp.RunMode.INTERACTIVE:
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.CALLING_ERROR,
                        GLib.Error('Palette Lab is interactive only.'))
                if image is None or image.get_base_type() != Gimp.ImageBaseType.INDEXED:
                    Gimp.message('Palette Lab needs an indexed image (Image > Mode > Indexed).')
                    return procedure.new_return_values(
                        Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error('not indexed'))
                GimpUi.init(PROC_LAB)
                lab = PaletteLabDialog(image)
                if lab.run():
                    Gimp.displays_flush()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                Gimp.message(f"Palette Lab failed: {exc}")
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))

    Gimp.main(LieroPaletteLab.__gtype__, sys.argv)
