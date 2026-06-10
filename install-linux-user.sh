#!/usr/bin/env bash
set -euo pipefail
PLUGIN_BASE="${XDG_CONFIG_HOME:-$HOME/.config}/GIMP/3.0/plug-ins"
mkdir -p "$PLUGIN_BASE"
for plugin in plugins/*.py; do
  name="$(basename "$plugin" .py)"
  dest="$PLUGIN_BASE/$name"
  mkdir -p "$dest"
  cp "$plugin" "$dest/$name.py"
  chmod +x "$dest/$name.py"
done
# Copy core package next to each plugin so GIMP can import it.
for dest in "$PLUGIN_BASE"/liero_*; do
  [ -d "$dest" ] || continue
  rm -rf "$dest/liero_core"
  cp -R liero_core "$dest/liero_core"
done
printf 'Installed draft Liero plug-ins to %s\n' "$PLUGIN_BASE"
printf 'Restart GIMP and check the Liero menu.\n'
