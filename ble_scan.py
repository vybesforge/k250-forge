#!/usr/bin/env python3
"""Scan BLE and list nearby devices, flagging K250 / Coyote.

    python3 ble_scan.py [seconds]      -> JSON list on stdout

Used by the launcher's /devices endpoint so the page can offer a device picker.
Read-only: it scans, it never connects.
"""
import asyncio
import json
import sys

from k250_codec import NAME as K250_NAME, SVC as K250_SVC
COYOTE_SVC = "0000180c-0000-1000-8000-00805f9b34fb"   # Civet data service (DG-Lab Coyote V3)

K250_HINTS = ("kx250", "k250")
COYOTE_HINTS = ("coyote", "civet", "dg-lab", "dglab")


def classify(name, uuids):
    n = (name or "").lower()
    uu = [u.lower() for u in (uuids or [])]
    if any(h in n for h in K250_HINTS) or K250_SVC.lower() in uu:
        return "k250"
    if any(h in n for h in COYOTE_HINTS) or COYOTE_SVC in uu:
        return "coyote"
    return "other"


async def scan(secs=6.0, only_estim=True):
    """Scan and return devices. `only_estim` (default) keeps just K250/Coyote —
    the page's picker is for our devices, not the neighbours' headphones."""
    from bleak import BleakScanner
    res = await BleakScanner.discover(timeout=secs, return_adv=True)
    out = []
    for addr, (dev, adv) in res.items():
        name = dev.name or getattr(adv, "local_name", "") or ""
        kind = classify(name, getattr(adv, "service_uuids", None))
        if only_estim and kind == "other":
            continue
        out.append({"address": addr, "name": name, "rssi": getattr(adv, "rssi", None), "kind": kind})
    # our devices first, then strongest signal
    out.sort(key=lambda d: (d["kind"] == "other", -(d["rssi"] if d["rssi"] is not None else -999)))
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    only = "--all" not in args               # default: e-stim only
    nums = [a for a in args if not a.startswith("-")]
    secs = float(nums[0]) if nums else 6.0
    print(json.dumps(asyncio.run(scan(secs, only_estim=only))))
