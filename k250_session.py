#!/usr/bin/env python3
"""K250-4S session ledger — the session TIMER that makes session.max_duration_s visible.

The limit is a budget of *running time*, not wall-clock ownership of the box.

It does not block anything, and it never needs resetting by hand:

  * no time-based reset — pausing does not behave differently from playing;
  * no cooldown to wait out;
  * when the budget is reached the next run begins a NEW session immediately
    (`check` rolls it over and exits 0), so a session can follow a session
    back-to-back with no gap and no ceremony.

  python k250_session.py check --max 1800 [--dir DIR]   # rolls over if spent; always 0
  python k250_session.py add --seconds 45 [--dir DIR]   # record a run
  python k250_session.py show --max 1800 [--json] [--dir DIR]
  python k250_session.py reset [--dir DIR]              # optional: start a fresh one now

State lives in <dir>/session.json.
"""
import argparse
import json
import os
import sys
import time


def path(d):
    return os.path.join(d, "session.json")


def load(d):
    try:
        with open(path(d)) as f:
            s = json.load(f)
        if not isinstance(s, dict):
            raise ValueError
        return s
    except Exception:
        return {"session_start": None, "used_s": 0.0, "last_run_end": None}


def save(d, s):
    tmp = path(d) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=2)
    os.replace(tmp, path(d))


def roll(s, now):
    """No time-based rollover. A budget that refills itself after a pause is not a
    budget: the session runs until it is reset on purpose. This only *starts* a
    session the first time the ledger is used."""
    if s.get("last_run_end") is None:
        s["session_start"] = now
        s["used_s"] = 0.0
    return s


def fmt(sec):
    m, s = divmod(int(sec), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["check", "add", "show", "reset"])
    ap.add_argument("--max", type=float, default=1800.0)
    ap.add_argument("--seconds", type=float, default=0.0)
    ap.add_argument("--dir", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--json", action="store_true",
                    help="machine-readable state (used_s / max_s / left_s / spent)")
    a = ap.parse_args()
    now = time.time()
    s = load(a.dir)

    if a.cmd == "reset":
        save(a.dir, {"session_start": now, "used_s": 0.0, "last_run_end": None})
        print("session ledger reset — a fresh budget", file=sys.stderr)
        return 0

    if a.cmd == "add":
        roll(s, now)
        s["used_s"] = float(s.get("used_s", 0.0)) + max(0.0, a.seconds)
        s["last_run_end"] = now
        save(a.dir, s)
        left = a.max - s["used_s"]
        if left <= 0:
            print(f"session budget spent ({fmt(s['used_s'])} of {fmt(a.max)})", file=sys.stderr)
        return 0

    s = roll(s, now)
    used = float(s.get("used_s", 0.0))
    left = a.max - used

    if a.cmd == "show":
        if a.json:
            print(json.dumps({"used_s": round(used, 1), "max_s": a.max,
                              "left_s": round(max(0.0, left), 1),
                              "spent": left <= 0,
                              "session_start": s.get("session_start")}))
        else:
            print(f"session : {fmt(used)} used of {fmt(a.max)}  ({fmt(max(0, left))} left)")
        return 0

    # check — NEVER refuses. The budget marks the end of a session, not the end of
    # the evening: when it is reached the next run simply begins a new session,
    # immediately. No idle wait, no manual reset.
    if left <= 0:
        s = {"session_start": now, "used_s": 0.0, "last_run_end": s.get("last_run_end")}
        save(a.dir, s)
        print(f"session complete ({fmt(used)} of {fmt(a.max)}) — starting a new one now",
              file=sys.stderr)
        return 0
    save(a.dir, s)      # persist the session start if this is the first use
    return 0


if __name__ == "__main__":
    sys.exit(main())