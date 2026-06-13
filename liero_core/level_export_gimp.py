"""GIMP-side dialog: export an OpenLiero MODERNLV level.

Authoring model (mirrors the OpenLiero engine, which keeps these separate):
  * the active RGB image is the **display / map** (true-colour level art),
  * the **material mask** (gameplay indices) is built on the fly from the named
    top-level layers/groups of that same image (a "rock" group -> rock, etc.),
    or, as a fallback, taken from a separate indexed image.

Animation: define colour **ramps** in-dialog (or load/save ramps.json), then
assign a ramp to any top-level layer/group; every pixel that layer covers
animates with that ramp. The preview renders exactly what the game shows,
including live animation, via liero_core.openliero (verified vs the engine).
"""
from __future__ import annotations

from pathlib import Path

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gimp, GimpUi, Gegl, GLib, Gtk, Gdk  # noqa: E402

from . import openliero  # noqa: E402
from .studio import PreviewCanvas, colors_from_gimp_palette  # noqa: E402
from .material import classify_name  # noqa: E402
from .defaults import MATERIAL, MATERIAL_NAMES, MATERIAL_GROUPS  # noqa: E402

RESP_EXPORT = 100
_MODE_DISPLAY, _MODE_MASK, _MODE_ANIM = 'display', 'mask', 'anim'

# Material choices offered per layer (label, key). key: int material, 'group:ID',
# or None = skip (layer doesn't contribute to the mask).
_MATERIAL_ORDER = ['DIRT', 'DIRT_2', 'ROCK', 'BG_SEESHADOW', 'BG',
                   'BG_DIRT', 'BG_DIRT_2', 'WORM', 'UNDEF']


def _material_options():
    opts = [('— skip —', None)]
    for nm in _MATERIAL_ORDER:
        if nm in MATERIAL:
            opts.append((f"{nm} (idx {openliero.canonical_index_for_material(MATERIAL[nm])})",
                         MATERIAL[nm]))
    for gid, (label, _members) in MATERIAL_GROUPS.items():
        opts.append((f"group: {label}", f"group:{gid}"))
    return opts


def _key_to_index(key):
    """Material key -> canonical palette index (or None for skip)."""
    if key is None:
        return None
    if isinstance(key, str) and key.startswith('group:'):
        _label, members = MATERIAL_GROUPS[key[6:]]
        return openliero.canonical_index_for_material(members[0])
    return openliero.canonical_index_for_material(int(key))


def _default_key_for(name):
    mat = classify_name(name or '')
    return mat if mat is not None else None


def _hex_to_rgba(hx):
    hx = hx.lstrip('#')
    c = Gdk.RGBA()
    c.red = int(hx[0:2], 16) / 255.0
    c.green = int(hx[2:4], 16) / 255.0
    c.blue = int(hx[4:6], 16) / 255.0
    c.alpha = 1.0
    return c


def _rgba_to_hex(rgba):
    return '#%02X%02X%02X' % (round(rgba.red * 255), round(rgba.green * 255),
                              round(rgba.blue * 255))


def _flatten_read(image, fmt, w, h):
    dup = image.duplicate()
    layer = dup.flatten()
    rect = Gegl.Rectangle.new(0, 0, w, h)
    data = bytes(layer.get_buffer().get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP))
    dup.delete()
    return data


def _default_palette_rgb():
    try:
        from .formats import default_palette
        pal = default_palette()
        colors = getattr(pal, 'colors', pal)
        out = bytearray(256 * 3)
        for i, c in enumerate(colors[:256]):
            out[i * 3:i * 3 + 3] = bytes(tuple(c)[:3])
        return bytes(out)
    except Exception:
        return bytes(256 * 3)


class LevelExportDialog:
    def __init__(self, image):
        self.image = image
        self.w = image.get_width()
        self.h = image.get_height()
        self.mask_source = 'layers'        # 'layers' | 'indexed'
        self.mask_image = None
        self.ramps = []                    # [{'shift': int, 'colors': ['#RRGGBB', ...]}]
        self.layer_rows = []               # parallel to image.get_layers(): (layer, mat_combo, ramp_combo)
        self._cov_cache = {}
        self._level = None
        self._pal = _default_palette_rgb()
        self._cycles = 0
        self._timer = None
        self._mode = _MODE_DISPLAY
        self._ready = False        # guards rebuilds during widget construction
        self._suspend = False      # set while programmatically refilling combos
        self._pending = None       # idle id for a coalesced rebuild
        self._display_rgba = None  # cached image composite (read once)
        self._mask_cache = {}      # cached indexed-mask indices by image id
        self._build()

    # ---- UI -----------------------------------------------------------------
    def _build(self):
        self.dialog = GimpUi.Dialog(title='Export OpenLiero Level (MODERNLV)')
        self.dialog.add_button('_Close', Gtk.ResponseType.CANCEL)
        self.export_btn = self.dialog.add_button('_Export .lev…', RESP_EXPORT)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.set_border_width(8)
        self.dialog.get_content_area().add(outer)

        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        left_scroll.set_size_request(420, 600)
        outer.pack_start(left_scroll, False, False, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_border_width(4)
        left_scroll.add(left)

        fmt = 'OLLEVEL2' if openliero.is_sized(self.w, self.h) else 'legacy 504×350'
        left.pack_start(Gtk.Label(label=f"Level {self.w}×{self.h} ({fmt}). "
                                  f"Display/map = this RGB image.", xalign=0,
                                  wrap=True), False, False, 0)

        # --- material mask source ---
        left.pack_start(Gtk.Label(label="<b>Material mask</b>", xalign=0,
                                  use_markup=True), False, False, 0)
        src_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.rb_layers = Gtk.RadioButton.new_with_label_from_widget(None,
                                                                    'From layers/groups')
        self.rb_indexed = Gtk.RadioButton.new_with_label_from_widget(self.rb_layers,
                                                                     'Indexed image')
        self.rb_layers.connect('toggled', self._on_source)
        src_row.pack_start(self.rb_layers, False, False, 0)
        src_row.pack_start(self.rb_indexed, False, False, 0)
        left.pack_start(src_row, False, False, 0)

        # layers pane
        self.layers_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for t, exp in (('Top-level layer', True), ('Material', False), ('Ramp', False)):
            lbl = Gtk.Label(label=f"<small>{t}</small>", use_markup=True, xalign=0)
            hdr.pack_start(lbl, exp, exp, 0)
        self.layers_pane.pack_start(hdr, False, False, 0)
        self._build_layer_rows()
        unc = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        unc.pack_start(Gtk.Label(label="Uncovered →", xalign=0), False, False, 0)
        self.uncovered_combo = self._material_combo(default_key=MATERIAL['BG_SEESHADOW'],
                                                    allow_skip=False)
        unc.pack_start(self.uncovered_combo, True, True, 0)
        self.layers_pane.pack_start(unc, False, False, 0)
        left.pack_start(self.layers_pane, False, False, 0)

        # indexed pane
        self.indexed_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.mask_combo = Gtk.ComboBoxText()
        self._fill_mask_combo()
        self.mask_combo.connect('changed', self._on_mask_changed)
        self.indexed_pane.pack_start(self.mask_combo, False, False, 0)
        left.pack_start(self.indexed_pane, False, False, 0)

        # --- ramps editor ---
        left.pack_start(Gtk.Separator(), False, False, 4)
        left.pack_start(Gtk.Label(label="<b>Animation ramps</b>", xalign=0,
                                  use_markup=True), False, False, 0)
        left.pack_start(Gtk.Label(
            label="<small>Assign a ramp to a layer above to animate it.</small>",
            use_markup=True, xalign=0), False, False, 0)
        self.ramps_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        left.pack_start(self.ramps_box, False, False, 0)
        rbtns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        add_ramp = Gtk.Button(label="+ Ramp")
        add_ramp.connect('clicked', self._on_add_ramp)
        save_j = Gtk.Button(label="Save JSON…")
        save_j.connect('clicked', self._on_save_ramps)
        load_j = Gtk.Button(label="Load JSON…")
        load_j.connect('clicked', self._on_load_ramps)
        for b in (add_ramp, save_j, load_j):
            rbtns.pack_start(b, False, False, 0)
        left.pack_start(rbtns, False, False, 0)
        self._rebuild_ramps_ui()

        # --- preview controls ---
        left.pack_start(Gtk.Separator(), False, False, 4)
        left.pack_start(Gtk.Label(label="<b>Preview</b>", xalign=0, use_markup=True),
                        False, False, 0)
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        first = None
        self._mode_btns = {}
        for key, lbl in ((_MODE_DISPLAY, 'Display'), (_MODE_MASK, 'Material mask'),
                         (_MODE_ANIM, 'Animation')):
            rb = Gtk.RadioButton.new_with_label_from_widget(first, lbl)
            first = first or rb
            rb.connect('toggled', self._on_mode, key)
            modes.pack_start(rb, False, False, 0)
            self._mode_btns[key] = rb
        left.pack_start(modes, False, False, 0)
        self.play_btn = Gtk.ToggleButton(label="▶ Play")
        self.play_btn.connect('toggled', self._on_play)
        self.play_btn.set_sensitive(False)
        left.pack_start(self.play_btn, False, False, 0)
        self.status = Gtk.Label(label="", xalign=0, wrap=True)
        left.pack_start(self.status, False, False, 0)

        # right: preview canvas
        self.canvas = PreviewCanvas()
        self.canvas.set_pixels(self.w, self.h, b'\x00' * (self.w * self.h))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(640, 600)
        scroller.add(self.canvas.widget)
        self.canvas.hadj = scroller.get_hadjustment()
        self.canvas.vadj = scroller.get_vadjustment()
        outer.pack_start(scroller, True, True, 0)

        self.dialog.set_default_size(1140, 680)
        self.dialog.get_content_area().show_all()
        self.indexed_pane.hide()  # default to layers source
        self._ready = True
        self._rebuild_level()

    def _material_combo(self, default_key=None, allow_skip=True):
        combo = Gtk.ComboBoxText()
        combo._keys = []
        for label, key in _material_options():
            if key is None and not allow_skip:
                continue
            combo.append_text(label)
            combo._keys.append(key)
        try:
            combo.set_active(combo._keys.index(default_key))
        except ValueError:
            combo.set_active(0)
        combo.connect('changed', lambda _c: self._rebuild_level())
        return combo

    def _combo_key(self, combo):
        i = combo.get_active()
        return combo._keys[i] if 0 <= i < len(combo._keys) else None

    def _build_layer_rows(self):
        self.layer_rows = []
        for layer in self.image.get_layers():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            name = layer.get_name() or 'layer'
            row.pack_start(Gtk.Label(label=name, xalign=0), True, True, 0)
            mat_combo = self._material_combo(default_key=_default_key_for(name))
            row.pack_start(mat_combo, False, False, 0)
            ramp_combo = Gtk.ComboBoxText()
            self._fill_ramp_combo(ramp_combo, 0)
            ramp_combo.connect('changed', lambda _c: self._rebuild_level())
            row.pack_start(ramp_combo, False, False, 0)
            self.layers_pane_row_add(row)
            self.layer_rows.append((layer, mat_combo, ramp_combo))

    def layers_pane_row_add(self, row):
        # rows are inserted before the "Uncovered" line; during initial build
        # the pane only has the header, so append is fine.
        self.layers_pane.pack_start(row, False, False, 0)

    def _fill_ramp_combo(self, combo, keep):
        combo.remove_all()
        combo.append_text('none')
        for i in range(len(self.ramps)):
            combo.append_text(f"Ramp {i + 1}")
        combo.set_active(keep if 0 <= keep <= len(self.ramps) else 0)

    def _fill_mask_combo(self):
        self.mask_combo.remove_all()
        self.mask_combo.append('__none__', '— select an indexed image —')
        self._mask_by_id = {}
        for img in Gimp.get_images():
            if img.get_base_type() != Gimp.ImageBaseType.INDEXED:
                continue
            iw, ih = img.get_width(), img.get_height()
            ident = str(img.get_id())
            label = f"{img.get_name() or 'image'} ({iw}×{ih})"
            if (iw, ih) != (self.w, self.h):
                label += "  ✗ size"
            self.mask_combo.append(ident, label)
            self._mask_by_id[ident] = img
        self.mask_combo.set_active(0)

    # ---- ramps editor -------------------------------------------------------
    def _rebuild_ramps_ui(self):
        for child in list(self.ramps_box.get_children()):
            self.ramps_box.remove(child)
        for ri, ramp in enumerate(self.ramps):
            self.ramps_box.pack_start(self._ramp_row(ri, ramp), False, False, 0)
        self.ramps_box.show_all()
        # keep each layer's ramp combo in sync with the ramp count, WITHOUT
        # letting set_active fire 'changed' (which would storm rebuilds).
        self._suspend = True
        try:
            for _layer, _mat, ramp_combo in self.layer_rows:
                cur = ramp_combo.get_active()
                self._fill_ramp_combo(ramp_combo, cur)
        finally:
            self._suspend = False

    def _ramp_row(self, ri, ramp):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top.pack_start(Gtk.Label(label=f"Ramp {ri + 1}", xalign=0), False, False, 0)
        top.pack_start(Gtk.Label(label="speed 2^", xalign=1), False, False, 0)
        shift = Gtk.SpinButton.new_with_range(0, 31, 1)
        shift.set_value(ramp.get('shift', 0))
        shift.connect('value-changed', self._on_shift, ri)
        top.pack_start(shift, False, False, 0)
        rm = Gtk.Button(label="✕ ramp")
        rm.connect('clicked', self._on_remove_ramp, ri)
        top.pack_end(rm, False, False, 0)
        box.pack_start(top, False, False, 0)

        colors = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for ci, hx in enumerate(ramp['colors']):
            btn = Gtk.ColorButton.new_with_rgba(_hex_to_rgba(hx))
            btn.set_title(f"Ramp {ri + 1} colour {ci + 1}")
            btn.connect('color-set', self._on_color_set, ri, ci)
            colors.pack_start(btn, False, False, 0)
        add_c = Gtk.Button(label="+")
        add_c.connect('clicked', self._on_add_color, ri)
        colors.pack_start(add_c, False, False, 0)
        if len(ramp['colors']) > 1:
            del_c = Gtk.Button(label="−")
            del_c.connect('clicked', self._on_del_color, ri)
            colors.pack_start(del_c, False, False, 0)
        box.pack_start(colors, False, False, 0)
        return box

    def _on_add_ramp(self, _b):
        self.ramps.append({'shift': 2, 'colors': ['#1A3A6A', '#2A4A7A']})
        self._rebuild_ramps_ui()
        self._rebuild_level()

    def _on_remove_ramp(self, _b, ri):
        del self.ramps[ri]
        # any layer pointing past the new count resets to none
        self._rebuild_ramps_ui()
        self._rebuild_level()

    def _on_shift(self, spin, ri):
        self.ramps[ri]['shift'] = int(spin.get_value())
        self._rebuild_level()

    def _on_color_set(self, btn, ri, ci):
        self.ramps[ri]['colors'][ci] = _rgba_to_hex(btn.get_rgba())
        self._rebuild_level()

    def _on_add_color(self, _b, ri):
        self.ramps[ri]['colors'].append('#FFFFFF')
        self._rebuild_ramps_ui()
        self._rebuild_level()

    def _on_del_color(self, _b, ri):
        if len(self.ramps[ri]['colors']) > 1:
            self.ramps[ri]['colors'].pop()
            self._rebuild_ramps_ui()
            self._rebuild_level()

    def _on_save_ramps(self, _b):
        if not self.ramps:
            Gimp.message("No ramps to save.")
            return
        chooser = Gtk.FileChooserDialog(title='Save ramps.json',
                                        action=Gtk.FileChooserAction.SAVE)
        chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        chooser.add_button('_Save', Gtk.ResponseType.OK)
        chooser.set_current_name('ramps.json')
        chooser.set_do_overwrite_confirmation(True)
        if chooser.run() == Gtk.ResponseType.OK:
            path = chooser.get_filename()
            chooser.destroy()
            try:
                openliero.save_ramps_json(path, self.ramps)
                Gimp.message(f"Saved {len(self.ramps)} ramp(s).")
            except Exception as exc:
                Gimp.message(f"Save failed: {exc}")
        else:
            chooser.destroy()

    def _on_load_ramps(self, _b):
        chooser = Gtk.FileChooserDialog(title='Load ramps.json',
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        chooser.add_button('_Open', Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK:
            path = chooser.get_filename()
            chooser.destroy()
            try:
                self.ramps = openliero.load_ramps_json(path)
            except Exception as exc:
                Gimp.message(f"Load failed: {exc}")
            self._rebuild_ramps_ui()
            self._rebuild_level()
        else:
            chooser.destroy()

    # ---- data plumbing ------------------------------------------------------
    def _on_source(self, _btn):
        if not self._ready:
            return
        self.mask_source = 'layers' if self.rb_layers.get_active() else 'indexed'
        self.layers_pane.set_visible(self.mask_source == 'layers')
        self.indexed_pane.set_visible(self.mask_source == 'indexed')
        self._rebuild_level()

    def _on_mask_changed(self, combo):
        self.mask_image = self._mask_by_id.get(combo.get_active_id())
        self._rebuild_level()

    def _coverage(self, layer):
        key = layer.get_id()
        if key in self._cov_cache:
            return self._cov_cache[key]
        lw, lh = layer.get_width(), layer.get_height()
        off = layer.get_offsets()
        ox, oy = (off[1], off[2]) if len(off) == 3 else (off[0], off[1])
        rect = Gegl.Rectangle.new(0, 0, lw, lh)
        rgba = layer.get_buffer().get(rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.CLAMP)
        W, H = self.w, self.h
        cov = bytearray(W * H)
        for ly in range(lh):
            iy = oy + ly
            if iy < 0 or iy >= H:
                continue
            rowbase = ly * lw * 4
            ibase = iy * W
            for lx in range(lw):
                ix = ox + lx
                if 0 <= ix < W and rgba[rowbase + lx * 4 + 3] > 0:
                    cov[ibase + ix] = 1
        cov = bytes(cov)
        self._cov_cache[key] = cov
        return cov

    def _material_from_layers(self):
        mat_layers, anim_layers = [], []
        for layer, mat_combo, ramp_combo in self.layer_rows:
            key = self._combo_key(mat_combo)
            if key is None:
                continue
            cov = self._coverage(layer)
            mat_layers.append((cov, _key_to_index(key)))
            anim_layers.append((cov, ramp_combo.get_active()))  # 0 = none
        default_idx = _key_to_index(self._combo_key(self.uncovered_combo)) or 160
        material = openliero.compose_material_mask(self.w, self.h, mat_layers, default_idx)
        animated = any(r > 0 for _c, r in anim_layers)
        if self.ramps and animated:
            anim = openliero.build_anim_rgba(self.w, self.h, anim_layers)
            return material, self.ramps, anim
        return material, None, None

    def _display(self):
        """The image composite as RGBA bytes, read once and cached."""
        if self._display_rgba is None:
            self._display_rgba = _flatten_read(self.image, "R'G'B'A u8", self.w, self.h)
        return self._display_rgba

    def _indexed_mask(self, image):
        key = image.get_id()
        if key not in self._mask_cache:
            self._mask_cache[key] = _flatten_read(image, None, self.w, self.h)
        return self._mask_cache[key]

    def _gather(self):
        """Return (material, display_rgba, ramps, anim_rgba) or None if not ready."""
        display = self._display()
        if self.mask_source == 'indexed':
            if self.mask_image is None or \
                    (self.mask_image.get_width(), self.mask_image.get_height()) != (self.w, self.h):
                return None
            material = self._indexed_mask(self.mask_image)
            ramps = self.ramps if self.ramps else None
            return material, display, ramps, None
        # layers source
        material, ramps, anim = self._material_from_layers()
        return material, display, ramps, anim

    def _rebuild_level(self):
        """Coalesce many rapid changes into one rebuild on the idle loop.

        Crucially this runs the heavy work OFF the GTK signal stack, so a combo
        'changed' can't re-enter image duplication mid-signal.
        """
        if not self._ready or self._suspend or self._pending is not None:
            return
        self._pending = GLib.idle_add(self._rebuild_idle)

    def _rebuild_idle(self):
        self._pending = None
        self._do_rebuild_level()
        return False

    def _do_rebuild_level(self):
        self._stop_timer()
        got = self._gather()
        if got is None:
            self._level = None
            self.export_btn.set_sensitive(False)
            self.play_btn.set_sensitive(False)
            self.status.set_text("Select an indexed material mask of matching size.")
            self.canvas.render_rgb(b'\x20' * (self.w * self.h * 3))
            return
        material, display, ramps, anim = got
        try:
            data = openliero.build_level(self.w, self.h, material, display, ramps, anim)
            self._level = openliero.extract_level(data)
        except Exception as exc:
            self._level = None
            self.export_btn.set_sensitive(False)
            self.status.set_text(f"Cannot build level: {exc}")
            return
        if self.mask_source == 'indexed' and self.mask_image is not None:
            self._pal = _palette_rgb_indexed(self.mask_image)
        n_anim = sum(1 for b in (self._level.get('display_anim') or b'') if b)
        self.export_btn.set_sensitive(True)
        self.play_btn.set_sensitive(bool(anim))
        self.status.set_text(f"Ready: {self.w}×{self.h}, "
                             f"{len(ramps) if ramps else 0} ramp(s), {n_anim} animated px.")
        self._render()

    # ---- preview render -----------------------------------------------------
    def _on_mode(self, btn, key):
        if not self._ready:
            return
        if btn.get_active():
            self._mode = key
            if key != _MODE_ANIM:
                self._stop_timer()
                if self.play_btn.get_active():
                    self.play_btn.set_active(False)
            self._render()

    def _render(self):
        if self._level is None:
            return
        if self._mode == _MODE_MASK:
            rgb = openliero.material_mask_rgb(self._level['material'])
        elif self._mode == _MODE_ANIM:
            rgb = openliero.render_frame_rgb(self._level, self._pal, self._cycles)
        else:
            rgb = openliero.render_frame_rgb(self._level, self._pal, 0)
        self.canvas.render_rgb(rgb)

    def _on_play(self, btn):
        if btn.get_active() and self._mode == _MODE_ANIM and self._level is not None \
                and self._level.get('display_anim'):
            btn.set_label("⏸ Pause")
            if self._timer is None:
                self._timer = GLib.timeout_add(120, self._tick)
        else:
            btn.set_label("▶ Play")
            self._stop_timer()

    def _tick(self):
        self._cycles += 1
        self._render()
        return True

    def _stop_timer(self):
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None

    # ---- export -------------------------------------------------------------
    def _export(self):
        got = self._gather()
        if got is None:
            return
        material, display, ramps, anim = got
        chooser = Gtk.FileChooserDialog(title='Export OpenLiero level',
                                        action=Gtk.FileChooserAction.SAVE)
        chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        chooser.add_button('_Save', Gtk.ResponseType.OK)
        chooser.set_do_overwrite_confirmation(True)
        chooser.set_current_name((self.image.get_name() or 'level') + '.lev')
        if chooser.run() == Gtk.ResponseType.OK:
            path = chooser.get_filename()
            chooser.destroy()
            try:
                openliero.write_level(path, self.w, self.h, material, display, ramps, anim)
                Gimp.message(f"Wrote {Path(path).name} ({self.w}×{self.h}, MODERNLV"
                             f"{', animated' if anim else ''}).")
            except Exception as exc:
                Gimp.message(f"Export failed: {exc}")
        else:
            chooser.destroy()

    def run(self):
        try:
            while True:
                resp = self.dialog.run()
                if resp == RESP_EXPORT:
                    self._export()
                    continue
                break
        finally:
            self._stop_timer()
            if self._pending is not None:
                GLib.source_remove(self._pending)
                self._pending = None
            self.dialog.destroy()


def _palette_rgb_indexed(image):
    out = bytearray(256 * 3)
    pal = image.get_palette() if image is not None else None
    if pal is not None:
        colors = colors_from_gimp_palette(pal)
        for i, c in enumerate(colors[:256]):
            out[i * 3:i * 3 + 3] = bytes(c[:3])
    return bytes(out)
