#!/usr/bin/env bash
# k250-forge installer — sets up the venv, links the tools onto your PATH, and
# gives you a limits file to edit.
#
#   ./install.sh
#
# Nothing here needs sudo. Everything lives inside this folder; the only thing
# written outside it is three symlinks in ~/.local/bin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "k250-forge → $HERE"
echo

# 1. venv + bleak
if [ ! -x "$HERE/venv/bin/python" ]; then
  echo "[1/3] creating venv"
  python3 -m venv "$HERE/venv"
else
  echo "[1/3] venv already present"
fi
"$HERE/venv/bin/pip" install --quiet --upgrade pip
"$HERE/venv/bin/pip" install --quiet bleak
echo "      bleak installed"
echo

# 2. tools onto PATH
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"
echo "[2/3] linking tools into $BIN"
for t in k250-scene k250-stop k250-status; do
  ln -sf "$HERE/bin/$t" "$BIN/$t"
  echo "      $BIN/$t"
done
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "      NOTE: $BIN is not on your PATH — add it, e.g."
     echo "            echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac
echo

# 3. your own limits file
echo "[3/3] limits"
if [ ! -f "$HERE/limits.local.json" ]; then
  cp "$HERE/limits.json" "$HERE/limits.local.json"
  echo "      wrote limits.local.json (a copy of the conservative default)"
else
  echo "      limits.local.json already exists — left alone"
fi
echo
echo "Done."
echo
echo "  k250-status                 see the box: battery, live channels, speed"
echo "  k250-scene --list           all patterns"
echo "  k250-scene --limits-show    your active ceilings"
echo "  k250-stop                   STOP NOW (kills the pattern, zeroes every channel)"
echo
echo "Edit limits.local.json — or regenerate it at limits-form.html — before your first run."
echo "Never run two k250-scene at once: the box accepts one BLE connection at a time."