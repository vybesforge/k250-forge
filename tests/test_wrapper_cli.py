"""The wrappers must work from ANY directory, with any python.

Regression guard for a real one: `k250-scene --list` imported `k250_play` from a
`python -` heredoc, and `python -` puts the CALLER's cwd on sys.path[0] — not the
wrapper's directory. So `--list` worked from inside the clone and died with
`ModuleNotFoundError: No module named 'k250_play'` everywhere else. `--limits-show`
was fine, which is what made it look like a python problem rather than a wrapper bug.

Two more things on the same path, both fixed here:
  * the interpreter check ran AFTER argument handling, so a missing venv surfaced as
    bash's `line 45: .../venv/bin/python: No such file or directory` instead of the
    "run ./install.sh first" message;
  * `$HERE` silently fell back to `$HOME/K250-forge` when it could not resolve, so a
    wrapper could report a directory that is not the clone as if it were the truth.

Read-only: this test only ever runs `--list` and `--limits-show`, never the engine.

Run: venv/bin/python tests/test_wrapper_cli.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASH = shutil.which("bash")
WRAPPER = "bin/k250-scene"


def skeleton(dest):
    """A copy of the repo a wrapper can run from: no .git, no venv, no caches."""
    shutil.copytree(
        ROOT, dest,
        ignore=shutil.ignore_patterns(".git", "venv", "__pycache__", "session.json"),
    )
    return dest


def run(args, cwd, env_extra=None):
    env = dict(os.environ)
    env["K250_PY"] = sys.executable          # a python that can import bleak? see note below
    env.pop("K250_DIR", None)
    env.pop("K250_LIMITS", None)
    env.update(env_extra or {})
    return subprocess.run(
        [BASH, os.path.join(ROOT, WRAPPER)] + args,
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )


def main():
    if not BASH:
        print("WRAPPER CLI: SKIP — no bash on this machine (the wrappers are bash; "
              "on Windows call the engine directly)")
        return 0

    checks = []
    fails = []
    tmp = tempfile.mkdtemp(prefix="k250-cli-")
    try:
        clone = skeleton(os.path.join(tmp, "K250-forge"))     # note the case
        elsewhere = os.path.join(tmp, "elsewhere")
        os.makedirs(elsewhere)

        # 1. --list from a directory that is not the clone  <- the reported bug
        r = run(["--list"], cwd=elsewhere, env_extra={"K250_DIR": clone})
        listed = [ln.strip() for ln in r.stdout.splitlines() if ln.startswith("  ")]
        checks.append(("--list from a foreign cwd exits 0", r.returncode == 0,
                       (r.stderr.strip().splitlines() or [""])[-1]))
        checks.append(("--list prints patterns", len(listed) >= 10, f"{len(listed)} patterns"))
        checks.append(("--list names no missing module", "ModuleNotFoundError" not in r.stderr, ""))

        # 2. --list still works from inside the clone (the old happy path)
        r2 = run(["--list"], cwd=clone, env_extra={"K250_DIR": clone})
        checks.append(("--list from inside the clone still works", r2.returncode == 0, ""))

        # 3. --list against a directory that has no modules: loud, and exit 1
        bad = os.path.join(tmp, "not-a-clone")
        os.makedirs(bad)
        r3 = run(["--list"], cwd=elsewhere, env_extra={"K250_DIR": bad})
        checks.append(("missing modules: exit 1", r3.returncode == 1, f"rc={r3.returncode}"))
        checks.append(("missing modules: says which path it looked in",
                       "k250_play.py" in r3.stderr and bad in r3.stderr, r3.stderr.strip()))

        # 4. --limits-show resolves to THIS clone's limits file, read-only
        r4 = run(["--limits-show"], cwd=elsewhere, env_extra={"K250_DIR": clone})
        reported = ""
        for line in r4.stdout.splitlines():
            if line.startswith("limits file"):
                reported = line.split(":", 1)[1].strip()
        checks.append(("--limits-show exits 0", r4.returncode == 0, ""))
        checks.append(("--limits-show names the limits file it actually used",
                       reported == os.path.join(clone, "limits.json"),
                       reported or "(no path printed)"))
        checks.append(("--limits-show reports the ceiling",
                       "POWER ceiling" in r4.stdout, ""))
        checks.append(("--limits-show did not write into the repo",
                       not os.path.exists(os.path.join(ROOT, "session.json")), ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
        if not ok:
            fails.append(label)

    print()
    if fails:
        print(f"WRAPPER CLI: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"WRAPPER CLI: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
