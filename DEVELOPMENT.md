# Development notes

Hard-won knowledge from building this toolkit (June 2026, Flatpak GIMP 3.2.4
on Linux). Read this before touching the GIMP-side code.

## Environment & build

- GIMP is the **Flatpak** (`org.gimp.GIMP`, 3.2.4, GNOME runtime 50). Python
  plug-ins go to `~/.config/GIMP/3.2/plug-ins/<name>/<name>.py` (one dir per
  plug-in, file executable) — the Flatpak bind-mounts config there. GIMP's
  bundled Python is 3.13 and has **no Pillow** (PNG palette work inside GIMP
  must go through GIMP itself).
- `./install-linux-user.sh` detects the GIMP version (native or Flatpak) and
  copies each plug-in + a `liero_core` snapshot next to it.
- Host Python has no pip/venv; use `uv` (`~/.local/bin`): `uv venv .venv &&
  uv pip install --python .venv/bin/python pytest pillow`.

## Testing recipes (all proven to catch real bugs)

1. **Core**: `.venv/bin/python -m pytest tests` — pure Python, no GIMP.
2. **Syntax under GIMP's Python**:
   `flatpak run --command=python3 org.gimp.GIMP -m py_compile <file>`.
3. **Registration**: delete `~/.config/GIMP/3.2/pluginrc`, run
   `flatpak run org.gimp.GIMP -i -d -f --batch-interpreter=plug-in-script-fu-eval -b '(gimp-quit 0)'`,
   then grep pluginrc for `python-fu-liero-*`. Zero tracebacks expected.
4. **Procedures headlessly**: `--batch-interpreter=python-fu-eval -b "exec(open('/tmp/x.py').read())"`,
   look up procedures via `Gimp.get_pdb().lookup_procedure(...)`, build a
   config, `proc.run(config)`. `Gimp.PDBStatusType.SUCCESS == 3`.
5. **Dialogs without GIMP**: exec the plug-in source up to
   `class Liero...(Gimp.PlugIn)` (everything before it), stub
   `GimpUi.Dialog = Gtk.Dialog` and a fake `Gimp` namespace, then drive the
   widget handlers directly. Call `Gegl.init(None)` first if Gegl.Color is
   used. A display (`:0`) is available on this machine, so widgets realize.
   This caught `Image.set_name` not existing before any user did.

## GIMP 3.2 API traps (each cost a debugging round)

- A plain `Gimp.Procedure` registered under a menu **fails**; menu procedures
  need the run-mode machinery — use `Gimp.ImageProcedure`.
- `Gimp.Image.set_name()` does **not** exist. (`get_name` does.)
- `Gimp.Image.set_colormap()` does not exist either — build a `Gimp.Palette`
  and call `image.set_palette(pal)`. The colormap is **copied**: deleting the
  palette resource afterwards is safe (used for "apply uniquified copy").
- `image.get_palette()` on an indexed image returns a palette whose
  `set_entry_color()` **writes through** to the colormap — but a duplicate
  image shown in a new display does **not repaint** when you do this
  (`Gimp.displays_flush()` is not enough). Hence the in-dialog preview canvas.
- File args with `Gimp.FileChooserAction.SELECT_FOLDER` are validated at call
  time: a non-existent folder → `CALLING_ERROR` (status 1).
- `image.get_thumbnail_data(w, h)` → `(GLib.Bytes, w, h, bpp)`; it preserves
  aspect ratio itself (returned w/h differ from requested) and bpp is often 4.
- `palette.get_entry_name(i)` returns `(bool, str)`.
- Override `do_set_i18n(self, name): return False` or every plug-in logs
  missing-locale warnings.
- `GimpUi.ProcedureDialog` persists last-used arg values for free; a custom
  `Gtk.FileChooserDialog` does not (trade-off taken in Import).
- Headless `-d` (no data) hides palette resources — palette lists look empty
  in batch tests; that's not a bug.

## Format ground truth (sources verified against each other)

- **.wlsprt**: `WLSPRT` + u16le version (0) + palette flag byte + (if flag)
  768-byte 8-bit RGB palette at offset 9 + u16 sprite count + sprites
  (w,h,xoff,yoff as i16le + w*h index bytes). Source:
  `~/liero/wltools/wltools/src/wltools.cc`.
- **.lpl**: raw 768 bytes, 8-bit RGB. (6-bit dumps auto-detected: max ≤ 63.)
- **LIERO.EXE** (decompressed 1.33, 135856 bytes): palette @ 132774 (6-bit,
  scale ×4 to match community tools), colorAnim @ 0x1AF0C (4 from/to byte
  pairs), materials @ 0x1C2E0 (5 bitplanes × 32 bytes: dirt/dirt2/rock/bg/
  seeshadow) + worm bitplane @ 0x1AEA8. Offsets from OpenLiero
  `src/tc_tool/common_exereader.cpp` (github gliptic/liero).
- **.lev**: 504×350 raw index bytes; POWERLEVEL variant appends the magic
  `POWERLEVEL` + 768 bytes of 6-bit palette.
- **colorAnim is (from,to) RANGE PAIRS**, not individual indices. Classic
  value `[129,131,133,136,152,159,168,171]` (the once-circulated `132` was a
  transcription error; exe + WebLiero classic mod.json agree on 131).
- **Material values are bitmasks** (BG_DIRT=9=dirt|bg, BG_SEESHADOW=24=
  bg|shadow). The classic exe table and wgetch's WebLiero reference agree
  byte-for-byte; wgetch's page is 404 — use the web.archive.org snapshot
  (20241108111359 of liero.phazon.xyz/materials.html).
- **WebLiero Extended custom materials** live in ROOM SCRIPTS (not mods):
  per-map `materials:` arrays passed to `WLROOM.setMaterials()`, written as
  `defaultMaterials.map(noUndef).map(replaceMatIndexBy(MATERIAL.X, ..., ..._range(a,b)))`.
  `parse_material_text` / `material_table_to_js` speak exactly this idiom.
  Reference scripts: `~/liero/dock/room/` (`_material_init.js`, `mapsettings.js`).

## Design decisions (and why)

- One git repo for all three projects: they share `liero_core`.
- `liero_core` stays GIMP/GTK-free except `palette_grid.py` (explicitly not
  imported by `__init__.py`) so the test suite runs without GTK.
- Protection = **worm indices only**, derived from the material table in use.
  Animated indices are informational (colorAnim is mod-configurable).
- Color pipeline works in **floats**; quantize only at display/export.
  Rationale: committed adjustment chains were visibly flattening in 8-bit.
- Apply paths (Lab, Import) write a **uniquified** colormap (minimal channel
  nudges, first occurrence wins — same idea as gimpalettetosimplearray.html)
  because GIMP can't distinguish identical colormap entries. The named
  palette resource keeps the raw colors.
- GIMP palettes carry materials in entry names (`042 ROCK`); 
  `materials_from_entry_names` restores the table (≥50% hit rate required).
