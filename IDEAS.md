# Ideas & iteration backlog

Parked ideas, rough priorities. Things the user explicitly asked for are
marked **(requested)**.

## Project 3: material quantizer — DONE (v1), remaining ideas below

The core (median-cut, slot allocation, remapping in `liero_core/quantizer.py`)
is tested; the GIMP side is still the v0.1 skeleton. Plan:

- Pixel extraction: reuse the Lab's proven trick — `get_thumbnail_data` at
  full size + RGB→index/RGB collection per layer group (or Gegl buffers if
  full resolution matters; thumbnail is capped, check size limits).
- Scan layer tree for material groups (classifier exists: `classify_name`).
- Review step before committing: show planned slot allocations on the palette
  grid (reuse `PaletteGrid`), let the user override per material, show which
  188-235 replacement slots get consumed.
- Output: indexed image + forked palette named with materials + room-script
  materials expression for WLE (the text-field widget pattern from the editor).
- Per-material color counts: start from `examples/material_counts.json`, edit
  in-dialog.
- Later: k-means in OKLab, dithering (the sibling C plugin
  `gimp-palette-quantize` has mature dithering — maybe just defer to it for
  the remap step and only do slot planning here).

## Palette Lab

- **(requested, parked)** "Intelligent" palette harmonization — discuss on a
  concrete example first. Candidate approaches: hue-harmony presets
  (analogous/complementary/triadic targets, shift selections toward them),
  OKLab-uniform ramp regeneration, auto-detect ramps (runs of monotonic
  lightness) and re-space them.
- Make gradient in HSL or OKLab (current: linear RGB) — better for hue ramps.
- Gradient between *picked* colors (color chooser for endpoints), not just
  the selection's first/last.
- colorAnim cycle preview: animate the 4 ranges in the preview canvas
  (GLib.timeout rotating the LUT slices) to see shining colors in motion.
- Undo stack for commits (cheap: keep a list of base snapshots).
- Before/after toggle or split view on the preview canvas.
- Zoom toggle on the preview (1x/2x nearest).
- Save/load adjustment presets; "copy palette as room-script/inline hex".
- Selection: rectangular (2D) drag selection on the grid; select-by-ramp.

## Palette editor (Import)

- Undo for material/color edits.
- Remember last-used folder (lost when ProcedureDialog was dropped — stash in
  a parasite or a config file under gimp directory).
- Drag-painting materials over swatches (hold button + move = assign).
- Export the (possibly edited) palette directly to .wlsprt/.lev from the
  editor (CLI `apply` already does this).
- Import sprites from .wlsprt as GIMP layers (we already parse them for the
  Lab preview sheet) — would make GIMP a viable wlsprt editor when combined
  with export.

## CLI / core

- `materials` command: clipboard output convenience; diff two tables.
- Batch apply: palette onto many .lev/.wlsprt at once.
- Write indexed PNG without Pillow (tiny PNG encoder) so the CLI can emit
  palette.png strips for wledit interop.
- WLE: also parse/emit `colorAnim` from room scripts (pairs), not just mods.

## Windows (planned later)

Python plug-ins are portable — no compilation, unlike the sibling C plugin.
Needed: an `install-windows-user.ps1`/`.bat` copying to
`%APPDATA%\GIMP\3.2\plug-ins\<name>\` and instructions (GIMP 3 Windows builds
bundle Python by default). The CLI needs any Python 3.10+ plus optional
Pillow.

## Crash insurance

This repo is local-only. Push it to a remote (GitHub/GitLab) for real
durability — the toolkit has no secrets in it.
