#!/usr/bin/env bash
#
# Installs the Liero GIMP plug-ins for the current user (GIMP 3.x, macOS).
#
# GIMP 3 on macOS looks for plug-ins in
#   ~/Library/Application Support/GIMP/<MAJOR.MINOR>/plug-ins
# one folder per plug-in. The plug-ins are pure Python and run on GIMP's
# bundled Python - nothing to compile.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$HOME/Library/Application Support/GIMP"

# pick the newest GIMP 3.x config dir; default to 3.2 if GIMP never ran yet
VER=""
if [ -d "$BASE" ]; then
  VER="$(ls "$BASE" 2>/dev/null | grep -E '^3\.[0-9]+$' | sort -V | tail -1 || true)"
fi
[ -n "$VER" ] || VER="3.2"

PLUGIN_BASE="$BASE/$VER/plug-ins"
mkdir -p "$PLUGIN_BASE"

for plugin in "$PROJECT_DIR"/plugins/*.py; do
  name="$(basename "$plugin" .py)"
  dest="$PLUGIN_BASE/$name"
  mkdir -p "$dest"
  cp "$plugin" "$dest/$name.py"
  chmod +x "$dest/$name.py"
  rm -rf "$dest/liero_core"
  cp -R "$PROJECT_DIR/liero_core" "$dest/liero_core"
  rm -rf "$dest/liero_core/__pycache__"
done

printf 'Installed Liero plug-ins for GIMP %s to %s\n' "$VER" "$PLUGIN_BASE"
printf 'Restart GIMP and check the Liero menu.\n'
