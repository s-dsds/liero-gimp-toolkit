"""GIMP-side engine for material-aware quantization.

GIMP-dependent: import only from plug-in code. The pure planning logic lives
in :mod:`liero_core.quantizer`; this module reads pixels out of layer groups,
runs the plan, and materializes the result as a new indexed image.

The output image is produced by writing an indexed PNG ourselves and loading
it back: GIMP's convert_indexed() compares colors with precision loss and
mis-assigns indices that differ by uniquify nudges; the PNG route is exact.
"""
from __future__ import annotations

import tempfile
from collections import Counter
from pathlib import Path

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, Gegl, Gio  # noqa: E402

from .defaults import DEFAULT_MATERIALS, MATERIAL, MATERIAL_NAMES, PROTECTED_BY_DEFAULT  # noqa: E402
from .formats import write_indexed_png  # noqa: E402
from .material import classify_name  # noqa: E402
from .palette import Palette, nearest_color_index  # noqa: E402
from .quantizer import plan_quantization  # noqa: E402

ALPHA_THRESHOLD = 128


def scan_material_layers(image):
    """Top-level layers/groups with their material guess (None = unmatched)."""
    out = []
    for layer in image.get_layers():
        out.append((layer, classify_name(layer.get_name() or '')))
    return out


def read_layer_rgba(layer):
    """(offset_x, offset_y, width, height, rgba_bytes) of a layer or group.

    Group layers expose their composited projection through get_buffer(),
    so children stack the way they do on canvas.
    """
    _ok, off_x, off_y = layer.get_offsets()
    w, h = layer.get_width(), layer.get_height()
    rect = Gegl.Rectangle.new(0, 0, w, h)
    data = bytes(layer.get_buffer().get(rect, 1.0, "R'G'B'A u8",
                                        Gegl.AbyssPolicy.CLAMP))
    return off_x, off_y, w, h, data


def compute_quantization(image, layer_materials, counts, base_palette,
                         material_table=None, protected=None, uniquify=True):
    """Run the full quantization plan over an image's material layers.

    layer_materials: [(layer, material_value)] — already filtered/overridden.
    counts: {material_value: target color count}

    Returns a plan dict: palette, table, assignments, indices (bytes, full
    canvas, 0 = untouched/UNDEF background), width, height, stats.
    """
    width, height = image.get_width(), image.get_height()
    sources = []          # (layer order kept), with raw pixel data
    material_colors = {}  # material -> Counter of opaque colors
    for layer, material in layer_materials:
        off_x, off_y, w, h, data = read_layer_rgba(layer)
        sources.append((material, off_x, off_y, w, h, data))
        counter = material_colors.setdefault(material, Counter())
        for p in range(w * h):
            o = p * 4
            if data[o + 3] >= ALPHA_THRESHOLD:
                counter[(data[o], data[o + 1], data[o + 2])] += 1

    material_pixels = {m: list(c.elements()) for m, c in material_colors.items()
                       if c}
    palette, table, assignments = plan_quantization(
        material_pixels, counts, base_palette,
        material_table=material_table, protected=protected)
    if uniquify:
        from .colorops import uniquify_palette
        palette = uniquify_palette(palette)

    # remap bottom-up so upper layers overwrite, like on canvas
    indices = bytearray(width * height)
    luts = {}
    for material, off_x, off_y, w, h, data in reversed(sources):
        allowed = assignments.get(material)
        if not allowed:
            continue
        lut = luts.setdefault(material, {})
        for p in range(w * h):
            o = p * 4
            if data[o + 3] < ALPHA_THRESHOLD:
                continue
            x = off_x + p % w
            y = off_y + p // w
            if not (0 <= x < width and 0 <= y < height):
                continue
            color = (data[o], data[o + 1], data[o + 2])
            idx = lut.get(color)
            if idx is None:
                idx = nearest_color_index(color, palette, allowed)
                lut[color] = idx
            indices[y * width + x] = idx

    stats = []
    for material, allowed in assignments.items():
        pool = [i for i in allowed if i >= 188]
        stats.append(
            f"{MATERIAL_NAMES.get(material, material)}: "
            f"{len(material_colors[material])} unique colors -> "
            f"{len(allowed)} slots ({len(allowed) - len(pool)} reused, "
            f"{len(pool)} from 188-235)")
    return {
        'palette': palette,
        'table': table,
        'assignments': assignments,
        'indices': bytes(indices),
        'width': width,
        'height': height,
        'stats': stats,
    }


def materialize(plan, name='quantized'):
    """Create the indexed GIMP image from a plan (exact indices, via PNG)."""
    import os
    fd, tmp_name = tempfile.mkstemp(suffix='.png', prefix='liero-quant-')
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write_indexed_png(tmp, plan['width'], plan['height'],
                          plan['indices'], Palette(name, plan['palette']))
        image = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE,
                               Gio.File.new_for_path(str(tmp)))
    finally:
        tmp.unlink(missing_ok=True)
    image.get_layers()[0].set_name(name)
    return image


# --- dialog -------------------------------------------------------------------

gi.require_version('GimpUi', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import GimpUi, Gtk  # noqa: E402

from .defaults import ANIMATED_INDICES  # noqa: E402
from .formats import default_palette  # noqa: E402
from .gimp_colors import make_gimp_palette, rgb8_from_color  # noqa: E402
from .material import materials_from_entry_names, animated_from_entry_names  # noqa: E402
from .palette_grid import PaletteGrid  # noqa: E402

DEFAULT_COUNT = 8
SKIP = '__skip__'


class QuantizeDialog:
    """Material-aware quantization: assign layers, pick counts, preview, create."""

    RESP_CREATE = 100

    def __init__(self, image):
        self.image = image
        self.plan = None

        self.dialog = GimpUi.Dialog(title='Quantize by Liero Material')
        self.dialog.add_button('_Close', Gtk.ResponseType.CANCEL)
        self.create_btn = self.dialog.add_button('Create _indexed image', self.RESP_CREATE)
        self.create_btn.set_sensitive(False)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, margin=12)
        self.dialog.get_content_area().add(hbox)

        # ---- left: layer assignments + counts + options ---------------------
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hbox.pack_start(left, False, False, 0)

        left.pack_start(Gtk.Label(label='Layer / group materials:', xalign=0),
                        False, False, 0)
        self.layer_rows = []  # (layer, combo)
        layer_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for layer, guess in scan_material_layers(image):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label(label=layer.get_name() or '(unnamed)', xalign=0)
            label.set_width_chars(22)
            label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            row.pack_start(label, True, True, 0)
            combo = Gtk.ComboBoxText()
            combo.append(SKIP, '— skip —')
            for mat_name, mat_value in MATERIAL.items():
                combo.append(str(mat_value), mat_name)
            combo.set_active_id(str(guess) if guess is not None else SKIP)
            combo.connect('changed', lambda _c: self._rebuild_counts())
            row.pack_start(combo, False, False, 0)
            self.layer_rows.append((layer, combo))
            layer_list.pack_start(row, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_size_request(-1, min(220, 30 * max(1, len(self.layer_rows))))
        scroll.add(layer_list)
        left.pack_start(scroll, False, False, 0)

        left.pack_start(Gtk.Label(label='Colors per material:', xalign=0),
                        False, False, 0)
        self.counts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left.pack_start(self.counts_box, False, False, 0)
        self.count_spins = {}

        left.pack_start(Gtk.Label(label='Base palette:', xalign=0), False, False, 0)
        self.palette_combo = Gtk.ComboBoxText()
        self.palette_combo.append('__default__', 'Classic Liero default')
        for pal in Gimp.palettes_get_list(''):
            if pal.get_color_count() == 256:
                self.palette_combo.append(pal.get_name(), pal.get_name())
        self.palette_combo.set_active_id('__default__')
        left.pack_start(self.palette_combo, False, False, 0)

        self.protect_worm = Gtk.CheckButton(label='Keep worm slots untouched')
        self.protect_worm.set_active(True)
        left.pack_start(self.protect_worm, False, False, 0)
        self.unique_check = Gtk.CheckButton(label='Unique colors')
        self.unique_check.set_active(True)
        left.pack_start(self.unique_check, False, False, 0)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_row.pack_start(Gtk.Label(label='Output name:', xalign=0), False, False, 0)
        self.name_entry = Gtk.Entry(text=f"{image.get_name() or 'image'} quantized")
        name_row.pack_start(self.name_entry, True, True, 0)
        left.pack_start(name_row, False, False, 0)

        quant_btn = Gtk.Button(label='Quantize (preview)')
        quant_btn.connect('clicked', self._on_quantize)
        left.pack_start(quant_btn, False, False, 0)

        self.stats = Gtk.Label(xalign=0)
        self.stats.set_line_wrap(True)
        self.stats.set_size_request(320, -1)
        left.pack_start(self.stats, False, False, 0)

        # ---- right: planned palette + preview --------------------------------
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hbox.pack_start(right, True, True, 0)
        base = default_palette().colors
        self.grid = PaletteGrid(base, DEFAULT_MATERIALS)
        frame = Gtk.Frame()
        frame.add(self.grid.widget)
        right.pack_start(frame, False, False, 0)

        from .studio import PreviewCanvas
        self.canvas = PreviewCanvas()
        pscroll = Gtk.ScrolledWindow()
        pscroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        pscroll.set_size_request(420, 300)
        pscroll.add(self.canvas.widget)
        self.canvas.hadj = pscroll.get_hadjustment()
        self.canvas.vadj = pscroll.get_vadjustment()
        right.pack_start(pscroll, True, True, 0)

        self._rebuild_counts()

    # ---- ui state -----------------------------------------------------------

    def _assignments(self):
        out = []
        for layer, combo in self.layer_rows:
            active = combo.get_active_id()
            if active and active != SKIP:
                out.append((layer, int(active)))
        return out

    def _rebuild_counts(self):
        used = sorted({m for _l, m in self._assignments()})
        for child in self.counts_box.get_children():
            child.destroy()
        old = dict(self.count_spins)
        self.count_spins = {}
        for material in used:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.pack_start(Gtk.Label(label=MATERIAL_NAMES.get(material, str(material)),
                                     xalign=0), True, True, 0)
            spin = Gtk.SpinButton.new_with_range(1, 48, 1)
            spin.set_value(old[material].get_value() if material in old else DEFAULT_COUNT)
            self.count_spins[material] = spin
            row.pack_start(spin, False, False, 0)
            self.counts_box.pack_start(row, False, False, 0)
        self.counts_box.show_all()

    def _base_palette(self):
        source = self.palette_combo.get_active_id()
        if source == '__default__':
            return default_palette().colors, list(DEFAULT_MATERIALS), set(ANIMATED_INDICES)
        pal = next((p for p in Gimp.palettes_get_list(source)
                    if p.get_name() == source), None)
        if pal is None:
            return default_palette().colors, list(DEFAULT_MATERIALS), set(ANIMATED_INDICES)
        colors = [rgb8_from_color(c) for c in pal.get_colors()]
        while len(colors) < 256:
            colors.append((0, 0, 0))
        names = [pal.get_entry_name(i)[1] for i in range(pal.get_color_count())]
        table = materials_from_entry_names(names) or list(DEFAULT_MATERIALS)
        animated = animated_from_entry_names(names) or set(ANIMATED_INDICES)
        return colors[:256], table, animated

    # ---- actions --------------------------------------------------------------

    def _on_quantize(self, _btn):
        assignments = self._assignments()
        if not assignments:
            self.stats.set_text('No layers assigned to a material.')
            return
        counts = {m: int(s.get_value()) for m, s in self.count_spins.items()}
        base_colors, table, animated = self._base_palette()
        self._animated = animated
        protected = set(PROTECTED_BY_DEFAULT) if self.protect_worm.get_active() else set()
        try:
            self.plan = compute_quantization(
                self.image, assignments, counts, base_colors,
                material_table=table, protected=protected,
                uniquify=self.unique_check.get_active())
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.stats.set_text(f"Quantization failed: {exc}")
            return
        self.grid.colors = list(self.plan['palette'])
        self.grid.table = list(self.plan['table'])
        self.grid.animated = set(animated)
        allocated = {i for allowed in self.plan['assignments'].values() for i in allowed}
        self.grid.selected = allocated
        self.grid.queue_draw()
        self.canvas.set_pixels(self.plan['width'], self.plan['height'],
                               self.plan['indices'])
        self.canvas.render(self.plan['palette'])
        self.stats.set_text("\n".join(self.plan['stats'])
                            + f"\nAllocated slots are selected on the grid.")
        self.create_btn.set_sensitive(True)

    def run(self):
        self.dialog.show_all()
        created = None
        try:
            while True:
                response = self.dialog.run()
                if response == self.RESP_CREATE and self.plan is not None:
                    name = self.name_entry.get_text().strip() or 'quantized'
                    out = materialize(self.plan, name=name)
                    make_gimp_palette(name, self.plan['palette'],
                                      table=self.plan['table'],
                                      animated=getattr(self, '_animated', None))
                    Gimp.Display.new(out)
                    Gimp.displays_flush()
                    created = out
                break
        finally:
            self.dialog.destroy()
        return created
