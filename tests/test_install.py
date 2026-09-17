"""The installer's PATH behaviour, without building a real venv.

install.sh is deliberate about not touching the machine unseen: when ~/.local/bin is missing from
the shell rc it does not edit silently, it asks. That decision has to survive, so this suite pins
the three ways it can go wrong:

  * K250_ADD_PATH=1  -- scripts/CI want the write without a prompt; it must happen, once.
  * no TTY, no flag  -- nothing is written; the user just gets a reminder (fails safe, no hang).
  * a second run      -- must not write the line twice.

The venv would normally be built by install.sh, but that needs network (pip + bleak). So the test
clone ships a fake `venv/bin/python` and `venv/bin/pip` that exit 0, which sends install.sh past
step 1 to the PATH logic -- the only part under test. `limits.json` is copied so step 3 succeeds.

Run: venv/bin/python tests/test_install.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IGNORED = {"venv", "__pycache__", ".git", "tests", "bin", "agent-skill"}


def sandbox():
    """A throwaway repo copy with fake venv tools so install.sh reaches its PATH step fast."""
    d = tempfile.mkdtemp(prefix="k250-install-test-")
    for name in os.listdir(ROOT):
        if name in IGNORED:
            continue
        src = os.path.join(ROOT, name)
        dst = os.path.join(d, name)
        if os.path.isdir(src):
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)
    # fake venv so install.sh skips the (network) build and comes straight to PATH handling
    vbin = os.path.join(d, "venv", "bin")
    os.makedirs(vbin)
    fake = "#!/bin/sh\nexit 0\n"
    for tool in ("python", "pip", "python3"):
        p = os.path.join(vbin, tool)
        with open(p, "w") as fh:
            fh.write(fake)
        os.chmod(p, 0o700)
    return d


def run_install(root, home, add_path):
    env = dict(os.environ)
    env["HOME"] = home
    env.pop("SHELL", None)          # deterministic: default to .bashrc
    if add_path is None:
        env.pop("K250_ADD_PATH", None)
    else:
        env["K250_ADD_PATH"] = add_path
    return subprocess.run(
        ["bash", os.path.join(root, "install.sh")],
        env=env, stdin=subprocess.DEVNULL,   # stdin is NOT a terminal
        capture_output=True, text=True, timeout=120)


def rc_file(home):
    return os.path.join(home, ".bashrc")


def rc_lines(home):
    p = rc_file(home)
    if not os.path.isfile(p):
        return []
    with open(p) as fh:
        return [l for l in fh if "k250-forge" in l or ".local/bin" in l]


def export_count(home):
    """The installer writes a marker comment and the export; both contain '.local/bin', so count
    only lines that actually export PATH."""
    return sum(1 for l in rc_lines(home) if l.strip().startswith("export PATH="))


def main():
    checks = []
    fails = []

    # 1. K250_ADD_PATH=1 writes the line, exactly once, into the right rc file. The write is two
    #    lines -- a marker comment and the export -- and BOTH contain ".local/bin", so count the
    #    export line specifically, not every matching line.
    d = sandbox()
    home = tempfile.mkdtemp(prefix="k250-home-")
    try:
        r = run_install(d, home, add_path="1")
        exports = export_count(home)
        checks.append(("K250_ADD_PATH=1 writes the PATH export to .bashrc",
                       r.returncode == 0 and exports == 1,
                       f"exit {r.returncode}; export line count={exports}"))

        # 2. idempotent: a second run must not add another export.
        r2 = run_install(d, home, add_path="1")
        exports2 = export_count(home)
        checks.append(("a second run does not write the line again",
                       r2.returncode == 0 and exports2 == 1,
                       f"exit {r2.returncode}; export line count={exports2}"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)

    # 3. no TTY and no flag: fails safe -- nothing written, no hang.
    d = sandbox()
    home = tempfile.mkdtemp(prefix="k250-home-")
    try:
        r = run_install(d, home, add_path=None)
        written = os.path.isfile(rc_file(home)) and bool(rc_lines(home))
        checks.append(("non-interactive with no flag writes nothing (fails safe)",
                       r.returncode == 0 and not written,
                       f"exit {r.returncode}; wrote={written}"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)

    # 4. K250_ADD_PATH=/no also declines.
    d = sandbox()
    home = tempfile.mkdtemp(prefix="k250-home-")
    try:
        r = run_install(d, home, add_path="no")
        written = os.path.isfile(rc_file(home)) and bool(rc_lines(home))
        checks.append(("K250_ADD_PATH=no writes nothing",
                       r.returncode == 0 and not written,
                       f"exit {r.returncode}; wrote={written}"))
    finally:
        shutil.rmtree(d, ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
        if not ok:
            fails.append(label)

    print()
    if fails:
        print(f"INSTALL PATH: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"INSTALL PATH: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())