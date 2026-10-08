#!/usr/bin/env python3
"""Hold a known power, sweep MA, then zero. The wearer is the instrument here.

PW is capped at 10 and MA stays inside 0-100 (the v2 axis). Both are restored to zero
at the end, on a `finally`, so an interrupt cannot leave the box energised.
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from k250_ble import K250, clean, find          # noqa: E402

POWER = os.environ.get("DRIVE_PW", "10")
MA_STEPS = ["0", "25", "50", "75", "100", "75", "50", "25", "0"]
HOLD = 4.0


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
        await k.send({"AC": "1"})
        await asyncio.sleep(0.5)
        try:
            k.seen.clear()
            await k.send({"PW": POWER})
            await asyncio.sleep(1.5)
            print(f"[{time.strftime('%H:%M:%S')}] PW={POWER}  echo "
                  f"{[o.get('PW') for o in k.seen if 'PW' in o]}", flush=True)
            for ma in MA_STEPS:
                k.seen.clear()
                await k.send({"MA": ma})
                await asyncio.sleep(HOLD)
                print(f"[{time.strftime('%H:%M:%S')}] MA={ma:<4} echo "
                      f"{[o.get('MA') for o in k.seen if 'MA' in o]}", flush=True)
        finally:
            print("\n--- zeroing ---", flush=True)
            try:
                await k.send({"MA": "0"})
                await asyncio.sleep(0.3)
                await k.send({"PW": "0"})
                await asyncio.sleep(0.8)
                await k.send({"AC": "0"})
                print("  zeroed (PW/MA/AC all written 0)", flush=True)
            except Exception as e:
                print("  !! COULD NOT ZERO:", e, flush=True)
                return 1
    return 0


sys.exit(asyncio.run(main()))