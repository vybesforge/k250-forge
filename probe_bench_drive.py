#!/usr/bin/env python3
"""Bench feel-test: hold a power level, then work the MA axis.

MA is the character: 0 is the fastest buzz, 100 the slowest/heaviest. Glides read better
than jumps, so this mostly slides. Power is held under 35 and everything is written back
to zero on a `finally`, so an interrupt cannot leave the box energised.

  PW_HOLD=30 MA_MAX=100  ./probe_bench_drive.py
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from k250_ble import K250, clean, find          # noqa: E402
from k250_codec import READ_ALL                 # noqa: E402

POWER = os.environ.get("PW_HOLD", "30")
MA_MAX = float(os.environ.get("MA_MAX", "100"))


class Probe(K250):
    def __init__(self, client):
        super().__init__(client)
        self.seen = []

    def _on_notify(self, _sender, data):
        self.buf += clean(data.decode("utf-8", "replace"))
        try:
            obj = json.loads(self.buf)
        except Exception:
            return
        self.buf = ""
        self.last = obj
        self.seen.append(obj)


def stamp(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def live_channel(k):
    """AC is 0-INDEXED. Never assume 0 -- pick what CA says is plugged in."""
    k.last = None
    await k.send(READ_ALL)
    await asyncio.sleep(2.4)
    ca = [str(c).lower() for c in (k.last or {}).get("CA", [])]
    idx = next((i for i, c in enumerate(ca) if c and "unplug" not in c), 0)
    stamp(f"CA={ca or '(not reported)'} -> driving AC index {idx} (channel {idx + 1})")
    await k.send({"AC": str(idx)})
    await asyncio.sleep(0.4)
    return idx


async def glide(k, a, b, secs, steps=24):
    for i in range(steps + 1):
        v = a + (b - a) * i / steps
        await k.send({"MA": str(int(round(v)))})
        await asyncio.sleep(secs / steps)


async def main():
    dev = await find(timeout=15)
    if dev is None:
        print("box not on the air")
        return 1
    from bleak import BleakClient
    async with BleakClient(dev, timeout=30) as cl:
        k = Probe(cl)
        await k.start()
        await asyncio.sleep(1.0)
        try:
            await live_channel(k)
            await k.send({"PW": POWER})
            await asyncio.sleep(1.2)
            stamp(f"PW={POWER} held for the whole routine")

            stamp("MA 0 — fastest buzz, settle into it")
            await k.send({"MA": "0"}); await asyncio.sleep(3)

            stamp("glide 0 -> 100 over 7s (buzz slides into a slow heavy thump)")
            await glide(k, 0, MA_MAX, 7)
            await asyncio.sleep(2)

            stamp("glide 100 -> 0 over 5s (and back to the buzz)")
            await glide(k, MA_MAX, 0, 5)
            await asyncio.sleep(1.5)

            stamp("staircase up: 20 / 40 / 60 / 80 / 100, ~1.2s each")
            for step in (20, 40, 60, 80, 100):
                await k.send({"MA": str(step)})
                await asyncio.sleep(1.2)

            stamp("drop to 25, then step 50 / 75 / 100 — flighty")
            for step in (25, 50, 75, 100):
                await k.send({"MA": str(step)})
                await asyncio.sleep(0.8)

            stamp("long slow glide 100 -> 0 over 9s, ending on the buzz")
            await glide(k, MA_MAX, 0, 9)
            await asyncio.sleep(1.5)
        finally:
            print("\n--- zeroing ---", flush=True)
            try:
                await k.send({"MA": "0"}); await asyncio.sleep(0.3)
                await k.send({"PW": "0"}); await asyncio.sleep(0.9)
                await k.send({"AC": "0"})
                stamp("zeroed: MA 0, PW 0, AC 0")
            except Exception as e:
                print("  !! COULD NOT ZERO:", e, flush=True)
                return 1
    return 0


sys.exit(asyncio.run(main()))