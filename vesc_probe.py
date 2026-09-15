#!/usr/bin/env python3
"""Probe the K250-4S BLE pipe with VESC-protocol read-only commands."""
import asyncio
from bleak import BleakClient, BleakScanner

ADDR = "AA:BB:CC:11:22:33"
CHR  = "086e0001-7935-0d3a-ca91-bfb0c8c34043"

def crc16(buf: bytes) -> int:
    """VESC buffer.c crc16 (CCITT-style, init 0)."""
    crc = 0
    for b in buf:
        crc = ((crc >> 8) | ((crc << 8) & 0xFFFF)) & 0xFFFF
        crc ^= b
        crc ^= (crc & 0xFF) >> 4
        crc = (crc ^ ((crc << 12) & 0xFFFF)) & 0xFFFF
        crc = (crc ^ ((crc & 0xFF) << 5)) & 0xFFFF
    return crc

def vframe(payload: bytes) -> bytes:
    n = len(payload)
    if n <= 255:
        out = bytes([2, n]) + payload
    else:
        out = bytes([3, (n >> 8) & 0xFF, n & 0xFF]) + payload
    c = crc16(payload)
    return out + bytes([(c >> 8) & 0xFF, c & 0xFF, 3])

def try_parse(buf: bytes):
    if not buf:
        return None
    if buf[0] == 2 and len(buf) >= 5:
        n = buf[1]
        if len(buf) >= 5 + n - 1 and buf[4 + n - 1] == 3:
            payload = buf[2:2 + n]
            return payload
    return None

async def main():
    dev = await BleakScanner.find_device_by_address(ADDR, timeout=8)
    if dev is None:
        dev = await BleakScanner.find_device_by_name("Kx250-4S", timeout=10)
    if dev is None:
        print("device not found"); return
    print("dev:", dev.address, dev.name)
    notes = []
    async with BleakClient(dev, timeout=30) as cl:
        print("connected:", cl.is_connected)
        for i in range(3):
            v = await cl.read_gatt_char(CHR)
            print(f"read{i}: {v.hex()} (be_int={int.from_bytes(v,'big')}, le_int={int.from_bytes(v,'little')})")
            await asyncio.sleep(1.5)
        def cb(sender, data):
            notes.append(bytes(data))
        await cl.start_notify(CHR, cb)
        print("subscribed")
        probes = [
            ("fw_version  [00]", bytes([0x00])),
            ("get_values  [04]", bytes([0x04])),
            ("get_mcconf  [0e]", bytes([0x0E])),
        ]
        for name, p in probes:
            f = vframe(p)
            print(f"--> {name}: {f.hex()}")
            try:
                await cl.write_gatt_char(CHR, f, response=False)
            except Exception as e:
                print("  write(noresp) err:", repr(e))
                try:
                    await cl.write_gatt_char(CHR, f, response=True)
                except Exception as e2:
                    print("  write(resp) err:", repr(e2))
            await asyncio.sleep(2.5)
            while notes:
                d = notes.pop(0)
                print(f"<-- {len(d)}B {d.hex()}")
                pl = try_parse(d)
                if pl is not None:
                    print(f"    parsed payload ({len(pl)}B): {pl.hex()}")
                    print(f"    ascii: {''.join(chr(c) if 32<=c<127 else '.' for c in pl)}")
                elif len(d) > 1:
                    print(f"    raw ascii: {''.join(chr(c) if 32<=c<127 else '.' for c in d)}")
        await asyncio.sleep(2)
        while notes:
            d = notes.pop(0)
            print(f"<-- late {len(d)}B {d.hex()}")
    print("done")

asyncio.run(main())
