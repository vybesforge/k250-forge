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
import re
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


def snapshot(root):
    """Every file in the tree, with size and mtime. Used to prove 'nothing was written to the repo'
    directly, instead of inferring it from the absence of one particular filename.

    The old check was `not os.path.exists(ROOT/session.json)` — a proxy that inverts on any machine
    that has actually driven the box: a real run whose working dir was the clone leaves a ledger
    there, so the suite failed permanently on the author's own bench while passing on a clean
    checkout. That is the v3.2.1 class backwards: green only where nobody uses the tool.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", "venv", "__pycache__"}]
        for name in filenames:
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns)
    return out


def norm_path(p):
    """Compare paths across the layer the wrapper runs under. Under Git Bash / MSYS the wrapper
    reports MSYS-style paths (/c/<user>/k250/…) while Python thinks in C:\\<user>\\k250\\…, so a raw
    string comparison fails on a difference that does not exist on disk."""
    p = (p or "").strip().strip('"').strip("'")
    m = re.match(r"^/([A-Za-z])/(.*)$", p)
    if m:
        p = f"{m.group(1)}:/{m.group(2)}"
    p = os.path.normpath(p.replace("\\", "/"))
    return p.lower() if os.name == "nt" else p


def changes(before, after):
    touched = set(after) - set(before)
    removed = set(before) - set(after)
    modified = {p for p in set(before) & set(after) if before[p] != after[p]}
    return sorted(touched | removed | modified)


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

        # 1b. the engine lists patterns itself -- this IS the Windows path (no bash, python direct),
        #     and until v3.2.10 the only way to see the pattern names was the bash wrapper.
        r1b = subprocess.run([sys.executable, os.path.join(ROOT, "k250_play.py"), "--list"],
                             cwd=elsewhere, capture_output=True, text=True, timeout=60)
        engine_listed = [ln.strip() for ln in r1b.stdout.splitlines() if ln.startswith("  ")]
        checks.append(("engine --list works from a foreign cwd (the Windows route)",
                       r1b.returncode == 0 and len(engine_listed) >= 10,
                       f"rc={r1b.returncode}, {len(engine_listed)} patterns"))
        checks.append(("wrapper and engine agree on the pattern list",
                       [ln.strip() for ln in listed] == engine_listed,
                       f"wrapper {len(listed)} vs engine {len(engine_listed)}"))

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

        # 4. --limits-show resolves to THIS clone's limits file, and touches nothing
        before_repo = snapshot(ROOT)
        before_clone = snapshot(clone)
        r4 = run(["--limits-show"], cwd=elsewhere, env_extra={"K250_DIR": clone})
        after_repo = snapshot(ROOT)
        after_clone = snapshot(clone)
        reported = ""
        for line in r4.stdout.splitlines():
            if line.startswith("limits file"):
                reported = line.split(":", 1)[1].strip()
        checks.append(("--limits-show exits 0", r4.returncode == 0, ""))
        checks.append(("--limits-show names the limits file it actually used",
                       norm_path(reported) == norm_path(os.path.join(clone, "limits.json")),
                       reported or "(no path printed)"))
        checks.append(("--limits-show reports the ceiling",
                       "POWER ceiling" in r4.stdout, ""))
        # not a proxy: compare the whole tree, so a pre-existing ledger is fine and any
        # actual write — new file, or a rewritten ledger — is caught.
        repo_touched = changes(before_repo, after_repo)
        checks.append(("--limits-show wrote nothing into the repo",
                       not repo_touched, ", ".join(repo_touched[:4])))
        clone_touched = changes(before_clone, after_clone)
        checks.append(("any state it did write landed in the working dir",
                       all(c.startswith("session.json") for c in clone_touched),
                       ", ".join(clone_touched) or "(nothing)"))

        # 5. the ENGINE's --limits-show must agree with the wrapper's: on Windows there is no
        #    wrapper, so this is the only way to inspect the contract before running anything.
        r5 = subprocess.run([sys.executable, os.path.join(clone, "k250_play.py"), "--limits-show"],
                            cwd=elsewhere, capture_output=True, text=True, timeout=60)
        def field(text, label):
            for line in text.splitlines():
                if line.startswith(label):
                    return line.split(":", 1)[1].strip()
            return ""
        checks.append(("engine --limits-show exits 0", r5.returncode == 0,
                       (r5.stderr.strip().splitlines() or [""])[-1]))
        for label in ("limits file ", "POWER ceiling ", "POWER start ", "stop word "):
            checks.append((f"engine and wrapper agree on: {label.strip()}",
                           norm_path(field(r5.stdout, label)) == norm_path(field(r4.stdout, label)),
                           f"{field(r5.stdout, label)!r} vs {field(r4.stdout, label)!r}"))
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
