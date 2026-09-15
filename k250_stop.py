#!/usr/bin/env python3
"""K250-4S — INSTANT STOP. Zeroes power on every live channel and exits.

Designed to be safe to run at any time, including while a pattern is playing:
first kills any running pattern process (so it can't re-send power), then
connects and writes PW=0 to every channel the box reports as plugged in.

Usage: python k250_stop.py
Exit 0 on success, 1 if the box couldn't be reached (caller must treat that as
UNSAFE — the box may still be energised).
"""
import asyncio
import os
import signal
import subprocess
import sys
import time

from bleak import BleakClient
from k250_codec import CHR, READ_ALL

from k250_ble import K250, find

MINE = os.getpid()

PS_POSIX = ["ps", "-eo", "pid=,comm=,args="]
# Windows has no `ps`. Ask PowerShell for the pid + command line of anything running the engine;
# one "pid,commandline" line per match, so there is nothing to unquote.
PS_WINDOWS = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
              "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'k250_play|k250_show' } "
              "| ForEach-Object { \"$($_.ProcessId),$($_.CommandLine)\" }"]


def parse_posix(text, mine):
    """`ps -eo pid=,comm=,args=` -> pids of engine processes that are not us."""
    out = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, comm, args = parts
        if "python" not in comm.lower():          # macOS reports "Python"
            continue
        for token in ("k250_play", "k250_show"):
            if token in args and "k250_stop" not in args and pid.isdigit() and int(pid) != mine:
                out.append(int(pid))
                break
    return out


def parse_windows(text, mine):
    """PowerShell's "pid,commandline" lines -> pids of engine processes that are not us."""
    out = []
    for line in text.splitlines():
        pid, _, args = line.strip().partition(",")
        if not pid.strip().isdigit():
            continue
        if "k250_play" in args or "k250_show" in args:
            if "k250_stop" in args or int(pid) == mine:
                continue
            out.append(int(pid))
    return out


def player_pids():
    """(pids, how) — how is None when the listing could not be done at all, which the caller
    must say out loud rather than silently continuing as if nothing were running."""
    if os.name == "nt":
        cmd, parse = PS_WINDOWS, parse_windows
    else:
        cmd, parse = PS_POSIX, parse_posix
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as e:
        return [], f"could not list processes ({e})"
    if r.returncode != 0:
        return [], f"process listing failed (exit {r.returncode})"
    return parse(r.stdout, MINE), None


def kill_players():
    """Kill any running k250_play / k250_show so nothing re-sends power after we zero it.

    On Windows, os.kill(pid, SIGTERM) is TerminateProcess — abrupt, which is what a stop
    wants, and the PW=0 writes below follow it.
    """
    pids, why = player_pids()
    if why:
        print(f"WARNING: {why} — this stop cannot confirm no pattern is still running.",
              flush=True)
        return
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"killed pattern pid {pid}", flush=True)
        except Exception as e:
            print(f"could not kill {pid}: {e}", flush=True)


async def main():
    kill_players()
    await asyncio.sleep(1.5)          # let the pattern's own zero-on-exit run

    dev = await find()
    if dev is None:
        print("STOP FAILED: box not reachable — IT MAY STILL BE ENERGISED!\n"
              "  Get to it physically and hold any knob for 2 seconds to power it off.\n"
              "  (If it was asleep it stops advertising: wake it — press a knob ~1 s — and\n"
              "   put it on Options -> 'Remote App Control', then run k250-stop again.)",
              flush=True)
        return 1

    async with BleakClient(dev, timeout=25) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.6)
        ca = None
        k.last = None
        await k.send({"CA": ""})
        for _ in range(25):
            if k.last and "CA" in k.last:
                ca = k.last["CA"]
                break
            await asyncio.sleep(0.1)
        chans = list(range(4))
        if isinstance(ca, list):
            chans = [i for i, s in enumerate(ca)
                     if str(s).strip().lower() != "unplugged"] or list(range(4))
        for ch in chans:
            await k.send({"AC": str(ch)})
            await asyncio.sleep(0.05)
            await k.send({"PW": "0"})
            await asyncio.sleep(0.05)
        await k.send({"PW": "0"})
        await asyncio.sleep(0.5)
        print(f"STOPPED: PW=0 written to channels {[c + 1 for c in chans]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))