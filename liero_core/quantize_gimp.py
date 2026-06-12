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

from .defaults import DEFAULT_MATERIALS, MATERIAL, MATERIAL_NAMES, MATERIAL_GROUPS, PROTECTED_BY_DEFAULT  # noqa: E402
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

    layer_materials: [(layer, key)] — key is a material value or 'group:GID'
        (several materials drawn as one group, e.g. DIRT + DIRT_2).
    counts: {key: target color count}

    Returns a plan dict: palette, table, assignments, indices (bytes, full
    canvas, 0 = untouched/UNDEF background), width, height, stats.
    """
    width, height = image.get_width(), image.get_height()
    sources = []        # (layer order kept), with raw pixel data
    key_colors = {}     # key -> Counter of opaque colors
    selectors = {}
    for layer, key in layer_materials:
        _label, target, members = resolve_selection_key(key)
        selectors[key] = (target, set(members))
        off_x, off_y, w, h, data = read_layer_rgba(layer)
        sources.append((key, off_x, off_y, w, h, data))
        counter = key_colors.setdefault(key, Counter())
        for p in range(w * h):
            o = p * 4
            if data[o + 3] >= ALPHA_THRESHOLD:
                counter[(data[o], data[o + 1], data[o + 2])] += 1

    key_pixels = {k: list(c.elements()) for k, c in key_colors.items() if c}
    palette, table, assignments = plan_quantization(
        key_pixels, counts, base_palette,
        material_table=material_table, protected=protected,
        selectors=selectors)
    if uniquify:
        from .colorops import uniquify_palette
        palette = uniquify_palette(palette)

    # remap bottom-up so upper layers overwrite, like on canvas
    indices = bytearray(width * height)
    luts = {}
    for key, off_x, off_y, w, h, data in reversed(sources):
        allowed = assignments.get(key)
        if not allowed:
            continue
        lut = luts.setdefault(key, {})
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
    for key, allowed in assignments.items():
        label = resolve_selection_key(key)[0]
        pool = [i for i in allowed if i >= 188]
        stats.append(
            f"{label}: {len(key_colors[key])} unique colors -> "
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

import json  # noqa: E402

from .defaults import ANIMATED_INDICES  # noqa: E402
from .formats import default_palette  # noqa: E402
from .gimp_colors import make_gimp_palette, rgb8_from_color  # noqa: E402
from .material import materials_from_entry_names, animated_from_entry_names  # noqa: E402
from .palette_grid import PaletteGrid  # noqa: E402
from .quantizer import find_isolated_pixels  # noqa: E402

DEFAULT_COUNT = 8
SKIP = '__skip__'
PARASITE_NAME = 'liero-quantize-settings'

# flat colors for the material-mask preview mode
MATERIAL_MASK_COLORS = {
    MATERIAL['UNDEF']: (20, 20, 20),
    MATERIAL['DIRT']: (139, 69, 19),
    MATERIAL['DIRT_2']: (205, 133, 63),
    MATERIAL['ROCK']: (128, 128, 128),
    MATERIAL['BG']: (30, 60, 200),
    MATERIAL['BG_DIRT']: (0, 150, 150),
    MATERIAL['BG_DIRT_2']: (100, 200, 200),
    MATERIAL['BG_SEESHADOW']: (128, 0, 160),
    MATERIAL['WORM']: (0, 220, 0),
}
BAD_PIXEL_COLOR = (255, 0, 255)
FILTERED_OUT_COLOR = (35, 35, 35)


def leaf_drawables(layer):
    """All non-group drawables under a layer (the layer itself if plain)."""
    if hasattr(layer, 'get_children'):
        try:
            children = layer.get_children()
        except Exception:
            children = None
        if children:
            out = []
            for child in children:
                out.extend(leaf_drawables(child))
            return out
    return [layer]


def palette_hex_string(colors):
    return ';'.join('#%02x%02x%02x' % tuple(c) for c in colors)


def resolve_selection_key(key):
    """A dialog selection key -> (label, target_material, members tuple).

    Keys are either a material value (int / str int) or ``group:GID``.
    """
    if isinstance(key, str) and key.startswith('group:'):
        label, members = MATERIAL_GROUPS[key[6:]]
        return label, members[0], tuple(members)
    material = int(key)
    return MATERIAL_NAMES.get(material, str(material)), material, (material,)


def apply_palette_to_drawable(drawable, allowed_colors, destructive=False):
    """Quantize one drawable to the given colors.

    Uses the sibling C plug-in's GEGL op (custom:palette-quantize) when
    available — non-destructively as a layer effect by default, or baked in
    with ``destructive=True``. The pure-python fallback is always destructive.
    """
    try:
        flt = Gimp.DrawableFilter.new(drawable, 'custom:palette-quantize',
                                      'liero material quantize')
        cfg = flt.get_config()
        cfg.set_property('palette', palette_hex_string(allowed_colors))
        flt.update()
        if destructive:
            drawable.merge_filter(flt)
        else:
            drawable.append_filter(flt)
        return 'gegl'
    except Exception:
        pass
    if not destructive:
        return 'unavailable'  # can't do non-destructive without the GEGL op
    # fallback: nearest-color remap, alpha preserved
    w, h = drawable.get_width(), drawable.get_height()
    rect = Gegl.Rectangle.new(0, 0, w, h)
    buf = drawable.get_buffer()
    data = bytearray(buf.get(rect, 1.0, "R'G'B'A u8", Gegl.AbyssPolicy.CLAMP))
    lut = {}
    for p in range(w * h):
        o = p * 4
        if data[o + 3] < ALPHA_THRESHOLD:
            continue
        color = (data[o], data[o + 1], data[o + 2])
        new = lut.get(color)
        if new is None:
            new = min(allowed_colors,
                      key=lambda c: (c[0] - color[0]) ** 2
                      + (c[1] - color[1]) ** 2 + (c[2] - color[2]) ** 2)
            lut[color] = new
        data[o], data[o + 1], data[o + 2] = new
    buf.set(rect, "R'G'B'A u8", bytes(data))
    buf.flush()
    drawable.update(0, 0, w, h)
    return 'python'


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
            for gid, (label_text, _members) in MATERIAL_GROUPS.items():
                combo.append(f"group:{gid}", label_text)
            combo.set_active_id(str(guess) if guess is not None else SKIP)
            combo.connect('changed', lambda _c: self._rebuild_counts())
            row.pack_start(combo, False, False, 0)
            self.layer_rows.append((layer, combo))
            layer_list.pack_start(row, False, False, 0)
        if len(self.layer_rows) <= 7:
            left.pack_start(layer_list, False, False, 0)
        else:
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroll.set_size_request(-1, 320)
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
        self.nde_check = Gtk.CheckButton(label='Apply as non-destructive layer effects')
        self.nde_check.set_active(True)
        self.nde_check.set_tooltip_text(
            'Attach the palette-quantize op as an editable layer effect '
            '(needs the GEGL op installed); uncheck to bake the colors into '
            'the layers (python fallback available)')
        left.pack_start(self.nde_check, False, False, 0)

        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        name_row.pack_start(Gtk.Label(label='Output name:', xalign=0), False, False, 0)
        self.name_entry = Gtk.Entry(text=f"{image.get_name() or 'image'} quantized")
        name_row.pack_start(self.name_entry, True, True, 0)
        left.pack_start(name_row, False, False, 0)

        quant_btn = Gtk.Button(label='Quantize (preview)')
        quant_btn.connect('clicked', self._on_quantize)
        left.pack_start(quant_btn, False, False, 0)

        self.apply_btn = Gtk.Button(label='Apply to layers (edits this image)')
        self.apply_btn.set_tooltip_text(
            'Quantize each assigned layer/group IN PLACE to its material '
            'colors (via the palette-quantize GEGL op when installed) - the '
            'XCF stays RGB and editable, but palette-correct, so exporting '
            'later is just File > Export')
        self.apply_btn.set_sensitive(False)
        self.apply_btn.connect('clicked', self._on_apply_layers)
        left.pack_start(self.apply_btn, False, False, 0)

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

        # manual material edits on the palette: feed both the plan (export,
        # mask view, bad pixels) and the next Quantize run (reuse targets)
        self._table_override = {}
        assign_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.assign_combo = Gtk.ComboBoxText()
        for mat_name, mat_value in MATERIAL.items():
            self.assign_combo.append(str(mat_value), mat_name)
        self.assign_combo.set_active(0)
        assign_row.pack_start(self.assign_combo, True, True, 0)
        assign_btn = Gtk.Button(label='Assign material to selection')
        assign_btn.connect('clicked', self._on_assign_material)
        assign_row.pack_start(assign_btn, False, False, 0)
        right.pack_start(assign_row, False, False, 0)

        from .studio import PreviewCanvas
        self.canvas = PreviewCanvas()
        pscroll = Gtk.ScrolledWindow()
        pscroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        pscroll.set_size_request(420, 300)
        pscroll.add(self.canvas.widget)
        self.canvas.hadj = pscroll.get_hadjustment()
        self.canvas.vadj = pscroll.get_vadjustment()
        right.pack_start(pscroll, True, True, 0)

        view_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        view_row.pack_start(Gtk.Label(label='View:', xalign=0), False, False, 0)
        self.view_combo = Gtk.ComboBoxText()
        for vid, label in (('colors', 'Quantized colors'),
                           ('mask', 'Material mask'),
                           ('bad', 'Bad pixels (isolated material)')):
            self.view_combo.append(vid, label)
        self.view_combo.set_active_id('colors')
        self.view_combo.connect('changed', lambda _c: self._render_plan_preview())
        view_row.pack_start(self.view_combo, True, True, 0)
        self.filter_combo = Gtk.ComboBoxText()
        self.filter_combo.append('all', 'All materials')
        self.filter_combo.set_active_id('all')
        self.filter_combo.connect('changed', lambda _c: self._render_plan_preview())
        view_row.pack_start(self.filter_combo, True, True, 0)
        right.pack_start(view_row, False, False, 0)

        self._rebuild_counts()
        self._restore_settings()

    # ---- ui state -----------------------------------------------------------

    def _assignments(self):
        out = []
        for layer, combo in self.layer_rows:
            active = combo.get_active_id()
            if active and active != SKIP:
                out.append((layer, active))
        return out

    def _default_count(self, key):
        """Default color count = slots that key's materials own in the base palette."""
        if not hasattr(self, 'palette_combo'):  # combos fire during construction
            return DEFAULT_COUNT
        _colors, table, _anim = self._base_palette()
        _label, _target, members = resolve_selection_key(key)
        n = sum(1 for m in table if m in members)
        return max(1, min(64, n or DEFAULT_COUNT))

    def _rebuild_counts(self):
        used = sorted({key for _l, key in self._assignments()})
        for child in self.counts_box.get_children():
            child.destroy()
        old = dict(self.count_spins)
        self.count_spins = {}
        for key in used:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            row.pack_start(Gtk.Label(label=resolve_selection_key(key)[0],
                                     xalign=0), True, True, 0)
            spin = Gtk.SpinButton.new_with_range(1, 64, 1)
            spin.set_value(old[key].get_value() if key in old
                           else self._default_count(key))
            self.count_spins[key] = spin
            row.pack_start(spin, False, False, 0)
            self.counts_box.pack_start(row, False, False, 0)
        self.counts_box.show_all()

    def _base_palette(self):
        source = self.palette_combo.get_active_id()
        colors, table, animated = (default_palette().colors,
                                   list(DEFAULT_MATERIALS), set(ANIMATED_INDICES))
        if source != '__default__':
            pal = next((p for p in Gimp.palettes_get_list(source)
                        if p.get_name() == source), None)
            if pal is not None:
                colors = [rgb8_from_color(c) for c in pal.get_colors()]
                while len(colors) < 256:
                    colors.append((0, 0, 0))
                colors = colors[:256]
                names = [pal.get_entry_name(i)[1]
                         for i in range(pal.get_color_count())]
                table = materials_from_entry_names(names) or list(DEFAULT_MATERIALS)
                animated = animated_from_entry_names(names) or set(ANIMATED_INDICES)
        table = list(table)
        for idx, material in self._table_override.items():
            table[idx] = material
        return colors, table, animated

    def _on_assign_material(self, _btn):
        if not self.grid.selected:
            self.stats.set_text('Select swatches on the grid first.')
            return
        material = int(self.assign_combo.get_active_id())
        for i in self.grid.selected:
            self._table_override[i] = material
        if self.plan is not None:
            for i in self.grid.selected:
                self.plan['table'][i] = material
            self.grid.table = list(self.plan['table'])
            self._bad = find_isolated_pixels(self.plan['indices'], self.plan['width'],
                                             self.plan['height'], self.plan['table'])
            self._render_plan_preview()
        else:
            table = list(self.grid.table)
            for i in self.grid.selected:
                table[i] = material
            self.grid.table = table
        self.grid.queue_draw()
        self.stats.set_text(f"Assigned {MATERIAL_NAMES.get(material, material)} to "
                            f"{len(self.grid.selected)} indices (applies to the "
                            "plan and to the next Quantize run).")

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
        self._bad = find_isolated_pixels(self.plan['indices'], self.plan['width'],
                                         self.plan['height'], self.plan['table'])
        # material filter options follow the plan (union of selected members)
        current = self.filter_combo.get_active_id()
        materials = sorted({m for key in self.plan['assignments']
                            for m in resolve_selection_key(key)[2]})
        self.filter_combo.remove_all()
        self.filter_combo.append('all', 'All materials')
        for material in materials:
            self.filter_combo.append(str(material),
                                     MATERIAL_NAMES.get(material, str(material)))
        self.filter_combo.set_active_id(
            current if current in {str(m) for m in materials} else 'all')
        self._render_plan_preview()
        self.stats.set_text("\n".join(self.plan['stats'])
                            + f"\nBad pixels (isolated material): {len(self._bad)}"
                            + "\nAllocated slots are selected on the grid.")
        self.create_btn.set_sensitive(True)
        self.apply_btn.set_sensitive(True)
        self._save_settings()

    def _render_plan_preview(self):
        if self.plan is None:
            return
        mode = self.view_combo.get_active_id() or 'colors'
        filt = self.filter_combo.get_active_id() or 'all'
        table = self.plan['table']
        if mode == 'mask':
            lut = [MATERIAL_MASK_COLORS.get(table[i], (255, 255, 0))
                   for i in range(256)]
        else:
            lut = list(self.plan['palette'])
        if mode == 'bad':
            lut = [(r // 3, g // 3, b // 3) for r, g, b in lut]  # dim base
        if filt != 'all':
            material = int(filt)
            lut = [c if table[i] == material else FILTERED_OUT_COLOR
                   for i, c in enumerate(lut)]
        if mode != 'bad':
            self.canvas.render(lut)
            return
        # bad-pixel mode: positional overlay needs prebuilt RGB
        width, height = self.plan['width'], self.plan['height']
        indices = self.plan['indices']
        tables = [bytes(lut[i][ch] for i in range(256)) for ch in range(3)]
        rgb = bytearray(width * height * 3)
        rgb[0::3] = indices.translate(tables[0])
        rgb[1::3] = indices.translate(tables[1])
        rgb[2::3] = indices.translate(tables[2])
        for x, y in self._bad:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < width and 0 <= yy < height:
                        o = (yy * width + xx) * 3
                        rgb[o:o + 3] = bytes(BAD_PIXEL_COLOR)
        self.canvas.render_rgb(bytes(rgb))

    def _on_apply_layers(self, _btn):
        if self.plan is None:
            return
        palette = self.plan['palette']
        destructive = not self.nde_check.get_active()
        self.image.undo_group_start()
        used_gegl = used_python = unavailable = 0
        try:
            for layer, key in self._assignments():
                allowed = self.plan['assignments'].get(key)
                if not allowed:
                    continue
                colors = [palette[i] for i in allowed]
                for leaf in leaf_drawables(layer):
                    how = apply_palette_to_drawable(leaf, colors,
                                                    destructive=destructive)
                    if how == 'gegl':
                        used_gegl += 1
                    elif how == 'python':
                        used_python += 1
                    else:
                        unavailable += 1
        except Exception as exc:
            import traceback
            traceback.print_exc()
            self.stats.set_text(f"Apply failed: {exc}")
            return
        finally:
            self.image.undo_group_end()
            Gimp.displays_flush()
        if unavailable:
            self.stats.set_text(
                'Non-destructive apply needs the palette-quantize GEGL op '
                '(sibling plug-in) installed. Uncheck "non-destructive" to '
                'bake colors with the python fallback instead.')
            return
        via = []
        if used_gegl:
            mode = 'layer effects' if not destructive else 'baked'
            via.append(f"{used_gegl} via palette-quantize op ({mode})")
        if used_python:
            via.append(f"{used_python} baked via python remap")
        self.stats.set_text(
            f"Applied material palettes to layers ({', '.join(via)}).\n"
            + ("Layer effects are editable/removable per layer.\n" if not destructive else "")
            + "One undo step reverts all.")

    # ---- settings persistence (parasite, survives in the XCF) -----------------

    def _save_settings(self):
        data = {
            'layers': {layer.get_name() or '': combo.get_active_id()
                       for layer, combo in self.layer_rows},
            'counts': {str(k): int(s.get_value())
                       for k, s in self.count_spins.items()},
            'palette': self.palette_combo.get_active_id(),
            'protect_worm': self.protect_worm.get_active(),
            'unique': self.unique_check.get_active(),
            'nde': self.nde_check.get_active(),
            'overrides': {str(i): m for i, m in self._table_override.items()},
            'name': self.name_entry.get_text(),
        }
        try:
            parasite = Gimp.Parasite.new(PARASITE_NAME, 1,  # 1 = persistent
                                         json.dumps(data).encode())
            self.image.attach_parasite(parasite)
        except Exception:
            import traceback
            traceback.print_exc()

    def _restore_settings(self):
        try:
            parasite = self.image.get_parasite(PARASITE_NAME)
            if parasite is None:
                return
            data = json.loads(bytes(parasite.get_data()).decode())
        except Exception:
            return
        for layer, combo in self.layer_rows:
            saved = data.get('layers', {}).get(layer.get_name() or '')
            if saved:
                combo.set_active_id(saved)
        self._rebuild_counts()
        for key, spin in self.count_spins.items():
            if str(key) in data.get('counts', {}):
                spin.set_value(data['counts'][str(key)])
        if data.get('palette') and not self.palette_combo.set_active_id(data['palette']):
            self.palette_combo.set_active_id('__default__')
        self.protect_worm.set_active(data.get('protect_worm', True))
        self.unique_check.set_active(data.get('unique', True))
        self.nde_check.set_active(data.get('nde', True))
        self._table_override = {int(i): m
                                for i, m in data.get('overrides', {}).items()}
        if data.get('name'):
            self.name_entry.set_text(data['name'])

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
