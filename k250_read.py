#!/usr/bin/env python3
"""Read-only state probe: connect, subscribe, send the app's 'read' JSON, print replies."""
import asyncio, json
from bleak import BleakClient, BleakScanner

ADDR = "AA:BB:CC:11:22:33"
CHR  = "086e0001-7935-0d3a-ca91-bfb0c8c34043"

READ_REQ = {"AC":"","PW":"","MA":"","GP":"","PA":"","CA":"","MP":"","SB":"","BC":"","CS":"","FV":"","ER":"0"}

def show(d):
    txt = d.decode("utf-8", "replace")
    print(f"<-- {len(d)}B hex={d.hex()[:160]}")
    print(f"    text={txt[:400]!r}")
    try:
        obj = json.loads(txt)
        print("    JSON:", json.dumps(obj, ensure_ascii=False))
    except Exception:
        pass

async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=10)
    if dev is None:
        dev = await BleakScanner.find_device_by_name("Kx250-4S", timeout=10)
    if dev is None:
        print("not found"); return
    print("found", dev.address, dev.name)
    incoming = []
    async with BleakClient(dev, timeout=30) as cl:
        print("connected:", cl.is_connected)
        def cb(sender, data):
            incoming.append(bytes(data))
        await cl.start_notify(CHR, cb)
        print("notify on; passive wait 3s...")
        await asyncio.sleep(3)
        print(f"passive received: {len(incoming)}")
        for d in incoming: show(d)
        incoming.clear()
        payload = json.dumps(READ_REQ, separators=(",", ":")).encode()
        print("writing read request:", payload)
        try:
            await cl.write_gatt_char(CHR, payload, response=True)
            print("write(with response) ok")
        except Exception as e:
            print("write(resp) err:", repr(e))
            try:
                await cl.write_gatt_char(CHR, payload, response=False)
                print("write(no response) ok")
            except Exception as e2:
                print("write(noresp) err:", repr(e2))
        await asyncio.sleep(4)
        print(f"after write received: {len(incoming)}")
        for d in incoming: show(d)
        incoming.clear()
        await asyncio.sleep(2)
        if incoming:
            print("late:")
            for d in incoming: show(d)
    print("done")

asyncio.run(main())
