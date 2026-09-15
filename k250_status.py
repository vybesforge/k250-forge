#!/usr/bin/env python3
"""K250-4S — human-readable status. Read-only: never changes power.

Usage: python k250_status.py
"""
import asyncio
import json
import sys

from bleak import BleakClient
from k250_codec import READ_ALL

from k250_ble import K250, find

PATTERNS = ["Climb", "Combo", "Intense", "Manual", "Orgasm",
            "Rhythm", "Stroke", "Torment", "Waves"]


async def main():
    dev = await find()
    if dev is None:
        print("K250 NOT FOUND — is the box awake and on Remote App Control?")
        return 1

    async with BleakClient(dev, timeout=25) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.7)

        state = {}
        for key in ("FV", "BC", "MP", "PA", "MA", "AC"):
            k.last = None
            await k.send({key: ""})
            t = 0.0
            while t < 2.0:
                if k.last and key in k.last:
                    state[key] = k.last[key]
                    break
                await asyncio.sleep(0.1)
                t += 0.1

        ca = None
        k.last = None
        await k.send({"CA": ""})
        t = 0.0
        while t < 2.0:
            if k.last and "CA" in k.last:
                ca = k.last["CA"]
                break
            await asyncio.sleep(0.1)
            t += 0.1

        print("K250-4S status")
        print(f"  address      : {dev.address}")
        print(f"  firmware     : {state.get('FV', '?')}")
        print(f"  battery      : {state.get('BC', '?')}%")
        print(f"  max power lvl: {state.get('MP', '?')}  (L-{state.get('MP', '?')})")
        print(f"  speed (MA)   : {state.get('MA', '?')}")
        print(f"  selected ch  : {int(state.get('AC', 0)) + 1 if str(state.get('AC', 0)).isdigit() else state.get('AC')}")
        pa = state.get("PA")
        if isinstance(ca, list):
            live = [i + 1 for i, s in enumerate(ca) if str(s).strip().lower() != "unplugged"]
            print(f"  LIVE channels: {live if live else 'none'}   (CA={ca})")
            for i, s in enumerate(ca):
                p = pa[i] if isinstance(pa, list) and i < len(pa) else "?"
                print(f"    ch{i + 1}: {str(s):9s} pattern={p}")
        else:
            print("  channels     : (no CA reply)")
        print("  NOTE: power (PW) is never reported by the box — only echoed on write.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))