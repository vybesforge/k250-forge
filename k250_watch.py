#!/usr/bin/env python3
"""Watch for the K250 to advertise (poll scans; print hits with timestamps)."""
import asyncio
import sys
import time

from bleak import BleakScanner

TARGET = "086e0000-7935-0d3a-ca91-bfb0c8c34043"
T0 = time.time()
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 180.0


async def main():
    best = {}
    while time.time() - T0 < DUR:
        try:
            res = await BleakScanner.discover(timeout=8, return_adv=True)
        except Exception as e:
            print(f"[{time.time()-T0:6.1f}] scan error {e}", flush=True)
            continue
        for addr, (d, a) in res.items():
            uu = [u.lower() for u in (a.service_uuids or [])]
            if TARGET in uu or (d.name and "Kx250" in d.name):
                print(f"[{time.time()-T0:6.1f}] FOUND {addr} {d.name} rssi={a.rssi}", flush=True)
                return
            if a.rssi > -60:
                best[addr] = (d.name, a.rssi, uu)
        print(f"[{time.time()-T0:6.1f}] scan done, no K250 (strong: "
              f"{[(k, v[1]) for k, v in best.items()]})", flush=True)
    print(f"[{time.time()-T0:6.1f}] timeout, never appeared", flush=True)


asyncio.run(main())