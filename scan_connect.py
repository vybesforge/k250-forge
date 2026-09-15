#!/usr/bin/env python3
"""K250-4S BLE recon: scan -> connect -> dump GATT -> catch notifications."""
import asyncio, traceback
from bleak import BleakScanner, BleakClient

TARGET_ADDR = "AA:BB:CC:11:22:33"

def name_match(d):
    n = (getattr(d, "name", None) or "")
    return "250" in n or "kink" in n.lower()

async def scan(timeout=12):
    res = await BleakScanner.discover(timeout=timeout, return_adv=True)
    print(f"== scan done: {len(res)} devices seen")
    target = None
    for addr, (dev, adv) in res.items():
        if name_match(dev) or addr.upper() == TARGET_ADDR:
            target = dev
            print(f"MATCH name={dev.name!r} addr={addr}")
            print(f"  rssi={getattr(adv,'rssi',None)} tx_power={getattr(adv,'tx_power',None)}")
            print(f"  service_uuids={adv.service_uuids}")
            md = {f"0x{k:04x}": v.hex() for k, v in adv.manufacturer_data.items()}
            sd = {k: v.hex() for k, v in adv.service_data.items()}
            print(f"  manufacturer_data={md}")
            print(f"  service_data={sd}")
    if target is None:
        print("no K250 in scan; top devices by rssi:")
        items = sorted(res.items(), key=lambda kv: -(getattr(kv[1][1], "rssi", None) or -999))
        for addr, (dev, adv) in items[:20]:
            print(f"  {getattr(adv,'rssi','?')} {dev.name!r} {addr}")
    return target

async def dump(client):
    print("== connected:", client.is_connected)
    svcs = client.services
    for s in svcs:
        print(f"SERVICE {s.uuid}  ({getattr(s,'description','')})")
        for c in s.characteristics:
            props = ",".join(c.properties)
            print(f"  CHAR {c.uuid} [{props}] handle=0x{c.handle:04x}")
            for d in c.descriptors:
                print(f"    DESC {d.uuid} handle=0x{d.handle:04x}")
            if "read" in c.properties:
                try:
                    v = await client.read_gatt_char(c)
                    print(f"    READ {v.hex()}")
                except Exception as e:
                    print(f"    READ-ERR {e!r}")
    notes = []
    def mk(u):
        def cb(sender, data):
            notes.append((str(u), bytes(data)))
        return cb
    for s in svcs:
        for c in s.characteristics:
            if ("notify" in c.properties) or ("indicate" in c.properties):
                try:
                    await client.start_notify(c, mk(c.uuid))
                    print(f"  SUBSCRIBED {c.uuid}")
                except Exception as e:
                    print(f"  SUB-ERR {c.uuid} {e!r}")
    await asyncio.sleep(10)
    print(f"== notifications over 10s: {len(notes)}")
    for u, d in notes[:120]:
        print(f"NOTIFY {u} {d.hex()}")

async def main():
    target = await scan()
    if target is None:
        return
    for attempt in (1, 2):
        print(f"== connect attempt {attempt} to {target.address}")
        try:
            async with BleakClient(target, timeout=30) as cl:
                await dump(cl)
                return
        except Exception as e:
            print(f"connect attempt {attempt} failed: {e!r}")
            traceback.print_exc()
            await asyncio.sleep(3)
    print("ALL CONNECT ATTEMPTS FAILED")

asyncio.run(main())
