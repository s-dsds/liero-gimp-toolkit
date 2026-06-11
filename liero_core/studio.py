"""Liero Palette Studio — the merged palette dialog.

One window for everything palette: load from any source (palette files,
the active image colormap, GIMP palettes), edit materials/animated flags/
colors, adjust ranges with live previews, and save as GIMP palette,
image colormap, or material table files.

GIMP/GTK-dependent: import only from plug-in code.
"""
from __future__ import annotations

import json
import traceback
from pathlib import Path

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('Gtk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gimp, GimpUi, GLib, Gtk, GdkPixbuf, Gdk, Gio  # noqa: E402
import cairo  # noqa: E402

from .colorops import (adjusted_palette_f, gradient_palette_f, quantize,  # noqa: E402
                       to_float, uniquify_palette)
from .defaults import (MATERIAL, MATERIAL_GROUPS, DEFAULT_MATERIALS,  # noqa: E402
                       ANIMATED_INDICES, expand_color_anim)
from .formats import (load_palette, read_exe_color_anim, read_lev_pixels,  # noqa: E402
                      wlsprt_sheet)
from .gimp_colors import color_from_rgb8, rgb8_from_color, make_gimp_palette  # noqa: E402
from .material import (index_info, materials_from_entry_names,  # noqa: E402
                       animated_from_entry_names, indices_to_anim_pairs,
                       material_table_to_js, parse_material_text)
from .palette import Palette  # noqa: E402
from .palette_grid import PaletteGrid  # noqa: E402

PREVIEW_W, PREVIEW_H = 504, 350  # classic Liero map size
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP = 0.25, 8.0, 1.25
FOCUS_COLOR = (255, 0, 255)

SLIDERS = [
    # (key, label, min, max, step, default)
    ('hue_degrees', 'Hue shift (absolute in colorize mode)', -180.0, 180.0, 1.0, 0.0),
    ('saturation', 'Saturation (absolute 0-1 in colorize mode)', 0.0, 3.0, 0.01, 1.0),
    ('brightness', 'Brightness', -128.0, 128.0, 1.0, 0.0),
    ('contrast', 'Contrast', 0.2, 3.0, 0.01, 1.0),
    ('temperature', 'Temperature (warm/cool)', -100.0, 100.0, 1.0, 0.0),
]


def colors_from_gimp_palette(gimp_palette):
    """Byte-exact sRGB colors (get_rgba would return linear = too dark)."""
    out = [rgb8_from_color(c) for c in gimp_palette.get_colors()]
    while len(out) < 256:
        out.append((0, 0, 0))
    return out[:256]


def subsample_indices(width, height, data, max_w, max_h):
    """Nearest-neighbor downscale of an index buffer (never blend indices)."""
    scale = min(1.0, max_w / width, max_h / height)
    if scale >= 1.0:
        return width, height, data
    nw, nh = max(1, int(width * scale)), max(1, int(height * scale))
    out = bytearray(nw * nh)
    for y in range(nh):
        row = int(y / scale) * width
        base = y * nw
        for x in range(nw):
            out[base + x] = data[row + int(x / scale)]
    return nw, nh, bytes(out)


def indices_from_image(image):
    """True palette indices of an indexed image, via the GEGL buffer.

    RGB round-trips are NOT usable: classic palettes carry duplicate RGB
    values across materials, so colors don't identify indices.
    """
    from gi.repository import Gegl
    dup = image.duplicate()
    try:
        layer = dup.flatten()
        w, h = layer.get_width(), layer.get_height()
        rect = Gegl.Rectangle.new(0, 0, w, h)
        data = bytes(layer.get_buffer().get(rect, 1.0, None, Gegl.AbyssPolicy.CLAMP))
    finally:
        dup.delete()
    bpp = len(data) // (w * h)
    if bpp > 1:  # indexed+alpha safety: keep the index plane
        data = data[::bpp]
    # keep full resolution so zoom shows real pixels; only clamp absurd sizes
    return subsample_indices(w, h, data, 2048, 2048)


def load_palette_file(path: Path) -> Palette:
    """Any supported palette source; indexed PNGs go through GIMP itself
    (the Flatpak GIMP python has no Pillow)."""
    if path.suffix.lower() == '.png':
        image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                               Gio.File.new_for_path(str(path)))
        try:
            if image.get_base_type() != Gimp.ImageBaseType.INDEXED:
                raise ValueError(f"PNG is not indexed/palette mode: {path}")
            return Palette(path.stem, colors_from_gimp_palette(image.get_palette()))
        finally:
            image.delete()
    return load_palette(path).padded256()


class PreviewCanvas:
    """Renders indexed pixels with the current palette.

    Mouse wheel zooms (anchored on the pointer), dragging pans (inside the
    per-preview scroller), a plain click picks the pixel's palette index via
    ``pick_cb``.
    """

    def __init__(self, pick_cb=None, zoom_cb=None):
        self.width = 0
        self.height = 0
        self.indices = b''
        self.zoom = 1.0
        self.hadj = None  # set by the host once inside a ScrolledWindow
        self.vadj = None
        self._pick_cb = pick_cb
        self._zoom_cb = zoom_cb
        self._pixbuf = None
        self._drag = None
        self.widget = Gtk.DrawingArea()
        self.widget.add_events(Gdk.EventMask.SCROLL_MASK
                               | Gdk.EventMask.BUTTON_PRESS_MASK
                               | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.POINTER_MOTION_MASK)
        self.widget.connect('draw', self._on_draw)
        self.widget.connect('scroll-event', self._on_scroll)
        self.widget.connect('button-press-event', self._on_press)
        self.widget.connect('button-release-event', self._on_release)
        self.widget.connect('motion-notify-event', self._on_motion)

    def set_pixels(self, width, height, indices):
        self.width, self.height, self.indices = width, height, indices
        self._update_size()

    def set_zoom(self, zoom, anchor=None):
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if zoom == self.zoom:
            return
        old = self.zoom
        self.zoom = zoom
        self._update_size()
        if anchor and self.hadj and self.vadj:
            ax, ay = anchor
            ratio = zoom / old

            def fix_scroll():
                self.hadj.set_value((self.hadj.get_value() + ax) * ratio - ax)
                self.vadj.set_value((self.vadj.get_value() + ay) * ratio - ay)
                return False
            GLib.idle_add(fix_scroll)
        self.widget.queue_draw()
        if self._zoom_cb:
            self._zoom_cb(self.zoom)

    def _update_size(self):
        if self.width:
            self.widget.set_size_request(int(self.width * self.zoom),
                                         int(self.height * self.zoom))

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
        cr.scale(self.zoom, self.zoom)
        Gdk.cairo_set_source_pixbuf(cr, self._pixbuf, 0, 0)
        cr.get_source().set_filter(cairo.Filter.NEAREST)
        cr.paint()
        return False

    # wheel = zoom anchored on the pointer
    def _on_scroll(self, widget, event):
        if event.direction == Gdk.ScrollDirection.UP:
            factor = ZOOM_STEP
        elif event.direction == Gdk.ScrollDirection.DOWN:
            factor = 1 / ZOOM_STEP
        elif event.direction == Gdk.ScrollDirection.SMOOTH:
            factor = ZOOM_STEP if event.delta_y < 0 else 1 / ZOOM_STEP
        else:
            return False
        anchor = None
        if self.hadj and self.vadj:
            anchor = (event.x - self.hadj.get_value(),
                      event.y - self.vadj.get_value())
        self.set_zoom(self.zoom * factor, anchor=anchor)
        return True  # consume: don't scroll the pane

    # drag = pan, click (no movement) = pick the pixel's index
    def _on_press(self, widget, event):
        if event.button == 1:
            self._drag = (event.x_root, event.y_root,
                          self.hadj.get_value() if self.hadj else 0,
                          self.vadj.get_value() if self.vadj else 0, False)
        return True

    def _on_motion(self, widget, event):
        if self._drag is None:
            return False
        x0, y0, h0, v0, moved = self._drag
        dx, dy = event.x_root - x0, event.y_root - y0
        if abs(dx) + abs(dy) > 3:
            moved = True
        self._drag = (x0, y0, h0, v0, moved)
        if moved and self.hadj and self.vadj:
            self.hadj.set_value(h0 - dx)
            self.vadj.set_value(v0 - dy)
        return True

    def _on_release(self, widget, event):
        if event.button != 1 or self._drag is None:
            return False
        moved = self._drag[4]
        self._drag = None
        if not moved and self._pick_cb and self.width:
            px, py = int(event.x / self.zoom), int(event.y / self.zoom)
            if 0 <= px < self.width and 0 <= py < self.height:
                self._pick_cb(self.indices[py * self.width + px])
        return True


class PaletteStudioDialog:
    """The merged Lab + palette/material editor."""

    RESP_SAVE_PALETTE = 99
    RESP_APPLY_IMAGE = 100
    RESP_APPLY_BOTH = 101

    def __init__(self, image=None, initial_file=None, name_hint=''):
        self.image = image
        self.can_apply = (image is not None
                          and image.get_base_type() == Gimp.ImageBaseType.INDEXED)
        self.table = list(DEFAULT_MATERIALS)
        self.animated = set(ANIMATED_INDICES)
        self._anim_offset = 0
        self._anim_timer = None
        self.name_hint = name_hint

        base8 = [(0, 0, 0)] * 256
        if self.can_apply:
            base8 = colors_from_gimp_palette(image.get_palette())
        self.base = to_float(base8)          # committed state, floats
        self.preview = list(self.base)       # committed + live sliders
        self._pristine = (list(base8), list(self.table), set(self.animated))
        self.previews = []

        self.grid = PaletteGrid(base8, self.table,
                                hover_cb=self._update_info,
                                select_cb=lambda idx: self._recompute(),
                                edit_cb=self._edit_color,
                                animated=self.animated)

        self.dialog = GimpUi.Dialog(title='Liero Palette Studio')
        self.dialog.add_button('_Close', Gtk.ResponseType.CANCEL)
        self.dialog.add_button('Save _palette', self.RESP_SAVE_PALETTE)
        if self.can_apply:
            self.dialog.add_button('Apply to _image', self.RESP_APPLY_IMAGE)
            self.dialog.add_button('Apply + save palette', self.RESP_APPLY_BOTH)
            self.dialog.set_default_response(self.RESP_APPLY_BOTH)
        else:
            self.dialog.set_default_response(self.RESP_SAVE_PALETTE)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=12)
        self.dialog.get_content_area().add(outer)
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer.pack_start(hbox, True, True, 0)

        # ---- left: grid + palette source -----------------------------------
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hbox.pack_start(left, False, False, 0)
        frame = Gtk.Frame()
        frame.add(self.grid.widget)
        left.pack_start(frame, False, False, 0)
        left.pack_start(Gtk.Label(label='Palette source:', xalign=0), False, False, 0)
        source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.source_combo = Gtk.ComboBoxText()
        if self.can_apply:
            self.source_combo.append('__image__', 'Image colormap (no material info)')
        for pal in Gimp.palettes_get_list(''):
            if pal.get_color_count() == 256:
                self.source_combo.append(pal.get_name(), pal.get_name())
        self._source_handler = self.source_combo.connect('changed', self._on_source_changed)
        source_row.pack_start(self.source_combo, True, True, 0)
        open_btn = Gtk.Button(label='Open file…')
        open_btn.set_tooltip_text('.gpl / indexed .png / .lpl / .wlsprt / .lev / LIERO.EXE')
        open_btn.connect('clicked', self._on_open_file)
        source_row.pack_start(open_btn, False, False, 0)
        left.pack_start(source_row, False, False, 0)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_row.pack_start(Gtk.Label(label='Palette name:', xalign=0), False, False, 0)
        self.name_entry = Gtk.Entry(text=name_hint or 'Liero palette')
        name_row.pack_start(self.name_entry, True, True, 0)
        left.pack_start(name_row, False, False, 0)
        self.unique_check = Gtk.CheckButton(label='Unique colors when applying to image')
        self.unique_check.set_active(True)
        self.unique_check.set_tooltip_text(
            'Nudge duplicate RGB values minimally so GIMP can tell colormap '
            'entries apart (saved palettes always keep the raw colors)')
        left.pack_start(self.unique_check, False, False, 0)

        # ---- middle: controls ------------------------------------------------
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
        for gid, (label, _values) in MATERIAL_GROUPS.items():
            self.material_combo.append(f"group:{gid}", label)
        self.material_combo.set_active(0)
        sel_row.pack_start(self.material_combo, True, True, 0)
        sel_btn = Gtk.Button(label='Select')
        sel_btn.connect('clicked', self._on_select_material)
        sel_row.pack_start(sel_btn, False, False, 0)
        assign_btn = Gtk.Button(label='Assign')
        assign_btn.set_tooltip_text('Assign this material to the selected indices')
        assign_btn.connect('clicked', self._on_assign)
        sel_row.pack_start(assign_btn, False, False, 0)
        side.pack_start(sel_row, False, False, 0)

        extra_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label, handler in (('Animated', self._on_select_animated),
                               ('Invert', self._on_invert),
                               ('All', self._on_select_all),
                               ('Clear', self._on_clear),
                               ('Toggle anim', self._on_toggle_animated)):
            btn = Gtk.Button(label=label)
            btn.connect('clicked', handler)
            extra_row.pack_start(btn, True, True, 0)
        side.pack_start(extra_row, False, False, 0)

        self.lock_worm = Gtk.CheckButton(label='Lock worm indices')
        self.lock_worm.set_active(True)
        self.lock_worm.connect('toggled', lambda _b: self._on_lock_changed())
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
        save_mats = Gtk.Button(label='Save materials…')
        save_mats.connect('clicked', self._on_save_materials)
        btn_row2.pack_start(save_mats, True, True, 0)
        side.pack_start(btn_row2, False, False, 0)

        tip = Gtk.Label(xalign=0)
        tip.set_markup('<small>Right-click swatches for selection/copy menus · '
                       'double-click edits a color.\nSliders act on the selection; '
                       'Commit chains adjustments.</small>')
        side.pack_end(tip, False, False, 0)

        # ---- right: previews -------------------------------------------------
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hbox.pack_start(right, True, True, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(PREVIEW_W + 60, PREVIEW_H + 120)
        self.preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self.preview_box)
        right.pack_start(scroll, True, True, 0)
        prow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        load_btn = Gtk.Button(label='Add preview… (.lev / .wlsprt / indexed .png)')
        load_btn.connect('clicked', self._on_load_preview)
        prow.pack_start(load_btn, True, True, 0)
        self.animate_toggle = Gtk.ToggleButton(label='Animate colors')
        self.animate_toggle.set_tooltip_text(
            'Cycle the animated (colorAnim) ranges in the previews and the '
            'palette grid, like in game')
        self.animate_toggle.connect('toggled', self._on_animate_toggled)
        prow.pack_start(self.animate_toggle, False, False, 0)
        self.focus_toggle = Gtk.ToggleButton(label='Focus selection')
        self.focus_toggle.set_tooltip_text(
            'Previews show only the selected colors; everything else turns '
            'magenta so even single pixels stand out')
        self.focus_toggle.connect('toggled', lambda _b: self._render_preview())
        prow.pack_start(self.focus_toggle, False, False, 0)
        right.pack_start(prow, False, False, 0)

        # ---- bottom: material table as text ----------------------------------
        outer.pack_start(Gtk.Label(
            label='Material table (room-script expression or array — paste & apply, or copy out):',
            xalign=0), False, False, 0)
        text_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        outer.pack_start(text_row, False, False, 0)
        tscroll = Gtk.ScrolledWindow()
        tscroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        tscroll.set_size_request(-1, 64)
        self.text_view = Gtk.TextView()
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.text_view.set_monospace(True)
        tscroll.add(self.text_view)
        text_row.pack_start(tscroll, True, True, 0)
        text_btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        apply_text = Gtk.Button(label='Apply text to grid')
        apply_text.connect('clicked', self._on_apply_text)
        text_btns.pack_start(apply_text, False, False, 0)
        refresh_text = Gtk.Button(label='Refresh from grid')
        refresh_text.connect('clicked', lambda _b: self._refresh_text())
        text_btns.pack_start(refresh_text, False, False, 0)
        text_row.pack_start(text_btns, False, False, 0)

        self.dialog.set_default_size(1500, 900)

        if self.can_apply:
            self._add_preview(f"Image: {image.get_name() or 'untitled'}",
                              *indices_from_image(image))
            self.source_combo.set_active_id('__image__')
        if initial_file is not None:
            self._load_file(Path(initial_file))
        self._sync_locked()
        self._refresh_text()
        self._recompute()
        self._update_info()

    # ---- sources --------------------------------------------------------------

    def _set_base(self, base8, table=None, animated=None):
        self.base = to_float(base8)
        if table is not None:
            self.table = list(table)
        if animated is not None:
            self.animated = set(animated)
        self._pristine = (list(base8), list(self.table), set(self.animated))
        self.grid.table = list(self.table)
        self.grid.animated = set(self.animated)
        self._sync_locked()
        self._reset_sliders_silent()
        self._refresh_text()
        self._recompute()

    def _on_source_changed(self, _combo):
        source = self.source_combo.get_active_id()
        if source is None or source.startswith('file:'):
            return
        if source == '__image__':
            self._set_base(colors_from_gimp_palette(self.image.get_palette()),
                           table=DEFAULT_MATERIALS, animated=ANIMATED_INDICES)
            return
        pals = Gimp.palettes_get_list(source)
        pal = next((p for p in pals if p.get_name() == source), None)
        if pal is None:
            return
        names = [pal.get_entry_name(i)[1] for i in range(pal.get_color_count())]
        self.name_entry.set_text(pal.get_name())
        self._set_base(colors_from_gimp_palette(pal),
                       table=materials_from_entry_names(names) or DEFAULT_MATERIALS,
                       animated=animated_from_entry_names(names) or ANIMATED_INDICES)

    def _on_open_file(self, _btn):
        chooser = Gtk.FileChooserDialog(title='Open Liero Palette',
                                        transient_for=self.dialog,
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        chooser.add_button('_Open', Gtk.ResponseType.OK)
        flt = Gtk.FileFilter()
        flt.set_name('Liero palette sources')
        for pattern in ('*.gpl', '*.png', '*.lpl', '*.wlsprt', '*.lev',
                        '*.exe', 'LIERO.EXE'):
            flt.add_pattern(pattern)
            flt.add_pattern(pattern.upper())
        chooser.add_filter(flt)
        path = None
        if chooser.run() == Gtk.ResponseType.OK:
            path = Path(chooser.get_filename())
        chooser.destroy()
        if path is None:
            return
        try:
            self._load_file(path)
        except Exception as exc:
            traceback.print_exc()
            self.info.set_text(f"Open failed: {exc}")

    def _load_file(self, path):
        pal = load_palette_file(path)
        animated = ANIMATED_INDICES
        if path.suffix.lower() == '.exe' or path.name.upper() == 'LIERO.EXE':
            pairs = [v for pair in read_exe_color_anim(path) for v in pair]
            animated = expand_color_anim(pairs)
        source_id = f"file:{path}"
        # register/replace a combo entry for this file
        if self.source_combo.get_active_id() != source_id:
            self.source_combo.handler_block(self._source_handler)
            self.source_combo.append(source_id, f"File: {path.name}")
            self.source_combo.set_active_id(source_id)
            self.source_combo.handler_unblock(self._source_handler)
        self.name_entry.set_text(f"Liero {path.stem}")
        self._set_base(pal.colors, table=DEFAULT_MATERIALS, animated=animated)
        # a map file is also a natural preview
        if path.suffix.lower() in ('.lev', '.wlsprt'):
            try:
                self._add_preview(path.name, *self._load_preview_pixels(path))
            except Exception:
                traceback.print_exc()

    # ---- previews ---------------------------------------------------------------

    def _add_preview(self, title, width, height, indices):
        entry = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(Gtk.Label(label=title, xalign=0), True, True, 0)

        zoom_label = Gtk.Label(label='100%')
        canvas = PreviewCanvas(
            pick_cb=self._on_preview_pick,
            zoom_cb=lambda z: zoom_label.set_text(f"{round(z * 100)}%"))
        canvas.set_pixels(width, height, indices)

        zoom_out = Gtk.Button(label='−')
        zoom_out.connect('clicked', lambda _b: canvas.set_zoom(canvas.zoom / ZOOM_STEP))
        zoom_in = Gtk.Button(label='+')
        zoom_in.connect('clicked', lambda _b: canvas.set_zoom(canvas.zoom * ZOOM_STEP))
        close = Gtk.Button(label='✕')
        close.connect('clicked', lambda _b: self._remove_preview(canvas, entry))
        for w in (zoom_out, zoom_label, zoom_in, close):
            header.pack_start(w, False, False, 0)
        entry.pack_start(header, False, False, 0)

        # each preview scrolls inside a fixed-height window so zooming
        # never pushes the controls (or other previews) off screen
        inner = Gtk.ScrolledWindow()
        inner.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        inner.set_size_request(-1, min(380, height + 12))
        inner.add(canvas.widget)
        canvas.hadj = inner.get_hadjustment()
        canvas.vadj = inner.get_vadjustment()
        entry.pack_start(inner, False, False, 0)

        self.preview_box.pack_start(entry, False, False, 0)
        entry.show_all()
        self.previews.append(canvas)
        canvas.render(self._canvas_colors())

    def _remove_preview(self, canvas, entry):
        if canvas in self.previews:
            self.previews.remove(canvas)
        entry.destroy()

    def _on_preview_pick(self, idx):
        self.grid.selected = {idx}
        self.grid.last_click = idx
        self._update_info(idx)
        self._recompute()

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
                self._add_preview(path.name, *self._load_preview_pixels(path))
            except Exception as exc:
                traceback.print_exc()
                self.info.set_text(f"Preview load error: {exc}")
        else:
            chooser.destroy()

    def _load_preview_pixels(self, path):
        suffix = path.suffix.lower()
        if suffix == '.lev':
            return read_lev_pixels(path)
        if suffix == '.wlsprt':
            return wlsprt_sheet(path, sheet_width=PREVIEW_W)
        if suffix == '.png':
            img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                                 Gio.File.new_for_path(str(path)))
            try:
                if img.get_base_type() != Gimp.ImageBaseType.INDEXED:
                    raise ValueError('PNG is not indexed')
                return indices_from_image(img)
            finally:
                img.delete()
        raise ValueError(f'Unsupported preview source: {path}')

    # ---- transform pipeline --------------------------------------------------------

    def _locked(self):
        if self.lock_worm.get_active():
            return {i for i, m in enumerate(self.table) if m == MATERIAL['WORM']}
        return set()

    def _sync_locked(self):
        self.grid.locked = self._locked()
        self.grid.queue_draw()

    def _on_lock_changed(self):
        self._sync_locked()
        self._recompute()

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

    def _canvas_colors(self):
        """Colors for the preview canvases: animation + focus applied."""
        colors8 = self._maybe_animated(quantize(self.preview))
        if self.focus_toggle.get_active():
            colors8 = [c if i in self.grid.selected else FOCUS_COLOR
                       for i, c in enumerate(colors8)]
        return colors8

    def _render_preview(self):
        colors8 = self._maybe_animated(quantize(self.preview))
        self.grid.colors = list(colors8)
        self.grid.queue_draw()
        canvas_colors = self._canvas_colors()
        for canvas in self.previews:
            canvas.render(canvas_colors)

    # ---- color animation ------------------------------------------------------------

    def _anim_runs(self):
        idxs = sorted(self.animated)
        runs, i = [], 0
        while i < len(idxs):
            j = i
            while j + 1 < len(idxs) and idxs[j + 1] == idxs[j] + 1:
                j += 1
            runs.append((idxs[i], idxs[j]))
            i = j + 1
        return runs

    def _maybe_animated(self, colors8):
        if not self.animate_toggle.get_active():
            return colors8
        # Classic palette cycling: rotate colors inside each (from, to) range,
        # all indices inclusive. (WebLiero reportedly skips one color due to an
        # off-by-one — preview follows classic; revisit if in-game look matters.)
        out = list(colors8)
        for a, b in self._anim_runs():
            n = b - a + 1
            if n < 2:
                continue
            for k in range(n):
                out[a + k] = colors8[a + (k + self._anim_offset) % n]
        return out

    def _on_animate_toggled(self, _btn):
        if self.animate_toggle.get_active():
            if self._anim_timer is None:
                self._anim_timer = GLib.timeout_add(140, self._anim_tick)
        else:
            if self._anim_timer is not None:
                GLib.source_remove(self._anim_timer)
                self._anim_timer = None
            self._anim_offset = 0
            self._render_preview()

    def _anim_tick(self):
        if not self.animate_toggle.get_active():
            self._anim_timer = None
            return False
        self._anim_offset += 1
        self._render_preview()
        return True

    # ---- selection & material editing -------------------------------------------------

    def _on_select_material(self, _btn):
        active = self.material_combo.get_active_id()
        if active.startswith('group:'):
            self.grid.select_materials(MATERIAL_GROUPS[active[6:]][1])
        else:
            self.grid.select_material(int(active))
        self._recompute()

    def _on_assign(self, _btn):
        active = self.material_combo.get_active_id()
        if active.startswith('group:'):
            self.info.set_text('Pick a single material to assign (groups are for selecting).')
            return
        value = int(active)
        for i in self.grid.selected:
            self.table[i] = value
        self.grid.table = list(self.table)
        self._sync_locked()
        self._refresh_text()
        self._recompute()

    def _on_toggle_animated(self, _btn):
        self.animated.symmetric_difference_update(self.grid.selected)
        self.grid.animated = set(self.animated)
        self._recompute()

    def _on_select_animated(self, _btn):
        self.grid.selected = set(self.animated)
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

    def _edit_color(self, idx):
        chooser = Gtk.ColorChooserDialog(title=f'Color for index {idx}',
                                         transient_for=self.dialog)
        chooser.set_use_alpha(False)
        r, g, b = quantize([self.base[idx]])[0]
        chooser.set_rgba(Gdk.RGBA(r / 255.0, g / 255.0, b / 255.0, 1.0))
        if chooser.run() == Gtk.ResponseType.OK:
            rgba = chooser.get_rgba()
            self.base[idx] = (rgba.red * 255.0, rgba.green * 255.0, rgba.blue * 255.0)
        chooser.destroy()
        self._update_info(idx)
        self._recompute()

    # ---- material text field -------------------------------------------------------

    def _refresh_text(self):
        self.text_view.get_buffer().set_text(
            "materials: " + material_table_to_js(self.table) + ",")

    def _on_apply_text(self, _btn):
        buf = self.text_view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        try:
            self.table = parse_material_text(text)
        except Exception as exc:
            self.info.set_text(f"Material text error: {exc}")
            return
        self.grid.table = list(self.table)
        self._sync_locked()
        self._recompute()

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

    # ---- adjustments -----------------------------------------------------------------

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
        # back to the pristine state of the current source
        base8, table, animated = self._pristine
        self._set_base(base8, table=table, animated=animated)

    def _update_info(self, idx=None):
        lines = []
        if idx is not None:
            info = index_info(idx, self.table)
            r, g, b = self.grid.colors[idx]
            flags = []
            if idx in self.animated:
                flags.append('animated')
            if idx in self.grid.locked:
                flags.append('locked')
            lines.append(f"Index {idx}  #{r:02x}{g:02x}{b:02x}  {info.material_name}"
                         + (f"  [{', '.join(flags)}]" if flags else ''))
        else:
            lines.append('Hover a swatch for details.')
        n_locked = len(self.grid.selected & self.grid.locked)
        sel_line = f"Selected: {len(self.grid.selected)}"
        if n_locked:
            sel_line += f"  ({n_locked} locked — not affected)"
        lines.append(sel_line)
        lines.append(f"colorAnim: {indices_to_anim_pairs(self.animated)}")
        self.info.set_text("\n".join(lines))

    # ---- save/apply -------------------------------------------------------------------

    def _palette_name(self):
        return self.name_entry.get_text().strip() or 'Liero palette'

    def run(self):
        self.dialog.show_all()
        applied = False
        try:
            while True:
                response = self.dialog.run()
                raw = quantize(self.preview)
                if response == self.RESP_SAVE_PALETTE:
                    make_gimp_palette(self._palette_name(), raw,
                                      table=self.table, animated=self.animated)
                    Gimp.message(f"Saved palette '{self._palette_name()}'.")
                    continue  # stay open: saving is not closing
                if response in (self.RESP_APPLY_IMAGE, self.RESP_APPLY_BOTH) \
                        and self.can_apply:
                    apply_colors = (uniquify_palette(raw)
                                    if self.unique_check.get_active() else raw)
                    tmp = make_gimp_palette(f"{self._palette_name()} (applied)",
                                            apply_colors,
                                            table=self.table, animated=self.animated)
                    self.image.set_palette(tmp)
                    tmp.delete()
                    if response == self.RESP_APPLY_BOTH:
                        make_gimp_palette(self._palette_name(), raw,
                                          table=self.table, animated=self.animated)
                    Gimp.displays_flush()
                    applied = True
                break
        finally:
            if self._anim_timer is not None:
                GLib.source_remove(self._anim_timer)
                self._anim_timer = None
            self.dialog.destroy()
        return applied
