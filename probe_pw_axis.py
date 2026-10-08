#!/usr/bin/env python3
"""What does the box think PW means? Compare against a level the wearer set by hand.

He sets the box to a known level (say 25%) with its own controls, then we step PW upward and he
says which value matches. Capped at 30 (his stated ceiling) so it is safe whichever axis this is.

If the box's power axis is 0-100 these are 1-20 %; if it is 0-10000 they are 0.01-0.2 %.
Either reading is well inside the ceiling. Watch the BOX's LCD and note the power figure
it shows at each step — that is the one thing software cannot read.

Ends by resting at PW 0 whatever happens.
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

VALUES = ["1", "3", "5", "10", "20", "25", "30", "0"]
HOLD = 5.0


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
        print("box reports MP (its own max power level) =", st.get("MP"),
              " FV =", st.get("FV"), "\n", flush=True)

        await k.send({"AC": "1"})
        await asyncio.sleep(0.5)
        try:
            for v in VALUES:
                k.seen.clear()
                await k.send({"PW": v})
                await asyncio.sleep(HOLD)
                echoes = [o.get("PW") for o in k.seen if "PW" in o]
                print(f"[{time.strftime('%H:%M:%S')}] sent PW={v:<3} -> box echoed "
                      f"{echoes if echoes else 'nothing'}", flush=True)
        finally:
            print("\n--- resting: PW 0 ---", flush=True)
            try:
                k.seen.clear()
                await k.send({"PW": "0"})
                await asyncio.sleep(1.0)
                print("  zero echo:", [o.get("PW") for o in k.seen if "PW" in o], flush=True)
            except Exception as e:
                print("  !! could not zero:", e, flush=True)
    return 0


sys.exit(asyncio.run(main()))