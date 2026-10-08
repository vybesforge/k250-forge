#!/usr/bin/env python3
"""Play an imported stim on the K250.

    python3 k250_stim_play.py --stim edge/edge_2_pulse_unsynced --level 30 --secs 60

The stim's timeline LOOPS until `--secs` is up (the page has no Run button — a
click plays, and it runs until the Duration slider expires). Reuses the tested
engine: the same `Player`, the same limits contract, the same stop/zero path. This is a launcher, never a bypass — `--level` is clamped to
the wearer's ceiling exactly as any pattern is (the page's Manual level wins only
via the wearer's own marker, same two locks as the engine).

    python3 k250_stim_play.py --stim <name> --list     # no box needed
"""
import argparse
import asyncio
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import stim_translate as T
from k250_play import (Player, K250, find, READ_ALL, find_limits, load_limits,
                       apply_limits, session_reserve, log, ma_box_max_for,
                       pw_scale_for)


def find_stim_or_die(name):
    cat = T.load_catalog()
    c, e = T.find_stim(cat, name)
    if e is None:
        print(f"unknown stim {name!r} — `--list` shows them all", file=sys.stderr)
        sys.exit(2)
    return c, e


async def run(a):
    cat, entry = find_stim_or_die(a.stim)
    ekey = f"{cat}/{entry.get('name')}"

    lim_path = find_limits(a.limits)
    lim = load_limits(lim_path)
    a.hardcap, a.max_rate, a.ma_top, a.channel_caps = apply_limits(
        a, lim, override=bool(a.override_ceiling))
    if not os.environ.get("K250_WRAPPED"):
        print(f"limits : {lim_path or '(none)'}  ceiling {a.hardcap:g}%  freq {a.ma_top:g}  "
              f"slew {(a.max_rate or 0):g}%/s")
    if not os.environ.get("K250_IGNORE_SESSION"):
        max_s = float(((lim or {}).get("session") or {}).get("max_duration_s", 1800) or 1800)
        if not session_reserve(HERE, max_s, a.secs + 5):
            return 1

    # ---- build the plan up front (offline; no box needed to see it) ----
    tl, _bv = T.stim_timeline(entry)
    tl = [s for s in tl if s[0] <= a.secs]
    ef = T.flat(entry.get("effectFrequence"), 0) or 1.0
    k = T.to_k250(tl, a.level, ma_top=a.ma_top, effect=ef)
    if not k:
        print("empty stim — nothing to play", file=sys.stderr)
        return 2
    print(f"stim   : {ekey}  ({len(k)} ticks, effectFreq {ef}, "
          f"PW {min(p for _,p,_ in k):.0f}-{max(p for _,p,_ in k):.0f}% )")

    dev = await find(address=a.address)
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    from bleak import BleakClient
    async with BleakClient(dev, timeout=30) as cl:
        kq = K250(cl)
        await kq.start()
        await asyncio.sleep(0.35)
        await kq.send(READ_ALL)
        t0 = time.time()
        while kq.last is None and time.time() - t0 < 2.0:
            await asyncio.sleep(0.05)
        log(f"state before: {json.dumps(kq.last or {}, ensure_ascii=False)}")

        pl = Player(kq, a.hardcap)
        pl.ma_top = a.ma_top
        _fv = (kq.last or {}).get("FV")
        pl.ma_out_max = ma_box_max_for(_fv)
        pl.pw_scale = pw_scale_for(_fv)
        if pl.ma_out_max or pl.pw_scale != 100.0:
            log(f"axes    : firmware {_fv} — MA apex {pl.ma_out_max or 'raw'}, "
                f"PW = percent x{pl.pw_scale:g}")
        pl.max_rate = a.max_rate
        pl.override_power = bool(a.override_ceiling)
        live = await pl.prepare_channels()
        log(f"live channels: {[c + 1 for c in live]}")
        pl.deadline = time.time() + a.secs

        def sigint(*_):
            pl.stop = True
            log("!! ABORT — zeroing")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(sig, sigint)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, sigint)

        log(f"PLAY stim {ekey} level={a.level}% secs={a.secs} (loops until the deadline)")
        try:
            while not pl.stop and not pl.expired():
                cyc = time.time()
                for (t, pw, ma) in k:
                    if pl.stop or pl.expired():
                        break
                    dt = t - (time.time() - cyc)
                    if dt > 0:
                        await asyncio.sleep(dt)
                    await pl.ma(ma)
                    await pl.w(pw)
        finally:
            await pl.w(0)
            await asyncio.sleep(0.4)
            await kq.send({"PW": "0"})
            await asyncio.sleep(0.8)
            await kq.send(READ_ALL)
            await asyncio.sleep(1.0)
            log("PW=0 (ended)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stim")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--level", type=float, default=20.0)
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--base", type=float, default=18.0)
    ap.add_argument("--peak", type=float, default=26.0)
    ap.add_argument("--hardcap", type=float, default=None)
    ap.add_argument("--override-ceiling", action="store_true")
    ap.add_argument("--limits", default=None)
    ap.add_argument("--address", default=None,
                    help="pin one BLE device by address (the page's BLE picker)")
    ap.add_argument("--frequency", type=float, default=None)
    ap.add_argument("--ma-top", type=float, default=None)
    ap.add_argument("--slew", type=float, default=None)
    ap.add_argument("--max-rate", type=float, default=None)
    ap.add_argument("--channel-caps", type=str, default="")
    a = ap.parse_args()
    if a.list:
        for s in T.list_stims(T.load_catalog()):
            print(f"{s['category']}/{s['name']}  seq={s['steps']}")
        return 0
    if not a.stim:
        print("pass --stim NAME (or --list)", file=sys.stderr)
        return 2
    a.ma_top = a.frequency if a.frequency is not None else (a.ma_top or 2500.0)
    a.max_rate = a.slew if a.slew is not None else (a.max_rate or 0.0)
    # refuse the wearer-only override outside the page, same locks as the engine
    if a.override_ceiling and (os.environ.get("K250_WRAPPED") == "1"
                               or os.environ.get("K250_WEARER_OVERRIDE") != "1"):
        print("REFUSED: --override-ceiling — only the page's Manual level may raise "
              "the wearer's ceiling.", file=sys.stderr)
        return 2
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
