#!/usr/bin/env python3
"""GIMP 3 plug-in draft: Liero material-aware quantization.

The core quantizer is implemented in liero_core.quantizer. This GIMP-side file is
an integration skeleton for scanning layer names, collecting pixels, and applying
an indexed output. Pixel extraction differs across GIMP GI builds, so keep this
as a first iteration and test with your installed GIMP 3.2 Python console.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

try:
    import gi
    gi.require_version('Gimp', '3.0')
    gi.require_version('GimpUi', '3.0')
    from gi.repository import Gimp, GimpUi, GLib
except Exception:
    Gimp = GimpUi = GLib = None

PLUGIN_DIR = Path(__file__).resolve().parent
CORE_DIR = PLUGIN_DIR.parent
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from liero_core.defaults import MATERIAL, MATERIAL_NAMES
from liero_core.material import classify_name
from liero_core.quantizer import median_cut, choose_palette_slots, remap_pixels_to_indices


def scan_group_materials(root_items):
    """Return [(item, material)] by matching item names.

    In GIMP this should be called with image.get_layers() and recursively with
    group.get_children() where available.
    """
    out = []
    stack = list(root_items)
    while stack:
        item = stack.pop(0)
        name = item.get_name() if hasattr(item, 'get_name') else str(item)
        material = classify_name(name)
        if material is not None:
            out.append((item, material))
        if hasattr(item, 'get_children'):
            try:
                stack.extend(item.get_children())
            except Exception:
                pass
    return out


def plan_material_palette(base_palette, material_pixels, material_counts):
    """Pure-Python planning helper.

    material_pixels: dict[int, list[Color]]
    material_counts: dict[int, int]
    """
    palette = list(base_palette[:256])
    material_table = None
    assignments = {}
    for material, pixels in material_pixels.items():
        k = int(material_counts.get(material, 0))
        if k <= 0 or not pixels:
            continue
        reps = median_cut(pixels, k)
        allowed, palette, material_table = choose_palette_slots(reps, palette, material, material_table=material_table)
        assignments[material] = allowed
    return palette, material_table, assignments


if Gimp is not None:
    class LieroMaterialQuantize(Gimp.PlugIn):
        def do_query_procedures(self):
            return ['python-fu-liero-material-quantize']

        def do_create_procedure(self, name):
            proc = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
            proc.set_image_types('RGB*')
            proc.set_menu_label('Quantize by Liero Material...')
            proc.add_menu_path('<Image>/Liero')
            proc.set_documentation('Quantize layer groups by Liero material.', 'First iteration integration skeleton.', name)
            proc.set_attribution('AB Tasty AI / generated starter', 'OpenAI', '2026')
            return proc

        def run(self, procedure, run_mode, image, drawables, config, data):
            # v0.1: scanning shell only. Next step is pixel extraction from material groups.
            groups = scan_group_materials(image.get_layers() if hasattr(image, 'get_layers') else [])
            print('Detected Liero material groups:', [(g.get_name(), MATERIAL_NAMES.get(m, m)) for g, m in groups])
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    Gimp.main(LieroMaterialQuantize.__gtype__, sys.argv)
