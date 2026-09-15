#!/usr/bin/env python3
"""K250-4S session ledger — makes session.max_duration_s real.

The limit is a budget of *running time*, not wall-clock ownership of the box.
A session ends when the box has been idle for IDLE_GAP seconds; the next run
starts a fresh session. Usage:

  python k250_session.py check --max 1800 [--dir DIR]   # exit 1 if over budget
  python k250_session.py add --seconds 45 [--dir DIR]   # record a run
  python k250_session.py show --max 1800 [--dir DIR]
  python k250_session.py reset [--dir DIR]

State lives in <dir>/session.json. Exit 1 (and a loud message) means: this run
would exceed the budget the wearer agreed to. Starting a new session anyway is
a deliberate act -- `k250-scene --reset-session`.
"""
import argparse
import json
import os
import sys
import time

IDLE_GAP = 900.0        # 15 quiet minutes ends a session


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
    """Start a new session if the last activity was long enough ago."""
    last = s.get("last_run_end")
    if last is None or (now - last) > IDLE_GAP:
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
    ap.add_argument("--dir", default=os.path.expanduser("~/k250"))
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
        print(f"session : {fmt(used)} used of {fmt(a.max)}  ({fmt(max(0, left))} left)")
        return 0

    # check
    if left <= 0:
        print(f"""
SESSION BUDGET REACHED — refusing to start.
  used {fmt(used)} of the agreed {fmt(a.max)} maximum.

  This limit exists so a session doesn't drift past what was decided while you
  were calm. Options:
    * stop for now (the box is fine; the budget resets after 15 quiet minutes)
    * raise session.max_duration_s in limits.json if you both genuinely want more
    * start a fresh session deliberately:  k250-scene --reset-session
""", file=sys.stderr)
        return 1
    save(a.dir, s)      # persist the roll (new session start) if any
    return 0


if __name__ == "__main__":
    sys.exit(main())