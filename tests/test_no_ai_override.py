"""The AI limits are the WEARER's: no tool or agent-driven run may exceed them.

The rules this guards, in the order they were learned:
  * the limits file is the user's and a driver must never edit it;
  * a caller's --hardcap may only tighten the file's ceiling, never loosen it;
  * the level-override exists for ONE thing — the wearer's own Manual level, pushed
    by the loopback bridge from the page — and is refused everywhere else, including
    on the wrapper path that every tool actually uses.

Run: venv/bin/python tests/test_no_ai_override.py
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAY = os.path.join(ROOT, "k250_play.py")
fails = []


def engine(extra, env=None):
    e = dict(os.environ)
    e.pop("K250_WRAPPED", None)
    e.pop("K250_WEARER_OVERRIDE", None)
    e["K250_LIMITS"] = os.path.join(ROOT, "limits.json")
    if env:
        e.update(env)
    return subprocess.run([sys.executable, PLAY, "tide", *extra],
                          capture_output=True, text=True, timeout=60, cwd=ROOT, env=e)


checks = []

# 1. the flag alone does nothing
r = engine(["--base", "60", "--peak", "60", "--secs", "1", "--override-ceiling"])
checks.append(("engine refuses the override without the wearer's marker",
               r.returncode == 2 and "REFUSED" in (r.stderr or ""), r.returncode))

# 2. the wrapper path is refused even if someone sets the marker
r = engine(["--base", "60", "--peak", "60", "--secs", "1", "--override-ceiling"],
           env={"K250_WRAPPED": "1", "K250_WEARER_OVERRIDE": "1"})
checks.append(("a wrapped (tool-path) run is refused even with the marker",
               r.returncode == 2 and "REFUSED" in (r.stderr or ""),
               (r.stderr or "").splitlines()[0] if r.stderr else r.returncode))

# 3. the wearer's own run (marker, unwrapped) gets past the guard. Use an unknown
#    pattern so nothing is driven: reaching the pattern error proves the guard passed.
r = engine(["--base", "45", "--override-ceiling"], env={"K250_WEARER_OVERRIDE": "1"})
checks.append(("the wearer's page run is allowed through the guard",
               "REFUSED" not in (r.stderr or ""), r.returncode))

# 4. the flag is documented as not-for-drivers, not as a feature of the CLI
src = open(PLAY, encoding="utf-8").read()
checks.append(("the flag's help says it is the wearer's, not the driver's",
               "NOT for tool or AI-driven runs" in src, "NOT for tool or AI-driven runs" in src))
checks.append(("the guard exists in the engine next to apply_limits",
               "K250_WEARER_OVERRIDE" in src and "K250_WRAPPED" in src, True))

# 5. only ONE place in the whole tree ever sets the marker: the bridge
setters = []
for dirpath, dirnames, filenames in os.walk(ROOT):
    dirnames[:] = [d for d in dirnames if d not in (".git", "venv", "__pycache__", ".venv")]
    for name in filenames:
        if not (name.endswith((".py", ".sh", ".cmd", ".ps1")) or name == "k250-scene"):
            continue
        p = os.path.join(dirpath, name)
        try:
            text = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("#"):
                continue
            # An ASSIGNMENT, not a mention. Prose in help text and refusal messages
            # says "(K250_WEARER_OVERRIDE=1)", so match the two real ways to set it:
            #   python: env["K250_WEARER_OVERRIDE"] = "1"
            #   shell:  export K250_WEARER_OVERRIDE=1
            py_assign = re.search(r'(?:environ|env)\[\s*["\']K250_WEARER_OVERRIDE["\']\s*\]\s*=',
                                  s)
            sh_assign = re.search(r'(?:^|[\s;])K250_WEARER_OVERRIDE=1(?:\s|$)', s)
            if py_assign or sh_assign:
                setters.append(os.path.relpath(p, ROOT))
setters = sorted(set(setters))
checks.append(("the marker is set in exactly one file (the bridge)",
               setters == ["k250_launcher.py"], setters))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nNO AI OVERRIDE: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
