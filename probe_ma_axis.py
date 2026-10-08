#!/usr/bin/env python3
"""Map the box's MA axis: which values it accepts, and where it clamps.

Run after a firmware update, or whenever the frequency axis appears dead — it prints
what was sent against what the box echoed, so a shrunken axis shows up immediately.

Power is held at 5% throughout (the shipped ceiling). MA is rested at 0 and power
zeroed when done. Prints every value sent and what the box echoes back.
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

LEVEL = "5"
SWEEP = ["0", "10", "25", "50", "75", "100", "150", "2500", "0"]
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


async def sync(k, wait=2.4):
    k.last = None
    await k.send(READ_ALL)
    await asyncio.sleep(wait)
    return k.last or {}


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
        st = await sync(k)
        print("box reports PA =", st.get("PA"), " AC =", st.get("AC"), "\n", flush=True)

        await k.send({"AC": "1"})
        await asyncio.sleep(0.5)
        rows = []
        try:
            print(f"--- power {LEVEL}% ---", flush=True)
            await k.send({"PW": LEVEL})
            await asyncio.sleep(1.2)
            for ma in SWEEP:
                k.seen.clear()
                await k.send({"MA": ma})
                await asyncio.sleep(HOLD)
                echoes = [o.get("MA") for o in k.seen if "MA" in o]
                rows.append((ma, echoes[-1] if echoes else None))
                print(f"[{time.strftime('%H:%M:%S')}] sent MA={ma:<5} -> box echoed "
                      f"{echoes if echoes else 'nothing'}", flush=True)
        finally:
            print("\n--- zeroing ---", flush=True)
            try:
                await k.send({"MA": "0"})
                await asyncio.sleep(0.4)
                await k.send({"PW": "0"})
                await asyncio.sleep(0.4)
                await k.send({"AC": "0"})
                await asyncio.sleep(0.8)
            except Exception as e:
                print("  !! zeroing write failed:", e, flush=True)
        print("\n=== sent -> echoed ===")
        for sent, echoed in rows:
            mark = "" if echoed is None else ("  (SATURATED)" if str(echoed) != str(sent) else " ok")
            print(f"   {sent:>5} -> {echoed}{mark}")
    return 0


sys.exit(asyncio.run(main()))