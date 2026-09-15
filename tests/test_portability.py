"""Portability: nothing in the shipped code may point at the author's machine.

Regression guard for a real one: `tests/test_pattern_change.py` had
`sys.path.insert(0, '/home/<user>/k250')` — the author's own working directory, hardcoded. It ran
fine on that box and raised `ModuleNotFoundError: k250_play` for everyone else, which meant the one
test guarding the pattern-change rule (the rule that used to silently zero channels) was the one
test that never ran on a fresh clone. Found by an agent doing a clean macOS checkout.

The rule this enforces: code may derive a path from `__file__`/`BASH_SOURCE`, or from an env var
(`K250_DIR`, `K250_PY`, `K250_LIMITS`), or fall back to `$HOME`-relative — never bake in a
particular user's home directory.

Run: venv/bin/python tests/test_portability.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# /home/<someone>/ or /Users/<someone>/ -- an absolute path into a personal home dir.
ABSOLUTE_HOME = re.compile(r"(?:/home/|/Users/)[A-Za-z0-9._-]+")

SKIP_DIRS = {".git", "venv", "__pycache__", "node_modules"}
CHECK_SUFFIXES = (".py", ".sh")
CHECK_NAMES = {"k250-scene", "k250-stop", "k250-status"}   # no extension, still shipped


def shipped_sources():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in sorted(filenames):
            if name.endswith(CHECK_SUFFIXES) or name in CHECK_NAMES:
                yield os.path.join(dirpath, name)


def rel(path):
    return os.path.relpath(path, ROOT)


def main():
    checks = []
    fails = []

    sources = list(shipped_sources())
    checks.append(("sources scanned", len(sources) >= 10, f"{len(sources)} files"))

    # 1. no personal absolute paths anywhere in shipped code
    offenders = []
    for path in sources:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                m = ABSOLUTE_HOME.search(line)
                if m:
                    offenders.append(f"{rel(path)}:{lineno}: {m.group(0)}")
    checks.append(("no author home paths in code", not offenders, "; ".join(offenders) or "clean"))

    # 2. every test resolves the package relative to itself, not by literal path
    tests_dir = os.path.join(ROOT, "tests")
    if os.path.isdir(tests_dir):
        test_files = sorted(f for f in os.listdir(tests_dir) if f.startswith("test_") and f.endswith(".py"))
    else:
        test_files = []
    bad_tests = []
    for name in test_files:
        with open(os.path.join(tests_dir, name), encoding="utf-8") as fh:
            src = fh.read()
        if "__file__" not in src:
            bad_tests.append(f"{name}: never uses __file__ to locate the package")
    checks.append(("tests are self-locating", not bad_tests, "; ".join(bad_tests) or f"{len(test_files)} tests"))

    # 3. the repo root actually holds the engine the tests import
    checks.append(("k250_play.py at repo root", os.path.isfile(os.path.join(ROOT, "k250_play.py")), ""))

    # 4. the wrappers can find the tools without an absolute path baked in
    wrapper = os.path.join(ROOT, "bin", "k250-scene")
    if os.path.isfile(wrapper):
        with open(wrapper, encoding="utf-8") as fh:
            wsrc = fh.read()
        checks.append(("wrapper resolves its own location",
                       "BASH_SOURCE" in wsrc and "K250_DIR" in wsrc,
                       "needs BASH_SOURCE + K250_DIR override"))
    else:
        checks.append(("wrapper present", False, "bin/k250-scene missing"))

    # 5. pulling the package in from the repo root works with cwd anywhere
    saved = list(sys.path)
    try:
        sys.path.insert(0, ROOT)
        import k250_play                                   # noqa: F401
        origin = os.path.abspath(sys.modules["k250_play"].__file__ or ROOT)
        checks.append(("imports resolve to this clone", origin.startswith(ROOT), origin))
    except Exception as exc:                                # pragma: no cover
        checks.append(("imports resolve to this clone", False, repr(exc)))
    finally:
        sys.path[:] = saved

    # 6. the shipped operator skill exists, is loadable-shaped, and still carries both hard rules.
    #    The changelog says the rules live in the skill; a claim like that has to be checkable from a
    #    clone, or it is the same defect as a hardcoded path.
    skill = os.path.join(ROOT, "agent-skill", "SKILL.md")
    if os.path.isfile(skill):
        with open(skill, encoding="utf-8") as fh:
            sk = fh.read()
        frontmatter = sk.split("---")[1] if sk.startswith("---") else ""
        keys = {line.split(":", 1)[0].strip() for line in frontmatter.splitlines() if ":" in line}
        checks.append(("skill frontmatter has name + description",
                       {"name", "description"} <= keys, ", ".join(sorted(keys)) or "no frontmatter"))
        rules = {
            "stop word ends everything": "stop word ends everything" in sk.lower(),
            "no sensation -> power down": "never more power" in sk.lower(),
            "ceiling is clamped in code": "clamped in code" in sk.lower(),
        }
        for label, ok in rules.items():
            checks.append((f"skill carries: {label}", ok, ""))
        checks.append(("skill names the stop file", "limits.json" in sk, ""))
    else:
        checks.append(("agent-skill/SKILL.md present", False, "missing"))

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
        if not ok:
            fails.append(label)

    print()
    if fails:
        print(f"PORTABILITY: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"PORTABILITY: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
