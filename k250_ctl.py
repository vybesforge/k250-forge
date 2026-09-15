#!/usr/bin/env python3
"""K250-4S live controller — one persistent BLE link, driven from a FIFO.

Start (background):
  <repo>/venv/bin/python k250_ctl.py > /tmp/k250_ctl.log 2>&1 &
Drive it (the FIFO sits next to this script):
  printf '%s\n' '{"PW":"10"}' > <repo>/ctl.fifo
  printf '%s\n' read         > <repo>/ctl.fifo     # force a read-all
  printf '%s\n' quit         > <repo>/ctl.fifo     # zero output + disconnect

Every written object is followed by an automatic read-all so we can see
whether the box actually LATCHED the value or just echoed the raw key.
"""
import asyncio
import json
import os
import sys
import time

from bleak import BleakClient
from k250_codec import CHR, READ_ALL

from k250_ble import K250, find

FIFO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ctl.fifo")
T0 = time.time()


def log(*a):
    print(f"[{time.time()-T0:6.1f}]", *a, flush=True)


async def main():
    if not hasattr(os, "mkfifo"):
        log("k250_ctl.py needs a FIFO, which Windows does not have.")
        log("  Use the pattern engine directly instead: python k250_play.py <pattern> --base N --secs N")
        log("  (it reads limits.json itself, so the ceiling still applies).")
        return 1
    if not os.path.exists(FIFO):
        os.mkfifo(FIFO)
    fd = os.open(FIFO, os.O_RDONLY | os.O_NONBLOCK)

    dev = await find()
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    quit_now = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_fifo():
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            return
        for line in data.decode(errors="replace").splitlines():
            if line.strip():
                queue.put_nowait(line.strip())

    loop.add_reader(fd, on_fifo)

    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        log("notifications on; link HELD OPEN")
        await asyncio.sleep(0.8)
        await k.send(READ_ALL)
        await asyncio.sleep(1.2)

        last_ping = time.time()
        while not quit_now.is_set():
            try:
                line = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                line = None
            if line:
                if line in ("quit", "q"):
                    break
                if line == "read":
                    log("TX read-all")
                    await k.send(READ_ALL)
                elif line.startswith("w0 "):
                    obj = json.loads(line[3:])
                    log("TX(noresp)", obj)
                    await cl.write_gatt_char(
                        CHR, json.dumps(obj, separators=(",", ":")).encode(),
                        response=False)
                elif line.startswith("stream "):
                    _, payload, secs, ms = line.split()
                    obj = json.loads(payload)
                    t_end = time.time() + float(secs)
                    n = 0
                    while time.time() < t_end:
                        await k.send(obj)
                        n += 1
                        await asyncio.sleep(float(ms) / 1000.0)
                    log(f"streamed {obj} x{n} over {secs}s")
                    await k.send(READ_ALL)
                else:
                    try:
                        obj = json.loads(line)
                    except Exception as e:
                        log("BAD JSON:", e)
                        continue
                    log("TX", obj)
                    await k.send(obj)
                    await asyncio.sleep(0.4)
                    log("TX read-all")
                    await k.send(READ_ALL)
                await asyncio.sleep(0.6)
            if time.time() - last_ping > 20:
                await k.send(READ_ALL)
                last_ping = time.time()

        log("TX {'PW':'0'} (safe down)")
        await k.send({"PW": "0"})
        await asyncio.sleep(1.0)
        await k.send(READ_ALL)
        await asyncio.sleep(1.5)
    log("link closed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))