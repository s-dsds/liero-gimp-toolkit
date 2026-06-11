# Liero GIMP Toolkit v0.1 Starter

This package contains specs and first-iteration code for three related projects:

1. **Palette management**: import/export/validate classic Liero palettes and split them by material.
2. **Palette manipulation with preview**: a future floating "Liero Palette Lab" for adjusting material palette ranges while previewing a level.
3. **Material-aware quantization**: quantize RGB layer groups into a forked Liero palette without breaking material semantics.

## Important design corrections

- `UNDEF` is **not** treated as unused. It is a valid shoot-through material.
- Indices `188-235` are treated as **preferred replacement candidates**, not automatically unused.
- `colorAnim = [129,131,133,136,152,159,168,171]` is a flat list of `(from, to)` **range pairs** (4 ranges, 19 animated indices), independent of materials. Confirmed against LIERO.EXE 1.33 (offset 0x1AF0C) and WebLiero's classic `mod.json`; the earlier `132` was a transcription error.
- Only worm indices are protected by default. Animated indices are informational — `colorAnim` is mod-configurable.
- Classic Liero material semantics are hardcoded for the default workflow. The material table is verified against LIERO.EXE 1.33 and wgetch's WebLiero material reference (which agree byte-for-byte); the originally transcribed table had two blocks (160-171, 176-184) shifted by 4.

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

- **Palette Studio: functional.** The merged palette tool (menu *Liero > Palette Studio…*, also opened by *Import Palette…* with a file preloaded). Sources: palette files (`.gpl`/`.png`/`.lpl`/`.wlsprt`/`.lev`/`LIERO.EXE`), the image colormap, or any 256-color GIMP palette (toolkit palettes carry materials and `ANIM` flags in entry names). Material + colorAnim editing (assign, toggle animated, room-script text field), color editing (double-click), adjustments (hue/saturation/brightness/contrast/temperature, colorize mode, make-gradient), stacked zoomable previews (`.lev`/`.wlsprt`/`.png`, wheel zoom anchored on pointer, drag pan, click to pick an index, focus mode, color animation). Save as GIMP palette and/or apply to the image (unique-colors checkbox, on by default). Float-precision pipeline; Commit chains adjustments.
- **Palette management: functional.** Export by material, validate, the CLI, and scripted (noninteractive) import. Verified headlessly against Flatpak GIMP 3.2.4.
- **Material quantizer: functional.** *Liero > Quantize by Material…* on an RGB image: scans top-level layers/groups for material names (override per layer in the dialog), per-material color counts, base palette (classic default or any toolkit GIMP palette — materials/ANIM recovered from entry names), keep-worm-slots and unique-colors options. *Quantize (preview)* shows the planned palette on the grid (allocated slots selected) and the quantized pixels on a zoomable canvas — view as quantized colors, **material mask** (flat color per material) or **bad pixels** (isolated-material pixels highlighted and counted, e.g. a lone BG pixel inside rock), optionally filtered to one material. *Apply to layers* quantizes each assigned layer/group **in place** via the sibling `custom:palette-quantize` GEGL op (python fallback) — the XCF stays RGB/editable but palette-correct, one undo step reverts. *Create indexed image* produces a new image (exact indices via an internally-written indexed PNG — GIMP's own convert loses uniquify nudges) plus the forked palette resource. Dialog settings persist in an image parasite (saved into the XCF), so re-quantizing is two clicks. Allocation: reuse close same-material slots first, then the 188-235 pool (only slots still UNDEF).

### Supported palette sources

| Source | Notes |
|---|---|
| `.gpl` | GIMP palette |
| indexed `.png` | via Pillow on the CLI, via GIMP itself in the plug-in |
| `.lpl` | raw 768-byte RGB (LieroKit/wledit); 6-bit dumps auto-detected |
| `.wlsprt` | WebLiero sprite file (default palette when none embedded) |
| `.lev` | POWERLEVEL variant; plain levels fall back to the default palette |
| `LIERO.EXE` | decompressed 1.33 exe: palette @132774, materials @0x1C2E0, colorAnim @0x1AF0C (offsets from OpenLiero's tc_tool) |

The CLI can also write `.lpl` files and patch a palette **into** a `.wlsprt` or `.lev` (turning it into a POWERLEVEL).

### Palette editor (Import Palette...)

Interactive import opens a 16x16 swatch grid of the loaded palette:

- each swatch shows a material badge (D, D2, R, B, BD, B2, S, W; blank = UNDEF) and a dot on animated indices; hover for index details
- *Toggle animated* marks/unmarks the selection as color-animated; the info panel shows the resulting `colorAnim` pair list, and created palettes carry an `ANIM` token in entry names (e.g. `129 UNDEF ANIM`) so the flag survives round-trips; importing LIERO.EXE seeds the set from the exe's real colorAnim
- click to select, Ctrl+click to toggle, Shift+click for ranges, or use *Select material* to grab a whole material at once
- *Assign to selection* re-materials the selected indices on the fly
- double-click a swatch to edit its color
- a material-table text field below the grid: paste a room-script expression (`materials: defaultMaterials.map(noUndef).map(replaceMatIndexBy(MATERIAL.BG,..._range(189,208))),` — helpers `noUndef`, `undefToDirt`, `replaceMatIndexBy`, `..._range` are understood) or a plain array, hit *Apply text to grid*; after edits the field shows the regenerated copy-ready expression
- *Save materials…* writes the (edited) table to a JSON or room-script `.js` file (fallback to the text field)
- *Create Palette* creates the GIMP palette (entry names carry the materials, e.g. `042 ROCK`) and can apply it to the active indexed image — applied colormaps are automatically made **unique** (minimal nudges on duplicates) since GIMP can't tell identical colors apart

### WebLiero Extended custom material tables

WLE rooms set custom materials per map via `WLROOM.setMaterials(<256-int array>)` in the room script (`mapsettings.js` style). The toolkit supports this everywhere a material table matters:

- `validate`/`split` (CLI) and Export by Material / Validate (GIMP) take an optional materials file: either a JSON array or any JS-ish text containing the array (you can point it straight at a room-script file).
- `materials` (CLI) converts a table to JSON, or with `--js` emits a paste-ready room-script expression (`defaultMaterials.map(noUndef).map(replaceMatIndexBy(MATERIAL.ROCK,...))`), falling back to a plain array literal when the table is too different.
- With a custom table, protected indices follow **that table's** worm indices.

GIMP 3's Python plug-in API maps to the C API through GObject Introspection, and GIMP's current docs recommend Python 3 as one of the main cross-platform plug-in languages. GIMP 3 images expose palette/colormap APIs for indexed images, and `Gimp.Image.set_palette()` changes the colormap of indexed images. GIMP 3 also has drawable filters for GEGL operations, although this toolkit is broader than a single GEGL filter.

## Try the core without GIMP

```bash
cd liero-gimp-toolkit
uv venv .venv && uv pip install --python .venv/bin/python pytest pillow
.venv/bin/python -m pytest tests
.venv/bin/python liero_palette_cli.py validate sprites.wlsprt
.venv/bin/python liero_palette_cli.py split LIERO.EXE out_palettes
.venv/bin/python liero_palette_cli.py convert sprites.wlsprt palette.gpl
.venv/bin/python liero_palette_cli.py apply new_palette.gpl map.lev -o powermap.lev
```

(Pillow is only needed for indexed PNG palette import.)

## Install GIMP plug-ins on Linux

Works with native or Flatpak GIMP 3.x (tested against Flatpak GIMP 3.2.4):

```bash
cd liero-gimp-toolkit
./install-linux-user.sh
```

Restart GIMP and look for the `Liero` menu.

To verify registration without opening the UI (Flatpak):

```bash
flatpak run org.gimp.GIMP -i -d -f --batch-interpreter=plug-in-script-fu-eval -b '(gimp-quit 0)'
grep -ao 'python-fu-liero[a-z-]*' ~/.config/GIMP/3.2/pluginrc | sort -u
```

## Next implementation steps

### Palette management

- JSON validation report export from the GIMP procedure (CLI already does it).

### Palette Lab

- Ramp smoothing, gradient remapping, per-channel curves (later spec items).
- Optional color animation preview (cycle the colorAnim ranges).

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
