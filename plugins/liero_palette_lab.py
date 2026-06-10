#!/usr/bin/env python3
"""GIMP 3 plug-in: Liero Palette Lab.

Adjust palette ranges of an indexed image with a live in-dialog preview.

- Palette source: the image colormap or any GIMP palette (toolkit palettes
  carry materials in entry names like ``042 ROCK``).
- Preview canvas: the current image by default; load any .lev map, .wlsprt
  sprite sheet or indexed .png to see how the palette fits other assets.
- Sliders (hue/saturation/brightness/contrast/temperature + colorize mode)
  act on the grid selection; *Commit* chains adjustments; *Make gradient*
  re-ramps a selection between its end colors.
- Apply & Close writes the palette back to the original image with colors
  made unique (GIMP needs distinct colormap entries), and keeps a GIMP
  palette resource.
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
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gimp, GimpUi, Gegl, GLib, Gtk, GdkPixbuf, Gdk, Gio
    import cairo
except Exception:
    Gimp = GimpUi = Gegl = GLib = Gtk = GdkPixbuf = Gdk = Gio = cairo = None

# liero_core sits next to the plugin file when installed, one level up in the repo.
PLUGIN_DIR = Path(__file__).resolve().parent
for _candidate in (PLUGIN_DIR, PLUGIN_DIR.parent):
    if (_candidate / 'liero_core').is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from liero_core.colorops import (adjusted_palette_f, gradient_palette_f, quantize,
                                 to_float, uniquify_palette)
from liero_core.defaults import MATERIAL, DEFAULT_MATERIALS, ANIMATED_INDICES
from liero_core.formats import read_lev_pixels, wlsprt_sheet
from liero_core.material import index_info, materials_from_entry_names

PROC_LAB = 'python-fu-liero-palette-lab'

PREVIEW_W, PREVIEW_H = 504, 350  # classic Liero map size

SLIDERS = [
    # (key, label, min, max, step, default)
    ('hue_degrees', 'Hue shift (absolute in colorize mode)', -180.0, 180.0, 1.0, 0.0),
    ('saturation', 'Saturation (absolute 0-1 in colorize mode)', 0.0, 3.0, 0.01, 1.0),
    ('brightness', 'Brightness', -128.0, 128.0, 1.0, 0.0),
    ('contrast', 'Contrast', 0.2, 3.0, 0.01, 1.0),
    ('temperature', 'Temperature (warm/cool)', -100.0, 100.0, 1.0, 0.0),
]


def colors_from_gimp_palette(gimp_palette):
    out = []
    for color in gimp_palette.get_colors():
        r, g, b, _a = color.get_rgba()
        out.append((round(r * 255), round(g * 255), round(b * 255)))
    while len(out) < 256:
        out.append((0, 0, 0))
    return out[:256]


def map_rgb_to_indices(data, bpp, palette_colors):
    """Map raw RGB(A) bytes to palette indices (exact first, then nearest)."""
    lookup = {}
    for i, c in enumerate(palette_colors):
        lookup.setdefault(tuple(c), i)
    cache = dict(lookup)

    def nearest(rgb):
        r, g, b = rgb
        best, best_d = 0, 1 << 30
        for i, (pr, pg, pb) in enumerate(palette_colors):
            d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
            if d < best_d:
                best, best_d = i, d
        return best

    out = bytearray(len(data) // bpp)
    for p in range(len(out)):
        o = p * bpp
        rgb = (data[o], data[o + 1], data[o + 2])
        idx = cache.get(rgb)
        if idx is None:
            idx = nearest(rgb)
            cache[rgb] = idx
        out[p] = idx
    return bytes(out)


if Gimp is not None:
    from liero_core.palette_grid import PaletteGrid

    class PreviewCanvas:
        """Renders indexed pixels with the current palette, scaled to fit."""

        def __init__(self):
            self.width = 0
            self.height = 0
            self.indices = b''
            self._pixbuf = None
            self.widget = Gtk.DrawingArea()
            self.widget.set_size_request(PREVIEW_W, PREVIEW_H)
            self.widget.connect('draw', self._on_draw)

        def set_pixels(self, width, height, indices):
            self.width, self.height, self.indices = width, height, indices

        def render(self, colors):
            if not self.indices:
                return
            tables = []
            for ch in range(3):
                tables.append(bytes(colors[i][ch] if i < len(colors) else 0
                                    for i in range(256)))
            n = self.width * self.height
            rgb = bytearray(n * 3)
            rgb[0::3] = self.indices.translate(tables[0])
            rgb[1::3] = self.indices.translate(tables[1])
            rgb[2::3] = self.indices.translate(tables[2])
            self._pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(bytes(rgb)), GdkPixbuf.Colorspace.RGB, False, 8,
                self.width, self.height, self.width * 3)
            self.widget.queue_draw()

        def _on_draw(self, widget, cr):
            if self._pixbuf is None:
                return False
            alloc = widget.get_allocation()
            scale = min(alloc.width / self.width, alloc.height / self.height)
            cr.scale(scale, scale)
            Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, 0, 0)
            cr.get_source().set_filter(cairo.Filter.NEAREST)
            cr.paint()
            return False


    class PaletteLabDialog:
        def __init__(self, image):
            self.image = image
            self.table = list(DEFAULT_MATERIALS)
            base8 = colors_from_gimp_palette(image.get_palette())
            self.base = to_float(base8)          # committed state, floats
            self.preview = list(self.base)       # committed + live sliders

            self.grid = PaletteGrid(base8, self.table,
                                    hover_cb=self._update_info,
                                    select_cb=lambda idx: self._recompute())
            self.canvas = PreviewCanvas()

            self.dialog = GimpUi.Dialog(title='Liero Palette Lab')
            self.dialog.add_button('_Cancel', Gtk.ResponseType.CANCEL)
            self.dialog.add_button('_Apply & Close', Gtk.ResponseType.OK)
            self.dialog.set_default_response(Gtk.ResponseType.OK)

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin=12)
            self.dialog.get_content_area().add(hbox)

            # left: grid + palette source
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hbox.pack_start(left, False, False, 0)
            frame = Gtk.Frame()
            frame.add(self.grid.widget)
            left.pack_start(frame, False, False, 0)
            left.pack_start(Gtk.Label(label='Palette source:', xalign=0), False, False, 0)
            self.source_combo = Gtk.ComboBoxText()
            self.source_combo.append('__image__', 'Image colormap (no material info)')
            for pal in Gimp.palettes_get_list(''):
                if pal.get_color_count() == 256:
                    self.source_combo.append(pal.get_name(), pal.get_name())
            self.source_combo.set_active_id('__image__')
            self.source_combo.connect('changed', self._on_source_changed)
            left.pack_start(self.source_combo, False, False, 0)

            # middle: controls
            side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hbox.pack_start(side, False, False, 0)

            self.info = Gtk.Label(xalign=0)
            self.info.set_line_wrap(True)
            self.info.set_size_request(300, -1)
            side.pack_start(self.info, False, False, 0)
            side.pack_start(Gtk.Separator(), False, False, 4)

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

            self.colorize = Gtk.CheckButton(label='Colorize (absolute hue/saturation — colors grays)')
            self.colorize.connect('toggled', lambda _b: self._recompute())
            side.pack_start(self.colorize, False, False, 0)

            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            commit = Gtk.Button(label='Commit')
            commit.connect('clicked', self._on_commit)
            btn_row.pack_start(commit, True, True, 0)
            gradient = Gtk.Button(label='Make gradient')
            gradient.set_tooltip_text('Re-ramp the selection: keep first and last '
                                      'selected colors, interpolate the rest')
            gradient.connect('clicked', self._on_gradient)
            btn_row.pack_start(gradient, True, True, 0)
            side.pack_start(btn_row, False, False, 0)

            btn_row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            reset_sliders = Gtk.Button(label='Reset sliders')
            reset_sliders.connect('clicked', self._on_reset_sliders)
            btn_row2.pack_start(reset_sliders, True, True, 0)
            reset_all = Gtk.Button(label='Reset all')
            reset_all.connect('clicked', self._on_reset_all)
            btn_row2.pack_start(reset_all, True, True, 0)
            side.pack_start(btn_row2, False, False, 0)

            tip = Gtk.Label(xalign=0)
            tip.set_markup('<small>Sliders act on the selection. Commit, then select\n'
                           'other indices to chain adjustments. Colors are made\n'
                           'unique when applied.</small>')
            side.pack_end(tip, False, False, 0)

            # right: preview
            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            hbox.pack_start(right, True, True, 0)
            pframe = Gtk.Frame()
            pframe.add(self.canvas.widget)
            right.pack_start(pframe, True, True, 0)
            load_btn = Gtk.Button(label='Load preview file… (.lev / .wlsprt / indexed .png)')
            load_btn.connect('clicked', self._on_load_preview)
            right.pack_start(load_btn, False, False, 0)

            self._load_preview_from_image(image)
            self._recompute()
            self._update_info()

        # -- preview sources ----------------------------------------------------

        def _load_preview_from_image(self, image):
            w0, h0 = image.get_width(), image.get_height()
            scale = min(1.0, PREVIEW_W / w0, PREVIEW_H / h0)
            w, h = max(1, int(w0 * scale)), max(1, int(h0 * scale))
            data, w, h, bpp = image.get_thumbnail_data(w, h)
            base8 = quantize(self.base)
            self.canvas.set_pixels(w, h, map_rgb_to_indices(bytes(data.get_data()), bpp, base8))

        def _on_load_preview(self, _btn):
            chooser = Gtk.FileChooserDialog(title='Load preview source',
                                            transient_for=self.dialog,
                                            action=Gtk.FileChooserAction.OPEN)
            chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
            chooser.add_button('_Open', Gtk.ResponseType.OK)
            flt = Gtk.FileFilter()
            flt.set_name('Liero maps & sprites (*.lev, *.wlsprt, *.png)')
            for pattern in ('*.lev', '*.wlsprt', '*.png'):
                flt.add_pattern(pattern)
                flt.add_pattern(pattern.upper())
            chooser.add_filter(flt)
            if chooser.run() == Gtk.ResponseType.OK:
                path = Path(chooser.get_filename())
                chooser.destroy()
                try:
                    self._load_preview_file(path)
                    self._render_preview()
                except Exception as exc:
                    traceback.print_exc()
                    self.info.set_text(f"Preview load error: {exc}")
            else:
                chooser.destroy()

        def _load_preview_file(self, path):
            suffix = path.suffix.lower()
            if suffix == '.lev':
                self.canvas.set_pixels(*read_lev_pixels(path))
            elif suffix == '.wlsprt':
                self.canvas.set_pixels(*wlsprt_sheet(path, sheet_width=PREVIEW_W))
            elif suffix == '.png':
                img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                     Gio.File.new_for_path(str(path)))
                try:
                    if img.get_base_type() != Gimp.ImageBaseType.INDEXED:
                        raise ValueError('PNG is not indexed')
                    own_pal = colors_from_gimp_palette(img.get_palette())
                    w0, h0 = img.get_width(), img.get_height()
                    scale = min(1.0, PREVIEW_W / w0, PREVIEW_H / h0)
                    w, h = max(1, int(w0 * scale)), max(1, int(h0 * scale))
                    data, w, h, bpp = img.get_thumbnail_data(w, h)
                    self.canvas.set_pixels(
                        w, h, map_rgb_to_indices(bytes(data.get_data()), bpp, own_pal))
                finally:
                    img.delete()
            else:
                raise ValueError(f'Unsupported preview source: {path}')

        # -- transform pipeline ---------------------------------------------------

        def _locked(self):
            if self.lock_worm.get_active():
                return {i for i, m in enumerate(self.table) if m == MATERIAL['WORM']}
            return set()

        def _slider_kwargs(self):
            kwargs = {s[0]: self.adjustments[s[0]].get_value() for s in SLIDERS}
            if self.colorize.get_active():
                kwargs['colorize'] = True
                kwargs['saturation'] = min(1.0, kwargs['saturation'])
            return kwargs

        def _recompute(self):
            self.preview = adjusted_palette_f(self.base, self.grid.selected,
                                              locked=self._locked(),
                                              **self._slider_kwargs())
            self._render_preview()
            self._update_info()

        def _render_preview(self):
            colors8 = quantize(self.preview)
            self.grid.colors = list(colors8)
            self.grid.queue_draw()
            self.canvas.render(colors8)

        # -- selection helpers ------------------------------------------------------

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

        def _on_source_changed(self, _combo):
            source = self.source_combo.get_active_id()
            if source == '__image__':
                base8 = colors_from_gimp_palette(self.image.get_palette())
                self.table = list(DEFAULT_MATERIALS)
            else:
                pals = Gimp.palettes_get_list(source)
                pal = next((p for p in pals if p.get_name() == source), None)
                if pal is None:
                    return
                base8 = colors_from_gimp_palette(pal)
                names = [pal.get_entry_name(i)[1] for i in range(pal.get_color_count())]
                self.table = materials_from_entry_names(names) or list(DEFAULT_MATERIALS)
            self.base = to_float(base8)
            self.grid.table = list(self.table)
            self._reset_sliders_silent()
            self._recompute()

        # -- buttons -------------------------------------------------------------------

        def _reset_sliders_silent(self):
            for key, _label, _lo, _hi, _step, default in SLIDERS:
                self.adjustments[key].set_value(default)
            self.colorize.set_active(False)

        def _on_commit(self, _btn):
            self.base = list(self.preview)
            self._reset_sliders_silent()
            self._recompute()

        def _on_gradient(self, _btn):
            self.base = gradient_palette_f(self.base, self.grid.selected,
                                           locked=self._locked())
            self._recompute()

        def _on_reset_sliders(self, _btn):
            self._reset_sliders_silent()
            self._recompute()

        def _on_reset_all(self, _btn):
            self.source_combo.set_active_id('__image__')
            base8 = colors_from_gimp_palette(self.image.get_palette())
            self.base = to_float(base8)
            self.table = list(DEFAULT_MATERIALS)
            self.grid.table = list(self.table)
            self._reset_sliders_silent()
            self._recompute()

        def _update_info(self, idx=None):
            lines = []
            if idx is not None:
                info = index_info(idx, self.table)
                r, g, b = self.grid.colors[idx]
                lines.append(f"Index {idx}  #{r:02x}{g:02x}{b:02x}  {info.material_name}"
                             + ('  [animated]' if info.animated else ''))
            else:
                lines.append('Hover a swatch for details.')
            lines.append(f"Selected: {len(self.grid.selected)}")
            self.info.set_text("\n".join(lines))

        # -- lifecycle ---------------------------------------------------------------

        def run(self):
            self.dialog.show_all()
            response = self.dialog.run()
            applied = False
            try:
                if response == Gtk.ResponseType.OK:
                    final = uniquify_palette(quantize(self.preview))
                    gimp_palette = Gimp.Palette.new(
                        f"{self.image.get_name() or 'image'} (Palette Lab)")
                    color = Gegl.Color.new('black')
                    for i, (r, g, b) in enumerate(final):
                        color.set_rgba(r / 255.0, g / 255.0, b / 255.0, 1.0)
                        gimp_palette.add_entry(
                            f"{i:03d} {index_info(i, self.table).material_name}", color)
                    self.image.set_palette(gimp_palette)
                    Gimp.displays_flush()
                    applied = True
            finally:
                self.dialog.destroy()
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
                'Hue/saturation/brightness/contrast/temperature (+ colorize and '
                'gradient tools) on selected indices or materials; in-dialog '
                'preview of the image or any .lev/.wlsprt/.png; applies unique '
                'colors to the original on confirm.',
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
                lab.run()
                return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())
            except Exception as exc:
                traceback.print_exc()
                Gimp.message(f"Palette Lab failed: {exc}")
                return procedure.new_return_values(
                    Gimp.PDBStatusType.EXECUTION_ERROR, GLib.Error(str(exc)))

    Gimp.main(LieroPaletteLab.__gtype__, sys.argv)
