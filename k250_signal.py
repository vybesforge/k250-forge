#!/usr/bin/env python3
"""K250-4S signal sampler -- a SIDECAR that reports the box's RSSI while the
controller holds the connection.

Why a separate process: the controller holds the ONE BLE connection, and we learned
(live, 2026-10-22) that a long-lived process's own discovery wedges after a hard
drop. A passive scan does NOT connect -- the box keeps advertising while connected,
so a scanner can read its RSSI without ever touching the held link. Running the
sampler as its own process keeps it isolated from the controller's BlueZ state.

Writes {"rssi": N, "at": epoch} to the signal file every ~2s. The controller reads
that file and applies the warn/cutoff ladder. Never connects; safe to run alongside
the held link.

Run: venv/bin/python k250_signal.py   (spawned by the controller)
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from k250_codec import NAME, runtime_dir  # noqa: E402

SIGNALFILE = os.path.join(runtime_dir(), "signal.state")
SCAN_SECS = 2.0


def _write(rssi):
    try:
        tmp = SIGNALFILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"rssi": rssi, "at": time.time()}, f)
        os.replace(tmp, SIGNALFILE)
    except Exception as e:
        print(f"[signal] could not write {SIGNALFILE}: {e}", flush=True)


async def main():
    from bleak import BleakScanner          # lazy: only the radio path pays for it
    print(f"[signal] sampling RSSI for {NAME} every {SCAN_SECS}s -> {SIGNALFILE}",
          flush=True)
    while True:
        try:
            devs = await BleakScanner.discover(timeout=SCAN_SECS)
            hit = None
            for d in devs:
                # match by NAME -- the box's address is a random static LE address that
                # changes on power-cycle; ADDR in the codec is a placeholder that never
                # matches a real device.
                if d.name and "Kx250" in d.name:
                    hit = d
                    break
            if hit is not None:
                rssi = getattr(hit, "rssi", None)
                _write(rssi if rssi is not None else None)
            else:
                _write(None)                 # box not advertising right now
        except Exception as e:
            print(f"[signal] scan error: {type(e).__name__} {e}", flush=True)
            _write(None)
        await asyncio.sleep(0.2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass