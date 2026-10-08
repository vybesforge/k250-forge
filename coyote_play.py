#!/usr/bin/env python3
"""Play an imported stim on a DG-Lab Coyote V3 over BLE.

    python3 coyote_play.py --stim edge/edge_2_pulse_unsynced --level 30 --secs 60

Writes the exact 20-byte `B0` frames the source client uses (see
stim_library/SIGNAL-FORMAT.md), one every 100 ms. The stim's own (frequency, intensity)
timeline drives it directly, so this is near-lossless versus the app.

The wearer's limits.json still applies: `--level` (0..100) is clamped to the
power ceiling, and it becomes the frame's per-channel strength byte.

    python3 coyote_play.py --scan      # list BLE devices that look like a Coyote
"""
import argparse
import asyncio
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import stim_translate as T

CIVET_SERVICE = "0000180c-0000-1000-8000-00805f9b34fb"
CIVET_CHAR_WRITE = "0000150a-0000-1000-8000-00805f9b34fb"
CIVET_CHAR_NOTIFY = "0000150b-0000-1000-8000-00805f9b34fb"
TICK = 0.1


def _ceiling():
    """The wearer's power ceiling, straight from the same limits file."""
    try:
        from k250_play import find_limits, load_limits
        lim = load_limits(find_limits(None)) or {}
        return float((lim.get("power") or {}).get("max_percent", 50) or 50)
    except Exception:
        return 50.0


async def scan():
    from bleak import BleakScanner
    print("scanning 6s for a Coyote...")
    devs = await BleakScanner.discover(timeout=6.0)
    for d in devs:
        nm = d.name or ""
        mark = "  <-- looks like a Coyote" if "coyote" in nm.lower() else ""
        print(f"  {d.address}  {nm}{mark}")
    return 0


async def find_coyote(address=None):
    from bleak import BleakScanner
    if address:
        return address
    devs = await BleakScanner.discover(timeout=6.0)
    for d in devs:
        if (d.name or "").lower().find("coyote") >= 0:
            return d
    # fall back: any device advertising the Civet data service
    for d in devs:
        return None  # service UUIDs need a targeted scan; keep it simple
    return None


async def run(a):
    cat = T.load_catalog()
    c, entry = T.find_stim(cat, a.stim)
    if entry is None:
        print(f"unknown stim {a.stim!r}", file=sys.stderr)
        return 2
    ceil = 100.0 if a.manual else _ceiling()
    level = max(0, min(a.level, ceil))
    if level != a.level:
        print(f"level {a.level} clamped to the ceiling {ceil:g}%")
    tl, _ = T.stim_timeline(entry)
    tl = [s for s in tl if s[0] <= a.secs]
    frames = T.to_coyote(tl, str_a=int(level), str_b=int(level))
    print(f"stim  : {c}/{entry.get('name')}  ({len(frames)} frames, level {level:g}%)")

    dev = await find_coyote(a.address)
    if dev is None:
        print("Coyote not found — put it on, and try --scan to see what advertises.",
              file=sys.stderr)
        return 1
    addr = dev if isinstance(dev, str) else dev.address
    print(f"connecting to {addr} ...")

    from bleak import BleakClient
    stop = {"v": False}

    def sigint(*_):
        stop["v"] = True
        print("\n!! ABORT")
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, sigint)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, sigint)

    async with BleakClient(addr, timeout=30) as cl:
        ch = cl.services.get_characteristic(CIVET_CHAR_WRITE)
        if ch is None:
            print("Civet write characteristic not found — is this a Coyote V3?",
                  file=sys.stderr)
            return 1
        zero = T.to_coyote([(0, 1000, -34)], str_a=int(level), str_b=int(level))[0]
        try:
            start = time.time()
            for i, fr in enumerate(frames):
                if stop["v"]:
                    break
                tgt = i * TICK
                dt = tgt - (time.time() - start)
                if dt > 0:
                    await asyncio.sleep(dt)
                await cl.write_gatt_char(ch, bytes.fromhex(fr), response=False)
        finally:
            for _ in range(3):                     # land a zero-ramp
                try:
                    await cl.write_gatt_char(ch, bytes.fromhex(zero), response=False)
                    await asyncio.sleep(TICK)
                except Exception:
                    break
            print("zeroed")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stim")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--address", default=None, help="BLE address, skip the scan")
    ap.add_argument("--level", type=float, default=20.0)
    ap.add_argument("--secs", type=float, default=60.0)
    ap.add_argument("--manual", action="store_true",
                    help="MANUAL mode: limits.json is out of the loop (no ceiling clamp)")
    a = ap.parse_args()
    if a.list:
        for s in T.list_stims(T.load_catalog()):
            print(f"{s['category']}/{s['name']}")
        return 0
    if a.scan:
        return asyncio.run(scan())
    if not a.stim:
        print("pass --stim NAME (or --list / --scan)", file=sys.stderr)
        return 2
    return asyncio.run(run(a))


if __name__ == "__main__":
    sys.exit(main())
