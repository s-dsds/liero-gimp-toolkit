# Liero GIMP Toolkit v0.1 Starter

This package contains specs and first-iteration code for three related projects:

1. **Palette management**: import/export/validate classic Liero palettes and split them by material.
2. **Palette manipulation with preview**: a future floating "Liero Palette Lab" for adjusting material palette ranges while previewing a level.
3. **Material-aware quantization**: quantize RGB layer groups into a forked Liero palette without breaking material semantics.

## Important design corrections

- `UNDEF` is **not** treated as unused. It is a valid shoot-through material.
- Indices `188-235` are treated as **preferred replacement candidates**, not automatically unused.
- `defaultColorAnim = [129,132,133,136,152,159,168,171]` is an independent animation flag, not a material.
- Worm and animated indices are protected by default.
- Classic Liero material semantics are hardcoded for the default workflow.

## Contents

```text
liero_core/
  defaults.py      hardcoded material table and animation indices
  material.py      layer-name classification and index metadata
  palette.py       GPL / indexed PNG palette import-export helpers
  quantizer.py     material-aware quantization helpers
plugins/
  liero_palette_management.py
  liero_palette_lab.py
  liero_material_quantize.py
specs/
  01-palette-management.md
  02-palette-manipulation-preview.md
  03-material-quantization.md
examples/
  material_counts.json
liero_palette_cli.py
install-linux-user.sh
tests/test_core.py
```

## Status

The core Python modules are usable outside GIMP and include a small test suite. The GIMP scripts are **first-iteration GIMP 3 plug-in drafts**. They define the architecture and menu targets, but the more sensitive pieces—native GIMP file dialogs, live preview plumbing, and pixel extraction from layer groups—should be finished while testing inside your exact GIMP 3.2 Python GI environment.

GIMP 3's Python plug-in API maps to the C API through GObject Introspection, and GIMP's current docs recommend Python 3 as one of the main cross-platform plug-in languages. GIMP 3 images expose palette/colormap APIs for indexed images, and `Gimp.Image.set_palette()` changes the colormap of indexed images. GIMP 3 also has drawable filters for GEGL operations, although this toolkit is broader than a single GEGL filter.

## Try the core without GIMP

```bash
cd liero-gimp-toolkit-v0.1
python3 -m pytest tests
python3 liero_palette_cli.py validate some_palette.gpl
python3 liero_palette_cli.py split some_palette.gpl out_palettes
```

For indexed PNG palette import, install Pillow:

```bash
python3 -m pip install pillow
```

## Install draft GIMP plug-ins on Linux

```bash
cd liero-gimp-toolkit-v0.1
./install-linux-user.sh
```

Restart GIMP and look for the `Liero` menu.

## Next implementation steps

### Palette management

- Add real `GimpUi.ProcedureDialog` file/folder arguments.
- Add command to apply an imported `.gpl` to the current indexed image via `Image.set_palette()`.
- Add JSON validation report export.

### Palette Lab

- Build the custom GTK window with material/index selectors.
- Read the current indexed image palette via `Image.get_palette()`.
- Preview changes on a duplicate image or temporary preview layer.
- Apply changes back with `Image.set_palette()`.

### Material quantizer

- Traverse the layer tree and render each material group to an RGB buffer.
- Collect material pixels, run `median_cut()`, allocate palette slots with `choose_palette_slots()`.
- Produce an indexed output layer/image that only uses allowed indices for each material.
- Add a review screen showing which palette indices changed.

## References used while shaping the GIMP side

- GIMP's Python plug-in tutorial describes Python 3 plug-ins and the GI mapping to `Gimp`/`GimpUi`.
- `Gimp.Image.get_palette()` returns an indexed image's colormap as a `GimpPalette`.
- `Gimp.Image.set_palette()` sets an indexed image's colormap from a `GimpPalette`.
- `GimpUi.ProcedureDialog` can auto-populate UI from procedure properties.
- `Gimp.DrawableFilter` is the GIMP 3 API family for applying GEGL operations non-destructively where supported.
