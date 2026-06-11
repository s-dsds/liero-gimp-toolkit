# Ideas & iteration backlog

Parked ideas, rough priorities. Things the user explicitly asked for are
marked **(requested)**.

## Material quantizer (v1 done) — next ideas

- Nested groups: only top-level layers/groups are scanned; recurse with
  per-subtree overrides if artwork gets deeper.
- Dithering on the remap step (or defer to the sibling C plugin
  `gimp-palette-quantize`, which has mature dithering — quantize there, then
  bring the result in for slot planning only).
- k-means in OKLab for representative colors (median-cut is fine but flat).
- Per-material "allow recoloring existing same-material slots" option
  (allocation policy step 2 from the spec — currently reuse-or-pool only).
- Option to allocate from user-approved UNDEF indices beyond 188-235.
- Show per-material quantization error (avg distance) in the stats.
- Load a .lev/.wlsprt into the preview to compare the quantized output with
  existing assets (the Studio's preview sources — share the pane).

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
