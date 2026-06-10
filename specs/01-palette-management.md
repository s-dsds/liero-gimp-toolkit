# Project 1: Liero Palette Management

## Goal
Create a GIMP-centered palette manager for classic Liero/WebLiero assets.

The tool imports palettes from GIMP `.gpl` files and indexed PNG sprites/maps, validates them against the classic 256-entry material table, and exports full or material-split GIMP palettes.

## Non-goals for v1
- Editing native `.lev` files directly.
- WebLiero Extended arbitrary material tables, except as a later import/export option.
- Treating `UNDEF` as unused. `UNDEF` is a valid shoot-through material.

## Source of truth
Classic material behavior is hardcoded from the provided constants:

- `UNDEF = 0`
- `DIRT = 1`
- `DIRT_2 = 2`
- `ROCK = 4`
- `BG = 8`
- `BG_DIRT = 9`
- `BG_DIRT_2 = 10`
- `BG_SEESHADOW = 24`
- `WORM = 32`

`defaultColorAnim = [129,132,133,136,152,159,168,171]` is stored as a separate property, not as a material.

## UX
Menu entries:

- `Liero > Palette > Import GPL...`
- `Liero > Palette > Import from Indexed PNG...`
- `Liero > Palette > Export by Material...`
- `Liero > Palette > Validate Current Image Palette`

## Core operations
1. Load palette.
2. Pad or reject to 256 colors depending on strict mode.
3. Report duplicate RGB colors.
4. Report protected indices: worm colors and animated indices.
5. Export:
   - full palette `.gpl`
   - one `.gpl` per material
   - optional JSON report with index, RGB, material, animation flag, protection flag.

## Data model
```python
PaletteIndexInfo(
    index: int,
    rgb: tuple[int,int,int],
    material: int,
    material_name: str,
    animated: bool,
    protected: bool,
    preferred_replacement_candidate: bool,
)
```

## v1 implementation included
- `liero_core.palette` for GPL and indexed PNG palette import/export.
- `liero_core.material` for material lookup and layer-name classification.
- `plugins/liero_palette_management.py` as a GIMP 3 plug-in draft.
- `liero_palette_cli.py` for command-line testing outside GIMP.
