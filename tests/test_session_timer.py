"""The session figure is a TIMER, not a wall.

Regression guard for two real behaviours that were wrong in turn:
  1. an idle-gap rollover (15 quiet minutes refilled the budget) — a budget that
     refills itself after a pause is not a budget, and it made the limit depend on
     whether you happened to stop;
  2. a refusal (exit 1) once the budget was spent, which stopped a session dead and
     needed either a wait or a manual reset.

Now: no cooldown, no manual reset, and a spent session rolls over instantly, so a
run can follow a run back-to-back. The timer is information on the page.

Run: venv/bin/python tests/test_session_timer.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SES = os.path.join(ROOT, "k250_session.py")
MAX = 3600


def run(d, *args):
    return subprocess.run([sys.executable, SES, *args, "--dir", d],
                          capture_output=True, text=True, timeout=60)


def state(d):
    r = run(d, "show", "--json", "--max", str(MAX))
    return json.loads(r.stdout.strip().splitlines()[-1])


def main():
    checks = []
    with tempfile.TemporaryDirectory() as d:
        led = os.path.join(d, "session.json")

        # a session spent long ago, and idle for hours
        json.dump({"session_start": time.time() - 9000, "used_s": float(MAX),
                   "last_run_end": time.time() - 8000}, open(led, "w"))

        r = run(d, "check", "--max", str(MAX))
        checks.append(("check on a spent session does not refuse", r.returncode == 0,
                       r.returncode))
        checks.append(("and says a new session is starting",
                       "starting a new one" in (r.stderr or ""), (r.stderr or "").strip()))

        st = state(d)
        checks.append(("the ledger rolled over to zero", st["used_s"] == 0.0, st["used_s"]))
        checks.append(("with the full budget available", st["left_s"] == float(MAX), st["left_s"]))

        # a run right after the rollover is charged to the NEW session
        run(d, "add", "--seconds", "45")
        checks.append(("the next run is charged normally", state(d)["used_s"] == 45.0,
                       state(d)["used_s"]))

        # no idle-gap rollover: 3 hours quiet, usage intact
        json.dump({"session_start": time.time() - 12000, "used_s": 100.0,
                   "last_run_end": time.time() - 10800}, open(led, "w"))
        st = state(d)
        checks.append(("an idle gap does NOT reset the session", st["used_s"] == 100.0,
                       st["used_s"]))

        # an explicit reset still works, and is the only thing that zeroes it
        run(d, "reset")
        checks.append(("an explicit reset zeroes it", state(d)["used_s"] == 0.0,
                       state(d)["used_s"]))

        # and the source no longer contains an idle-gap concept at all
        src = open(SES, encoding="utf-8").read()
        checks.append(("no IDLE_GAP constant left in the module",
                       "IDLE_GAP" not in src, "IDLE_GAP" in src))

    width = max(len(n) for n, _, _ in checks)
    bad = 0
    for name, ok, got in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
        bad += not ok
    print(f"\nSESSION TIMER: {'PASS' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


sys.exit(main())
