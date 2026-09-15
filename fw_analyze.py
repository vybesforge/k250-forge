#!/usr/bin/env python3
"""Analyze the estimbox DFU firmware container."""
import os, math, struct, collections

HERE = os.path.dirname(os.path.abspath(__file__))
p = os.path.join(HERE, "firmware", "estimbox_fw_v1.08_dfu.bin")
data = open(p, "rb").read()
print(f"file size: {len(data)}")

magic = data[:4]
name = data[4:12]
v1, v2 = data[12], data[13]
x1, x2 = data[14], data[15]
size_le = struct.unpack("<I", data[16:20])[0]
print(f"magic: {magic.hex()}")
print(f"name: {name}")
print(f"bytes12-13: {v1:#x} {v2:#x}  bytes14-15: {x1:#x} {x2:#x}")
print(f"u32 @16 LE: {size_le} (0x{size_le:x})")
print(f"20 + size_le = {20+size_le} vs file {len(data)} -> {'EXACT' if 20+size_le==len(data) else 'MISMATCH'}")

payload = data[20:20+size_le]

def entropy(b):
    if not b: return 0
    c = collections.Counter(b)
    n = len(b)
    return -sum((v/n)*math.log2(v/n) for v in c.values())

print(f"payload len {len(payload)}; entropy {entropy(payload):.4f} bits/byte")

# check for known magics inside
for m in [b"\x1f\x8b", b"PK\x03\x04", b"\x28\xb5\x2f\xfd", b"LZ4", b"eStim", b"\xb4Fh\x04"]:
    idx = payload.find(m)
    print(f"magic {m!r}: first at {idx}")

# byte histogram of payload head
head = payload[:4096]
print("head hex:", head[:64].hex())

# count total occurrences of the outer magic
print("outer magic occurrences in payload:", payload.count(magic))

# possibly encrypted: check simple stats
b0 = collections.Counter(head)
print("distinct byte values in first 4k:", len(b0))

# tail
print("tail hex:", payload[-32:].hex())

# scan for ASCII strings >= 6 chars in whole payload (sample first 200k for speed)
import re
strings = re.findall(rb"[ -~]{6,}", payload[:400000])
print(f"strings found (first 400k): {len(strings)}")
for s in strings[:60]:
    print("  ", s[:100])
