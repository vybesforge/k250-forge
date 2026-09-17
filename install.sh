#!/usr/bin/env bash
# k250-forge installer — sets up the venv, links the tools onto your PATH, and
# gives you a limits file to edit.
#
#   ./install.sh
#
# Nothing here needs sudo. Everything lives inside this folder; the only things
# written outside it are three symlinks in ~/.local/bin and, only if you say yes,
# one line of your shell rc file that puts ~/.local/bin on your PATH.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "k250-forge → $HERE"
echo

# 0. preflight — these are the failures that otherwise look like mysteries.
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found."
  echo "  macOS : xcode-select --install     (or: brew install python)"
  echo "  Linux : sudo apt install python3 python3-venv   [Debian/Ubuntu]"
  echo "  Windows: use the PowerShell steps in the README instead of this script."
  exit 1
fi
echo "python3    : $(python3 --version 2>&1)"
# No static Python floor here, on purpose. pip resolves the NEWEST bleak that runs on THIS
# interpreter: a Python 3.9 box gets bleak 1.1.1 (which installs and runs fine), a 3.10+ box
# gets the current release. A fixed "needs 3.10+" check would therefore block a Python that
# installs frankly fine on the false premise that the newer bleak is the only bleak there is.
# The real gates are below: `pip install` failing to resolve (it names the true reason, so a
# genuinely too-old Python still gets an honest error) and the `import bleak` check that follows.
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
echo "      bleak installed ($("$HERE/venv/bin/python" -c 'import importlib.metadata as m; print("bleak", m.version("bleak"))' 2>/dev/null || echo "bleak"))"
echo

# 2. tools onto PATH
BIN="${HOME}/.local/bin"
mkdir -p "$BIN"
echo "[2/3] linking tools into $BIN"
for t in k250-scene k250-stop k250-status; do
  ln -sf "$HERE/bin/$t" "$BIN/$t"
  echo "      $BIN/$t"
done
# Is ~/.local/bin actually PERSISTED, not just in this session's PATH? The old check
# (`case ":$PATH:"`) only tested the live shell, so if the current session happened to have the
# dir (a parent shell exported it, or you added it manually) it printed "already on your PATH" —
# but a fresh terminal reads the rc file and won't find the tools. The real question is whether
# the rc file carries the export line.
_rc="$HOME/.bashrc"
case "${SHELL:-}" in
  *zsh*) _rc="$HOME/.zshrc" ;;
esac
# macOS login shells read a different file than interactive ones: bash reads .bash_profile,
# zsh reads .zprofile. Prefer those when present so a login shell (the default on macOS) sees it.
if [ "$(uname -s)" = "Darwin" ]; then
  case "${SHELL:-}" in
    *zsh*) [ -f "$HOME/.zprofile" ] && _rc="$HOME/.zprofile" ;;
    *)     [ -f "$HOME/.bash_profile" ] && _rc="$HOME/.bash_profile" ;;
  esac
fi
if grep -q '\.local/bin' "$_rc" 2>/dev/null; then
  echo "      $BIN is on your PATH (persisted in $_rc)"
else
  # Add it? Two ways in, never silently: K250_ADD_PATH=1 for scripts/CI (no TTY, no prompting),
  # or an interactive prompt whose default is yes. With stdin not a terminal and no override, it
  # fails safe: nothing is written, the user just gets the reminder below.
  _write_path=0
  case "${K250_ADD_PATH:-}" in
    1|y|Y|yes|YES) _write_path=1 ;;
  esac
  if [ "$_write_path" -eq 0 ] && [ -t 0 ]; then
    printf "      %s is not on your PATH. Add it to %s? [Y/n] " "$BIN" "${_rc##*/}"
    read -r _ans || true
    case "${_ans:-y}" in
      y|Y|yes) _write_path=1 ;;
    esac
  fi
  if [ "$_write_path" -eq 1 ]; then
    if printf '\n# ~/.local/bin on PATH (added by k250-forge install.sh)\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$_rc" 2>/dev/null; then
      echo "      added to ${_rc##*/}:  export PATH=\"\$HOME/.local/bin:\$PATH\""
      echo "      open a new terminal and the tools will be on your PATH."
      echo "      (to undo, remove the line ending in '# k250-forge install.sh' from ${_rc##*/})"
    else
      echo "      could not write ${_rc##*/} — add it yourself:"
      echo "            echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ${_rc##*/}"
    fi
  else
    echo "      $BIN is not on your PATH. Open a new shell, or call the tools directly:"
    echo "            $HERE/venv/bin/python $HERE/k250_play.py --help"
    if [ "$_rc" = "$HOME/.zshrc" ] || [ "$_rc" = "$HOME/.zprofile" ]; then
      echo "            (zsh detected — macOS defaults to zsh, so ${_rc##*/} is the file to edit)"
    fi
  fi
fi
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