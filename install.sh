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

# 0. preflight — these are the two failures that otherwise look like mysteries
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found."
  echo "  macOS : xcode-select --install     (or: brew install python)"
  echo "  Linux : sudo apt install python3 python3-venv   [Debian/Ubuntu]"
  echo "  Windows: use the PowerShell steps in the README instead of this script."
  exit 1
fi
echo "python3    : $(python3 --version 2>&1)"
if ! python3 -c "import venv" >/dev/null 2>&1; then
  echo "ERROR: this python3 has no venv module."
  echo "  Debian/Ubuntu: sudo apt install python3-venv"
  exit 1
fi

# 1. venv + bleak
if [ ! -x "$HERE/venv/bin/python" ]; then
  echo "[1/3] creating venv"
  python3 -m venv "$HERE/venv"
else
  echo "[1/3] venv already present"
fi
"$HERE/venv/bin/pip" install --quiet --upgrade pip
"$HERE/venv/bin/pip" install --quiet bleak
if ! "$HERE/venv/bin/python" -c "import bleak" 2>/dev/null; then
  echo "ERROR: bleak installed but will not import — the venv is not usable."
  exit 1
fi
echo "      bleak installed ($("$HERE/venv/bin/python" -c 'import bleak; print("bleak", bleak.__version__)'))"
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
  *":$BIN:"*) echo "      already on your PATH" ;;
  *) _rc="$HOME/.bashrc"
     case "${SHELL:-}" in *zsh*) _rc="$HOME/.zshrc" ;; esac
     echo "      NOTE: $BIN is not on your PATH. Add it with:"
     echo "            echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> $_rc"
     echo "            then open a new shell."
     if [ "$_rc" = "$HOME/.zshrc" ]; then
       echo "            (zsh detected — macOS defaults to zsh, so ~/.zshrc is the right file there)"
     fi
     echo "      Or skip PATH entirely and call the tools directly:"
     echo "            $HERE/venv/bin/python $HERE/k250_play.py --help" ;;
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
echo
echo "── giving this to an AI agent ────────────────────────────────────────────"
echo "Point it at this folder (or the repo) and say:"
echo
echo "  Read README.md. Follow the Install section for this OS, then the First"
echo "  steps section. Read limits.json before driving anything. Use k250-status"
echo "  to check the box, k250-scene to run a pattern, k250-stop to stop. The stop"
echo "  word is red. Never exceed the ceiling in limits.json — it is enforced in"
echo "  the code, but do not try to work around it. If I say I feel nothing, stop"
echo "  and check the pads — do not add power."