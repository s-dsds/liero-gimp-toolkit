#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path
from liero_core.palette import read_gpl, load_indexed_png_palette, Palette
from liero_core.material import index_info, indices_for_material
from liero_core.defaults import MATERIAL, MATERIAL_NAMES


def load_palette(path: Path) -> Palette:
    if path.suffix.lower() == ".gpl":
        return read_gpl(path).padded256()
    if path.suffix.lower() == ".png":
        return load_indexed_png_palette(path).padded256()
    raise SystemExit(f"Unsupported palette source: {path}")


def cmd_validate(args):
    pal = load_palette(Path(args.input))
    dupes = pal.unique_report()
    report = []
    for i, rgb in enumerate(pal.colors[:256]):
        info = index_info(i)
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
    pal = load_palette(Path(args.input))
    outdir = Path(args.output)
    outdir.mkdir(parents=True, exist_ok=True)
    for mat_name, mat_value in MATERIAL.items():
        idxs = indices_for_material(mat_value)
        colors = [pal.colors[i] for i in idxs]
        Palette(f"{pal.name}-{mat_name}", colors).to_gpl(outdir / f"{pal.name}-{mat_name}.gpl")
    print(f"Wrote material palettes to {outdir}")


def main():
    p = argparse.ArgumentParser(description="Liero palette toolkit CLI")
    sub = p.add_subparsers(required=True)
    v = sub.add_parser("validate")
    v.add_argument("input")
    v.set_defaults(func=cmd_validate)
    s = sub.add_parser("split")
    s.add_argument("input")
    s.add_argument("output")
    s.set_defaults(func=cmd_split)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
