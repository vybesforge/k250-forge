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
    the page's picker is for our devices, not the neighbours' headphones.

    Uses the filtered scan, NOT `BleakScanner.discover()`. `discover()` opens a
    fresh active scan and fails with `org.bluez.Error.InProgress` whenever a
    previous scanner has not released the adapter; the filtered scan returns as
    soon as it matches and degrades to an empty list instead of exploding.
    """
    from bleak import BleakScanner

    found = {}

    def on_adv(d, adv):
        name = d.name or getattr(adv, "local_name", "") or ""
        found[d.address] = {
            "address": d.address,
            "name": name,
            "rssi": getattr(adv, "rssi", None),
            "kind": classify(name, getattr(adv, "service_uuids", None)),
        }

    # A raw scanner session, held open for the whole window. Do NOT use
    # find_device_by_filter here: on this bleak/BlueZ build the filter callback
    # fires once and the session closes, so the picker saw a single device (or
    # none) and reported an empty list while the box was plainly on the air.
    scanner = BleakScanner(detection_callback=on_adv)
    try:
        await scanner.start()
        await asyncio.sleep(max(secs, 4.0))
    except Exception:
        pass
    finally:
        try:
            await scanner.stop()
        except Exception:
            pass

    out = list(found.values())
    if only_estim:
        out = [d for d in out if d["kind"] != "other"]
    # our devices first, then strongest signal
    out.sort(key=lambda d: (d["kind"] == "other", -(d["rssi"] if d["rssi"] is not None else -999)))
    return out


if __name__ == "__main__":
    args = sys.argv[1:]
    only = "--all" not in args               # default: e-stim only
    nums = [a for a in args if not a.startswith("-")]
    secs = float(nums[0]) if nums else 6.0
    print(json.dumps(asyncio.run(scan(secs, only_estim=only))))
