#!/usr/bin/env python3
"""K250-4S BLE driver — read state / set fields / monitor.

Usage:
  python k250_ble.py read                  # one state sync
  python k250_ble.py monitor [seconds]     # stay connected, print updates
  python k250_ble.py set AC=1 MA=0         # set fields (values are strings)
  python k250_ble.py raw '{"FV":""}'       # send a literal JSON object
"""
import asyncio
import json
import sys

from bleak import BleakClient, BleakScanner
from k250_codec import ADDR, NAME, CHR, READ_ALL, SVC


def clean(txt: str) -> str:
    return txt.replace("\n", " ").replace("\r", " ").replace("\x00", "")


class K250:
    def __init__(self, client):
        self.cl = client
        self.buf = ""
        self.last = None

    async def start(self):
        await self.cl.start_notify(CHR, self._on_notify)

    def _on_notify(self, _sender, data):
        self.buf += clean(data.decode("utf-8", "replace"))
        try:
            obj = json.loads(self.buf)
        except Exception:
            return
        self.buf = ""
        self.last = obj
        print("STATE:", json.dumps(obj, ensure_ascii=False), flush=True)

    async def send(self, obj: dict):
        payload = json.dumps(obj, separators=(",", ":")).encode()
        await self.cl.write_gatt_char(CHR, payload, response=True)


def _matches(dev, adv=None):
    uu = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
    if dev.address and dev.address.upper() == ADDR:
        return True
    if SVC.lower() in uu:
        return True
    return bool(dev.name and "Kx250" in dev.name)


async def find(timeout=12.0):
    """Return the box the MOMENT it answers; blind scan only as a fallback.

    This used to be a flat `BleakScanner.discover(timeout=12)`, and a discovery
    scan does not return early — it waits out the whole clock even when the box
    is sitting right there advertising. That was the twelve seconds of silence
    before every pattern started, in every tool, because they all call this.
    A filter scan returns as soon as the advertisement matches (typically 1-2 s).

    The slow path is kept only for the case the filter can miss — a BlueZ scan
    race or an odd adapter — and it is NOT run after a clean timeout, or a box
    that is asleep would cost double the wait.
    """
    try:
        dev = await BleakScanner.find_device_by_filter(
            lambda d, a: _matches(d, a), timeout=timeout)
        if dev is not None:
            return dev
        return None
    except Exception:
        pass
    res = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for addr, (dev, adv) in res.items():
        if _matches(dev, adv):
            return dev
    return None


async def main():
    args = sys.argv[1:] or ["read"]
    mode = args[0]
    dev = await find()
    if dev is None:
        print("K250 not found.\n"
              "  - wake the box: press any knob for ~1 second (side LED glows red)\n"
              "  - put it on Options -> 'Remote App Control'\n"
              "  - it advertises as 'Kx250-4S' (not 'k250'), at about -50 dBm up close\n"
              "  - it only advertises when awake and on that screen")
        return 1
    print("found", dev.address, dev.name)
    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(1.0)

        if mode == "read":
            await k.send(READ_ALL)
            await asyncio.sleep(3.0)

        elif mode == "monitor":
            secs = float(args[1]) if len(args) > 1 else 15.0
            await k.send(READ_ALL)
            await asyncio.sleep(secs)

        elif mode == "set":
            for kv in args[1:]:
                key, _, val = kv.partition("=")
                print("set", key, "=", val)
                await k.send({key: val})
                await asyncio.sleep(0.4)
            await k.send(READ_ALL)
            await asyncio.sleep(3.0)

        elif mode == "raw":
            obj = json.loads(" ".join(args[1:]))
            print("raw", obj)
            await k.send(obj)
            await asyncio.sleep(3.0)

        else:
            print(__doc__)
            return 2

        print("last:", json.dumps(k.last, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
