#!/usr/bin/env python3
"""Why does the state read cost 2.4 s -- the box, or the way we ask?

Times three ways of getting the box's state off the wire:
  1. connect + start notifications               (the fixed cost of any fresh link)
  2. send READ_ALL and wait for the pushed reply (what the engine does today)
  3. a plain GATT read of the same characteristic (same data, if the box answers it)

Read-only: nothing here writes to the box.
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from k250_ble import K250, clean, find          # noqa: E402
from k250_codec import CHR, READ_ALL            # noqa: E402


class Probe(K250):
    def __init__(self, client):
        super().__init__(client)
        self.arrived = None

    def _on_notify(self, _sender, data):
        self.buf += clean(data.decode("utf-8", "replace"))
        try:
            obj = json.loads(self.buf)
        except Exception:
            return
        self.buf = ""
        self.last = obj
        if self.arrived is None:
            self.arrived = time.time()


async def main():
    t = {}
    t0 = time.time()
    dev = await find(timeout=15)
    t["find"] = time.time() - t0
    if dev is None:
        print("box not on the air")
        return 1
    from bleak import BleakClient

    t0 = time.time()
    async with BleakClient(dev, timeout=30) as cl:
        t["connect"] = time.time() - t0
        k = Probe(cl)
        t0 = time.time()
        await k.start()
        t["subscribe"] = time.time() - t0

        # 1. the way the engine does it: send READ_ALL, wait for the push
        for i in range(3):
            k.last = None
            k.arrived = None
            t0 = time.time()
            await k.send(READ_ALL)
            while k.arrived is None and time.time() - t0 < 6.0:
                await asyncio.sleep(0.01)
            t[f"READ_ALL #{i + 1}"] = time.time() - t0

        # 2. a plain GATT read of the same characteristic
        for i in range(3):
            t0 = time.time()
            try:
                raw = await cl.read_gatt_char(CHR)
                dur = time.time() - t0
                txt = clean(raw.decode("utf-8", "replace"))
                try:
                    obj = json.loads(txt)
                except Exception:
                    obj = txt[:70]
                t[f"GATT read #{i + 1}"] = dur
                print(f"   gatt read returned: {obj}")
            except Exception as e:
                t[f"GATT read #{i + 1}"] = f"FAILED {e}"
                print(f"   gatt read failed: {e}")

    print("\n=== timings (seconds) ===")
    for kk, v in t.items():
        print(f"  {kk:16} {v if isinstance(v, str) else f'{v:.2f}'}")
    return 0


sys.exit(asyncio.run(main()))