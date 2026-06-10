# Project 3: Material-Aware Quantization

## Goal
Quantize RGB artwork into a forked classic Liero palette while preserving material semantics and minimizing damage to shared sprites/assets.

## Intended GIMP workflow
1. Artwork is organized in layer groups named by material:
   - `rock`
   - `rock #2`
   - `dirt`
   - `background dirt`
   - `bg dirt 2`
   - `worm`
   - `undef`
2. Plug-in scans layer/group names and infers target material.
3. User reviews and overrides assignments.
4. User enters target color count per material.
5. Plug-in loads the base/default palette.
6. For each material:
   - collect RGB pixels from matching groups
   - choose representative colors
   - reuse existing same-material palette colors if close enough
   - otherwise allocate from preferred replacement candidates 188-235
   - do not treat all UNDEF indices as disposable
   - keep worm and animated indices locked by default
7. Output an indexed image and a forked palette.

## Allocation policy
Default allocation order:

1. Reuse close existing same-material indices.
2. Recolor same-material indices if allowed.
3. Allocate from 188-235.
4. Allocate from other user-approved `UNDEF` indices only if explicitly allowed.
5. Never touch protected indices unless unlocked.

## Quantizer
V1 uses deterministic median cut for representative colors, then nearest-color remapping into the allowed material-specific index set.

Later options:
- k-means in OKLab/Lab.
- dithering.
- preserving antialiasing via alpha-aware remap.
- sprite usage analysis to increase protection penalties.

## Layer-name classifier
Hardcoded names are fine for classic mode:

- `rock`, `rocks`, `stone` -> ROCK
- `dirt`, `soil`, `ground` -> DIRT
- `dirt #2`, `dirt 2` -> DIRT_2
- `bg dirt`, `background dirt` -> BG_DIRT
- `bg dirt 2` -> BG_DIRT_2
- `background`, `bg` -> BG
- `see shadow`, `seeshadow` -> BG_SEESHADOW
- `worm` -> WORM
- `undef`, `shoot-through`, `pass through` -> UNDEF

## v1 implementation included
- `liero_core.quantizer` contains median-cut, palette-slot selection, and pixel remapping.
- `plugins/liero_material_quantize.py` is a GIMP 3 plug-in draft.
- `examples/material_counts.json` shows target color-count input.
