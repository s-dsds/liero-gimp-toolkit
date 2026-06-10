# Project 2: Palette Manipulation with Level Preview

## Goal
A floating GIMP plug-in window, "Liero Palette Lab", for adjusting palette ranges while previewing the impact on an indexed level or map image.

## Important constraint
This should not initially be a dockable GIMP panel. Normal external GIMP plug-ins can open custom GTK windows, but first-class dockables are a GIMP core UI feature.

## Intended workflow
1. Open an indexed level image in GIMP.
2. Launch `Liero > Palette Lab...`.
3. The plug-in reads the image palette/colormap.
4. User selects material, indices, or color ranges.
5. User changes hue/saturation/lightness/contrast/gamma.
6. Preview updates on a duplicate preview image or temporary layer.
7. Apply writes back a forked image palette.

## Selection modes
- Whole material, e.g. ROCK.
- Index range, e.g. 188-235.
- Explicit selected indices.
- Animated indices.
- All except protected.

## Default safety rules
- `UNDEF` is editable only when explicitly selected.
- Worm indices are locked by default.
- Animated indices are locked by default.
- 188-235 are marked as preferred replacement candidates, not automatically unused.

## Preview strategy
V1 should use a duplicate image/layer rather than live editing the user's only copy.

Possible modes:
- Apply palette update to a duplicate indexed image.
- Render RGB preview layer from indexed image and temporary palette.
- Later: custom GTK preview widget.

## Adjustment operations
V1:
- brightness
- contrast
- hue shift
- saturation multiplier

Later:
- ramp smoothing
- gradient remapping
- material curve editing
- color animation preview

## v1 implementation included
- Core color transform helpers in the preview plug-in draft.
- GTK UI skeleton in `plugins/liero_palette_lab.py`.
- Command-line pieces are intentionally light; this is primarily UI-driven.
