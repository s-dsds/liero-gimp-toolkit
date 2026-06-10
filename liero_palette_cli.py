#!/usr/bin/env python3
"""Liero palette toolkit CLI.

Palette sources: .gpl, indexed .png (needs Pillow), .lpl, .wlsprt,
POWERLEVEL .lev, decompressed LIERO.EXE.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from liero_core.palette import Palette
from liero_core.formats import load_palette, write_lpl, write_wlsprt_palette, write_lev_palette
from liero_core.material import index_info, indices_for_material, load_material_table, material_table_to_js
from liero_core.defaults import MATERIAL, DEFAULT_MATERIALS


def materials_arg(args):
    return load_material_table(args.materials) if getattr(args, 'materials', None) else None


def cmd_validate(args):
    table = materials_arg(args)
    pal = load_palette(Path(args.input)).padded256()
    dupes = pal.unique_report()
    report = []
    for i, rgb in enumerate(pal.colors[:256]):
        info = index_info(i, table)
        report.append({
            "index": i,
            "rgb": "#%02x%02x%02x" % rgb,
            "material": info.material_name,
            "animated": info.animated,
            "protected": info.protected,
            "preferredReplacementCandidate": info.preferred_replacement_candidate,
        })
    out = {"name": pal.name, "duplicateRgbCount": len(dupes), "indices": report}
    print(json.dumps(out, indent=2))


def cmd_split(args):
    table = materials_arg(args)
    pal = load_palette(Path(args.input)).padded256()
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    for mat_name, mat_value in MATERIAL.items():
        idxs = indices_for_material(mat_value, table)
        colors = [pal.colors[i] for i in idxs]
        Palette(f"{pal.name}-{mat_name}", colors).to_gpl(outdir / f"{pal.name}-{mat_name}.gpl")
    print(f"Wrote material palettes to {outdir}")


def cmd_convert(args):
    pal = load_palette(Path(args.input)).padded256()
    out = Path(args.output)
    suffix = out.suffix.lower()
    if suffix == ".gpl":
        pal.to_gpl(out)
    elif suffix == ".lpl":
        write_lpl(out, pal)
    else:
        raise SystemExit(f"Unsupported convert target (use .gpl or .lpl): {out}")
    print(f"Wrote {out}")


def cmd_apply(args):
    pal = load_palette(Path(args.palette)).padded256()
    target = Path(args.target)
    dest = Path(args.output) if args.output else target
    suffix = target.suffix.lower()
    if suffix == ".wlsprt":
        write_wlsprt_palette(target, pal, dest)
    elif suffix == ".lev":
        write_lev_palette(target, pal, dest)
    else:
        raise SystemExit(f"Apply target must be a .wlsprt or .lev file: {target}")
    print(f"Applied palette {pal.name!r} to {dest}")


def cmd_materials(args):
    table = load_material_table(args.input) if args.input else list(DEFAULT_MATERIALS)
    if args.js:
        print(material_table_to_js(table))
    else:
        print(json.dumps(table))


def main():
    p = argparse.ArgumentParser(description="Liero palette toolkit CLI")
    sub = p.add_subparsers(required=True)

    v = sub.add_parser("validate", help="JSON report of a palette (any supported source)")
    v.add_argument("input")
    v.add_argument("--materials", help="custom WLE material table (JSON or JS array file)")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("split", help="export one .gpl per material")
    s.add_argument("input")
    s.add_argument("output")
    s.add_argument("--materials", help="custom WLE material table (JSON or JS array file)")
    s.set_defaults(func=cmd_split)

    m = sub.add_parser("materials", help="print a material table (default if no input) as JSON or a WLE room-script expression")
    m.add_argument("input", nargs="?", help="material table file (JSON or JS array); omit for the classic default")
    m.add_argument("--js", action="store_true", help="emit a paste-ready defaultMaterials.map(...) expression")
    m.set_defaults(func=cmd_materials)

    c = sub.add_parser("convert", help="convert any palette source to .gpl or .lpl")
    c.add_argument("input")
    c.add_argument("output")
    c.set_defaults(func=cmd_convert)

    a = sub.add_parser("apply", help="write a palette into a .wlsprt or .lev file")
    a.add_argument("palette", help="palette source (any supported format)")
    a.add_argument("target", help=".wlsprt or .lev file to receive the palette")
    a.add_argument("-o", "--output", help="write to this file instead of modifying target in place")
    a.set_defaults(func=cmd_apply)

    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
