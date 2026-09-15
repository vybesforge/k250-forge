"""Windows: the stop tool must not fail silently, and its parsing must be right on both platforms.

`k250_stop.py` kills any running pattern before zeroing the pads, so nothing re-sends power after the
stop. It found those processes by running `ps -eo pid,comm,args` — which does not exist on Windows,
where the whole thing was wrapped in `except Exception: return`: no kill, no message, and the tool
carried on looking like a clean stop. That is the worst shape a safety tool can fail in.

It now lists processes per platform (`ps` on POSIX, one PowerShell `Get-CimInstance Win32_Process`
call on Windows) and, when it cannot list them at all, says so out loud instead of pretending.

Neither branch can be run on the other platform, so the parsing — the part that decides *which pid
to kill* — is factored into pure functions and tested here against captured output from both. The
"could not list" path is exercised for real by pointing it at a command that does not exist.

Run: venv/bin/python tests/test_stop_parsers.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import k250_stop as S                                    # noqa: E402

MINE = 4242

# `ps -eo pid=,comm=,args=`, shaped like a real run (self, patterns, an unrelated python, a pattern
# that is stopping, and a grep that merely mentions the engine). Paths are deliberately generic:
# this file must not contain anything that looks like a real user's home directory.
PS_SAMPLE = """\
  123 python3         /srv/k250/venv/bin/python k250_play.py tide --base 5 --secs 30
 4242 python3         /srv/k250/venv/bin/python k250_stop.py
  456 Python          /opt/k250/venv/bin/python k250_show.py setlist
  777 python3         /srv/k250/venv/bin/python some_other_tool.py
  888 grep            grep k250_play /tmp/log
  999 python3         /srv/k250/venv/bin/python k250_play.py climb --secs 5
"""

# PowerShell: Get-CimInstance Win32_Process | ... | ForEach-Object { "$($_.ProcessId),$($_.CommandLine)" }
WIN_SAMPLE = """\
12345,C:\\k250\\venv\\Scripts\\python.exe k250_play.py tide --base 5 --secs 30
4242,C:\\k250\\venv\\Scripts\\python.exe k250_stop.py
 999,C:\\k250\\venv\\Scripts\\python.exe k250_show.py setlist
"""


def main():
    checks = []
    fails = []

    posix = S.parse_posix(PS_SAMPLE, MINE)
    checks.append(("posix: finds the running pattern", 123 in posix, posix))
    checks.append(("posix: finds a second pattern", 999 in posix, posix))
    checks.append(("posix: finds k250_show", 456 in posix, posix))
    checks.append(("posix: never kills itself", MINE not in posix, posix))
    checks.append(("posix: ignores other python tools", 777 not in posix, posix))
    checks.append(("posix: ignores a grep that mentions the engine", 888 not in posix, posix))
    checks.append(("posix: finds exactly three", len(posix) == 3, posix))

    win = S.parse_windows(WIN_SAMPLE, MINE)
    checks.append(("windows: finds the running pattern", 12345 in win, win))
    checks.append(("windows: never kills itself", MINE not in win, win))
    checks.append(("windows: finds exactly two", len(win) == 2, win))

    # a listing that cannot be done must be reported, not swallowed
    saved = S.PS_POSIX
    try:
        S.PS_POSIX = ["k250-command-that-does-not-exist"]
        pids, why = S.player_pids()
        checks.append(("unlistable processes -> empty list", pids == [], pids))
        checks.append(("unlistable processes -> a reason, not silence", bool(why), why or "None"))
    finally:
        S.PS_POSIX = saved

    # and on this platform the real listing works
    pids, why = S.player_pids()
    checks.append((f"real listing works here ({os.name})", why is None, why or "ok"))

    for label, ok, note in checks:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + (f"  — {note}" if note else ""))
        if not ok:
            fails.append(label)

    print()
    if fails:
        print(f"STOP PARSERS: FAIL ({len(fails)} of {len(checks)} checks)")
        return 1
    print(f"STOP PARSERS: PASS — all {len(checks)} checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
