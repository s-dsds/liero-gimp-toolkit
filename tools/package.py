#!/usr/bin/env python3
"""Build per-platform distribution zips into dist/.

The plug-ins are pure Python (no compilation); each zip carries the same code
plus that platform's installer. Used by CI and runnable locally:

    python3 tools/package.py [version-tag]
"""
from __future__ import annotations
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

COMMON = [
    'LICENSE',
    'README.md',
    'DEVELOPMENT.md',
    'IDEAS.md',
    'liero_palette_cli.py',
    'examples/material_counts.json',
]

PLATFORM_INSTALLERS = {
    'linux': ['install-linux-user.sh'],
    'macos': ['install-macos-user.sh'],
    'windows': ['install-windows-user.ps1'],
}


def gather_tree(rel_dir: str):
    for path in sorted((ROOT / rel_dir).rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts:
            yield path


def build(version: str) -> list:
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    built = []
    for platform, installers in PLATFORM_INSTALLERS.items():
        out = dist / f'liero-gimp-toolkit-{version}-{platform}.zip'
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
            prefix = 'liero-gimp-toolkit/'
            for rel_dir in ('liero_core', 'plugins', 'specs'):
                for path in gather_tree(rel_dir):
                    zf.write(path, prefix + str(path.relative_to(ROOT)))
            for rel in COMMON + installers:
                path = ROOT / rel
                if path.exists():
                    zf.write(path, prefix + rel)
        built.append(out)
        print(f'built {out.name} ({out.stat().st_size // 1024} KiB)')
    return built


if __name__ == '__main__':
    build(sys.argv[1] if len(sys.argv) > 1 else 'dev')
