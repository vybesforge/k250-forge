"""macOS ships bash 3.2 — and bash before 4.4 kills the primary tool.

Regression guard for a real one. Under `set -u`, bash < 4.4 treats an expansion of an EMPTY
array as an unbound variable:

    $ bash3.2 -c 'set -euo pipefail; A=(); f(){ :; }; f "${A[@]}"'
    bash: A[@]: unbound variable          (exit 1)

`k250-scene` ended with `... "${FWD[@]}" ... "${EXTRA[@]}"`. EXTRA is empty whenever per-channel
caps are unset — the shipped default — so on macOS's stock /bin/bash (3.2.57) the primary tool
could not run at all: exit 1 at that line, the box never touched. `--list` and `--limits-show` exit
earlier, which is why every "does the clone work" check passed while driving the box did not.

The fix is the standard guard: `${A[@]+"${A[@]}"}`. Under bash 5 the two forms are equivalent; under
bash 3.2 only the guarded one survives.

Static checks — all run everywhere:

  * no shipped shell file may expand an array unguarded while it has `set -u`
    (`"${A[@]}"` / `"${A[*]}"`, as opposed to `${A[@]+"${A[@]}"}`, which is fine)
  * no bash-4+ only syntax, since /bin/bash on macOS is 3.2
  * every shipped shell file passes `bash -n`

Behavioural check — only when you point it at an older bash:

    K250_TEST_OLD_BASH=/path/to/bash tests/...        # or run this file directly

Grab a pre-4.4 bash without touching your system one, e.g. Debian/Ubuntu archive packages:

    curl -O http://old-releases.ubuntu.com/ubuntu/pool/main/b/bash/bash_3.2-4ubuntu1_amd64.deb
    curl -O http://old-releases.ubuntu.com/ubuntu/pool/main/n/ncurses/\\
libncurses5_5.6+20071124-1ubuntu2_amd64.deb
    dpkg-deb -x bash_3.2-4ubuntu1_amd64.deb /tmp/b32 && dpkg-deb -x libncurses5_*.deb /tmp/b32
    export LD_LIBRARY_PATH=/tmp/b32/lib
    K250_TEST_OLD_BASH=/tmp/b32/bin/bash tests/...

Run: venv/bin/python tests/test_shell_compat.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_FILES = ["bin/k250-scene", "bin/k250-stop", "bin/k250-status", "install.sh"]

# "…"${A[@]}"…" with no `+` before it. The guarded form ${A[@]+"${A[@]}"} contains this text too,
# which is why the lookbehind matters.
UNGUARDED_ARRAY = re.compile(r'(?<!\+)"\$\{[A-Za-z_][A-Za-z0-9_]*\[[*@]\]\}"')

BASH4_ONLY = [
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?[,^]{1,2}\}"), "${var,,} / ${var^^} (4.0)"),
    (re.compile(r"\b(declare|typeset|local)\s+-A\b"), "associative arrays (4.0)"),
    (re.compile(r"\b(mapfile|readarray)\b"), "mapfile / readarray (4.0)"),
    (re.compile(r"&>>"), "&>> (4.0)"),
    (re.compile(r"\|\&\s*$", re.M), "|& (4.0)"),
    (re.compile(r";;&"), ";;& case fallthrough (4.0)"),
    (re.compile(r"\[\[\s+-v\s"), "[[ -v ]] (4.2)"),
    (re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*@[A-Za-z]\}"), "${var@Q} transforms (4.4)"),
]


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


STUB_PY = """#!/bin/bash
# stands in for the venv python: answers the limits read, reports its own argv
if [ "$1" = "-" ]; then echo "%s"; exit 0; fi
echo "STUB-PY: $*"
exit 0
"""


def old_bash():
    return os.environ.get("K250_TEST_OLD_BASH") or ""


def run_old_bash_repo(bash, checks):
    """End to end: drive the wrapper with an old bash and a stub python, assert the
    pattern actually launches. This is the check that would have caught the bug."""
    tmp = tempfile.mkdtemp(prefix="k250-bash32-")
    try:
        clone = os.path.join(tmp, "K250-forge")
        shutil.copytree(ROOT, clone, ignore=shutil.ignore_patterns(
            ".git", "venv", "__pycache__", "session.json"))
        elsewhere = os.path.join(tmp, "elsewhere")
        os.makedirs(elsewhere)

        for label, pcaps in (("no per-channel caps", "{}"),
                             ("per-channel caps set", '{"1": {"power": 10}}')):
            for variant in ("normal", "empty-fwd"):
                stub = os.path.join(tmp, f"stub-{variant}-{pcaps.count('1')}.py")
                with open(stub, "w", encoding="utf-8") as fh:
                    fh.write(STUB_PY % f"50|5|red|25|2500|{pcaps}|1800")
                os.chmod(stub, 0o755)

                args = [os.path.join(clone, "bin", "k250-scene")]
                args += (["tide", "--base", "5", "--secs", "5"] if variant == "normal"
                         else ["--limits", os.path.join(clone, "limits.json")])

                env = dict(os.environ)
                env.update({"K250_DIR": clone, "K250_PY": stub})
                r = subprocess.run([bash] + args, cwd=elsewhere, env=env,
                                   capture_output=True, text=True, timeout=120)
                launched = "k250_play.py" in r.stdout
                checks.append((f"old bash, {label}, {variant}: exits 0",
                               r.returncode == 0, f"rc={r.returncode}"))
                checks.append((f"old bash, {label}, {variant}: no unbound variable",
                               "unbound variable" not in r.stderr,
                               r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""))
                checks.append((f"old bash, {label}, {variant}: engine launched",
                               launched, "engine was never reached" if not launched else ""))
                if pcaps != "{}" and variant == "normal":
                    checks.append((f"old bash, {label}: --channel-caps reached the engine",
                                   "--channel-caps" in r.stdout, ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    checks = []
    fails = []

    bash = shutil.which("bash")
    checks.append(("a bash to check with", bool(bash), bash or "none"))

    for rel in SHELL_FILES:
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            checks.append((f"{rel} present", False, "missing"))
            continue
        src = read(path)
        strict = re.search(r"set\s+-[a-z]*u", src) is not None

        offenders = [f"{rel}:{i}" for i, line in enumerate(src.splitlines(), 1)
                     if not line.lstrip().startswith("#") and UNGUARDED_ARRAY.search(line)] \
            if strict else []
        checks.append((f"{rel}: no unguarded array expansion under set -u",
                       not offenders, "; ".join(offenders) or "clean"))

        bad = []
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for pattern, what in BASH4_ONLY:
                if pattern.search(line):
                    bad.append(f"{rel}:{i} {what}")
        checks.append((f"{rel}: no bash-4+ only syntax", not bad, "; ".join(bad) or "clean"))

        if bash:
            r = subprocess.run([bash, "-n", path], capture_output=True, text=True)
            checks.append((f"{rel}: parses", r.returncode == 0, r.stderr.strip()))

    ob = old_bash()
    if ob:
        checks.append((f"old bash present ({ob})", os.access(ob, os.X_OK), ""))
        if os.access(ob, os.X_OK):
            ver = subprocess.run([ob, "--version"], capture_output=True, text=True).stdout.split()[3]
            print(f"  (behavioural run under bash {ver})")
            run_old_bash_repo(ob, checks)
    else:
        print("  (K250_TEST_OLD_BASH not set — static checks only; see this file's docstring)")

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
        if not ok:
            fails.append(label)

    print()
    if fails:
        print(f"SHELL COMPAT: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"SHELL COMPAT: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
