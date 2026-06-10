#!/usr/bin/env bash
#
# Install the Liero GIMP plug-ins for the current user.
#
# Works with both native and Flatpak GIMP 3.x. The Flatpak GIMP bind-mounts
# its config dir to ~/.config/GIMP/<MAJOR.MINOR> (xdg-config/GIMP permission),
# so plug-ins land in the same place either way; only the version detection
# differs.
#
# GIMP 3 requires each plug-in in its own directory:
#   plug-ins/<name>/<name>.py   (executable)
# liero_core is copied next to each plug-in file so it can be imported.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

gimp_version() {
  if command -v gimp >/dev/null 2>&1; then
    gimp --version 2>/dev/null | sed -n 's/.*version \([0-9]\+\.[0-9]\+\).*/\1/p' | head -1
  elif flatpak info org.gimp.GIMP >/dev/null 2>&1; then
    flatpak info org.gimp.GIMP | sed -n 's/.*Version:[[:space:]]*\([0-9]\+\.[0-9]\+\).*/\1/p' | head -1
  fi
}

VER="$(gimp_version)"
[ -n "$VER" ] || { echo "error: could not detect an installed GIMP (native or Flatpak)" >&2; exit 1; }

PLUGIN_BASE="${XDG_CONFIG_HOME:-$HOME/.config}/GIMP/$VER/plug-ins"
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
