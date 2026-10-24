#!/usr/bin/env bash
# Run every suite the way they are written to be run: as scripts, from the repo root.
# (`python -m unittest discover` does NOT work here - the suites are self-contained
# scripts that print a verdict and exit non-zero on failure.)
#   tests/run_all.sh            # uses venv/bin/python if present, else python3
#   PY=/path/to/python tests/run_all.sh
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"
PY="${PY:-}"
if [ -z "$PY" ]; then
  if [ -x venv/bin/python ]; then PY=venv/bin/python
  elif [ -x venv/Scripts/python.exe ]; then PY=venv/Scripts/python.exe
  else PY=python3; fi
fi
pass=0; fail=0; failed=()
for t in tests/test_*.py; do
  if out="$("$PY" "$t" 2>&1)"; then
    pass=$((pass+1)); printf 'PASS  %s\n' "$t"
  else
    fail=$((fail+1)); failed+=("$t"); printf 'FAIL  %s\n%s\n' "$t" "$out"
  fi
done
echo
echo "suites: $pass passed, $fail failed"
if [ "$fail" -gt 0 ]; then printf '  %s\n' ${failed[@]+"${failed[@]}"}; exit 1; fi
