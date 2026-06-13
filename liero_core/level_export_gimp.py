"""GIMP-side dialog: export an OpenLiero MODERNLV level.

Authoring model (mirrors the OpenLiero engine, which keeps the two separate):
  * the active RGB image is the **display / map** (true-colour level art),
  * a separate **indexed** image is the **material mask** (gameplay indices) —
    typically the output of the Quantize-by-Material tool.

Both must be the same size; the level dimensions come from the active image.
Animation is optional: a ramps.json plus an anim layer (R=ramp 1-based,
G=phase). The preview renders exactly what the game shows, including live
animation, via liero_core.openliero (verified against the engine).
"""
from __future__ import annotations

import json
from pathlib import Path

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('GimpUi', '3.0')
gi.require_version('Gegl', '0.4')
gi.require_version('Gtk', '3.0')
from gi.repository import Gimp, GimpUi, Gegl, GLib, Gtk  # noqa: E402

from . import openliero  # noqa: E402
from .studio import PreviewCanvas, colors_from_gimp_palette  # noqa: E402

RESP_EXPORT = 100
_MODE_DISPLAY, _MODE_MASK, _MODE_ANIM = 'display', 'mask', 'anim'


def _flatten_read(image, fmt, w, h):
    """Composite the image to a single layer and read its pixels.

    fmt=None reads the buffer's native format (raw indices for an indexed
    image); fmt="R'G'B'A u8" reads byte-exact sRGB+alpha.
    """
    dup = image.duplicate()
    layer = dup.flatten()
    rect = Gegl.Rectangle.new(0, 0, w, h)
    data = bytes(layer.get_buffer().get(rect, 1.0, fmt, Gegl.AbyssPolicy.CLAMP))
    dup.delete()
    return data


def _palette_rgb(image):
    """256*3 RGB bytes from an indexed image's colormap (for the fallback path)."""
    out = bytearray(256 * 3)
    pal = image.get_palette() if image is not None else None
    if pal is not None:
        colors = colors_from_gimp_palette(pal)
        for i, c in enumerate(colors[:256]):
            out[i * 3:i * 3 + 3] = bytes(c[:3])
    return bytes(out)


class LevelExportDialog:
    def __init__(self, image):
        self.image = image
        self.w = image.get_width()
        self.h = image.get_height()
        self.mask_image = None
        self.ramps = []
        self.ramps_path = None
        self.anim_layer = None
        self._level = None           # cached extract_level() dict for preview
        self._pal = bytes(256 * 3)
        self._cycles = 0
        self._timer = None
        self._mode = _MODE_DISPLAY
        self._build()

    # ---- UI -----------------------------------------------------------------
    def _build(self):
        self.dialog = GimpUi.Dialog(title='Export OpenLiero Level (MODERNLV)')
        self.dialog.add_button('_Close', Gtk.ResponseType.CANCEL)
        self.export_btn = self.dialog.add_button('_Export .lev…', RESP_EXPORT)

        outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        outer.set_border_width(8)
        self.dialog.get_content_area().add(outer)

        # left: controls
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        left.set_size_request(360, -1)
        outer.pack_start(left, False, False, 0)

        fmt = 'OLLEVEL2' if openliero.is_sized(self.w, self.h) else 'legacy 504×350'
        left.pack_start(Gtk.Label(
            label=f"Level size: {self.w}×{self.h}  ({fmt})", xalign=0), False, False, 0)
        left.pack_start(Gtk.Label(
            label="Display / map = this RGB image.", xalign=0), False, False, 0)

        # material mask (indexed image of matching size)
        left.pack_start(Gtk.Label(label="<b>Material mask</b> (indexed image)",
                                  xalign=0, use_markup=True), False, False, 0)
        self.mask_combo = Gtk.ComboBoxText()
        self._fill_mask_combo()
        self.mask_combo.connect('changed', self._on_mask_changed)
        left.pack_start(self.mask_combo, False, False, 0)

        # animation (optional)
        left.pack_start(Gtk.Separator(), False, False, 4)
        self.anim_check = Gtk.CheckButton(label="Add animation (ramps + anim layer)")
        self.anim_check.connect('toggled', self._on_anim_toggled)
        left.pack_start(self.anim_check, False, False, 0)

        self.anim_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ramps_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.ramps_label = Gtk.Label(label="no ramps.json", xalign=0)
        ramps_btn = Gtk.Button(label="Load ramps.json…")
        ramps_btn.connect('clicked', self._on_load_ramps)
        ramps_row.pack_start(ramps_btn, False, False, 0)
        ramps_row.pack_start(self.ramps_label, True, True, 0)
        self.anim_box.pack_start(ramps_row, False, False, 0)
        self.anim_box.pack_start(Gtk.Label(
            label="Anim layer (R=ramp 1-based, G=phase):", xalign=0), False, False, 0)
        self.anim_combo = Gtk.ComboBoxText()
        self._fill_anim_combo()
        self.anim_combo.connect('changed', lambda _c: self._rebuild_level())
        self.anim_box.pack_start(self.anim_combo, False, False, 0)
        self.anim_box.set_sensitive(False)
        left.pack_start(self.anim_box, False, False, 0)

        # preview mode + playback
        left.pack_start(Gtk.Separator(), False, False, 4)
        left.pack_start(Gtk.Label(label="<b>Preview</b>", xalign=0, use_markup=True),
                        False, False, 0)
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._mode_btns = {}
        first = None
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

        self.status = Gtk.Label(label="", xalign=0)
        self.status.set_line_wrap(True)
        left.pack_start(self.status, False, False, 0)

        # right: preview canvas in a scroller
        self.canvas = PreviewCanvas(zoom_cb=None)
        self.canvas.set_pixels(self.w, self.h, b'\x00' * (self.w * self.h))
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(640, 560)
        scroller.add(self.canvas.widget)
        self.canvas.hadj = scroller.get_hadjustment()
        self.canvas.vadj = scroller.get_vadjustment()
        outer.pack_start(scroller, True, True, 0)

        self.dialog.set_default_size(1080, 640)
        self.dialog.get_content_area().show_all()
        self._rebuild_level()

    def _fill_mask_combo(self):
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

    def _fill_anim_combo(self):
        self.anim_combo.append('__none__', '— none —')
        self._anim_by_id = {}
        for layer in self.image.get_layers():
            ident = str(layer.get_id())
            self.anim_combo.append(ident, layer.get_name() or 'layer')
            self._anim_by_id[ident] = layer
        self.anim_combo.set_active(0)

    # ---- data plumbing ------------------------------------------------------
    def _on_mask_changed(self, combo):
        ident = combo.get_active_id()
        self.mask_image = self._mask_by_id.get(ident)
        self._rebuild_level()

    def _on_anim_toggled(self, btn):
        on = btn.get_active()
        self.anim_box.set_sensitive(on)
        self._rebuild_level()

    def _on_load_ramps(self, _btn):
        chooser = Gtk.FileChooserDialog(title='Load ramps.json',
                                        action=Gtk.FileChooserAction.OPEN)
        chooser.add_button('_Cancel', Gtk.ResponseType.CANCEL)
        chooser.add_button('_Open', Gtk.ResponseType.OK)
        if chooser.run() == Gtk.ResponseType.OK:
            path = chooser.get_filename()
            chooser.destroy()
            try:
                ramps = json.load(open(path))
                openliero.validate_ramps(ramps)
                self.ramps = ramps
                self.ramps_path = path
                self.ramps_label.set_text(f"{len(ramps)} ramp(s)")
            except Exception as exc:
                self.ramps = []
                self.ramps_label.set_text(f"error: {exc}")
            self._rebuild_level()
        else:
            chooser.destroy()

    def _anim_enabled(self):
        return self.anim_check.get_active() and bool(self.ramps)

    def _gather(self):
        """Return (material, display_rgba, ramps, anim_rgba) or None if not ready."""
        if self.mask_image is None or \
                (self.mask_image.get_width(), self.mask_image.get_height()) != (self.w, self.h):
            return None
        material = _flatten_read(self.mask_image, None, self.w, self.h)
        display = _flatten_read(self.image, "R'G'B'A u8", self.w, self.h)
        ramps = self.ramps if self._anim_enabled() else None
        anim = None
        if ramps:
            ident = self.anim_combo.get_active_id()
            layer = self._anim_by_id.get(ident)
            if layer is not None:
                rect = Gegl.Rectangle.new(0, 0, self.w, self.h)
                anim = bytes(layer.get_buffer().get(rect, 1.0, "R'G'B'A u8",
                                                    Gegl.AbyssPolicy.CLAMP))
        return material, display, ramps, anim

    def _rebuild_level(self):
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
        self._pal = _palette_rgb(self.mask_image)
        self.export_btn.set_sensitive(True)
        self.play_btn.set_sensitive(self._anim_enabled())
        n_anim = sum(1 for b in (self._level.get('display_anim') or b'') if b)
        self.status.set_text(
            f"Ready: {self.w}×{self.h}, {len(self.ramps) if self._anim_enabled() else 0} "
            f"ramp(s), {n_anim} animated px.")
        self._render()

    # ---- preview render -----------------------------------------------------
    def _on_mode(self, btn, key):
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
        else:  # display: frame 0, animation frozen
            rgb = openliero.render_frame_rgb(self._level, self._pal, 0)
        self.canvas.render_rgb(rgb)

    def _on_play(self, btn):
        if btn.get_active() and self._mode == _MODE_ANIM and self._anim_enabled():
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
                Gimp.message(f"Wrote {Path(path).name} "
                             f"({self.w}×{self.h}, MODERNLV"
                             f"{', animated' if ramps else ''}).")
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
            self.dialog.destroy()
