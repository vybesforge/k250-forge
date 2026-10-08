#!/usr/bin/env python3
"""Can we set the PATTERN slot (PA) on firmware 2.x?

Read-only apart from PA writes -- and a PA change ZEROES that channel's power, so
nothing here can energise anything; it can only drop the level.

  "Intense" is the current slot (a known-good name off the box's own list) and "Sine"
  is a second control, so we can tell "this name is refused" apart from "PA is refused".
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from k250_ble import K250, clean, find          # noqa: E402
from k250_codec import READ_ALL                 # noqa: E402


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

        base = await sync(k)
        orig = list(base.get("PA") or [])
        print(f"before: PA={orig}\n", flush=True)
        if not orig:
            print("no PA list reported")
            return 1

        def frame(name):
            out = list(orig)
            out[0] = name
            return {"PA": out}

        attempts = [
            ("Manual (what the engine wants)", frame("Manual")),
            ("Manual, blanks for the dead channels",
             {"PA": ["Manual"] + [""] * (len(orig) - 1)}),
            ("Intense (already the current slot)", frame("Intense")),
            ("Sine (a name we know is in the box's list)", frame("Sine")),
        ]
        for label, f in attempts:
            print(f"try: {label}\n     -> {json.dumps(f)}", flush=True)
            await k.send(f)
            await asyncio.sleep(1.3)
            st = await sync(k)
            pa = st.get("PA") or []
            got = pa[0] if pa else None
            print(f"     box now says PA[0]={got!r}  "
                  f"{'STUCK' if got != (orig[0] if orig else None) else 'unchanged'}\n",
                  flush=True)

        print("restoring:", orig, flush=True)
        await k.send({"PA": orig})
        await asyncio.sleep(1.0)
        st = await sync(k)
        print("final: PA =", st.get("PA"))
    return 0


sys.exit(asyncio.run(main()))