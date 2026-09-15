#!/usr/bin/env python3
"""Download any _next JS chunks referenced by already-downloaded files but missing locally."""
import re, os, glob, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KONN = os.path.join(HERE, "konnector")
BASE = "https://www.konnector.com/_next/"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"}

names = set()
for f in glob.glob(os.path.join(KONN, "*.js")):
    s = open(f, encoding="utf-8", errors="replace").read()
    names |= set(re.findall(r"static/chunks/[A-Za-z0-9/._-]+\.js", s))

print(f"{len(names)} chunk refs found")
got, skip, fail = [], [], []
for name in sorted(names):
    local = os.path.join(KONN, os.path.basename(name))
    if os.path.exists(local):
        skip.append(name); continue
    url = BASE + name
    try:
        req = urllib.request.Request(url, headers=UA)
        data = urllib.request.urlopen(req, timeout=30).read()
        open(local, "wb").write(data)
        got.append((name, len(data)))
    except Exception as e:
        fail.append((name, repr(e)))
print("DOWNLOADED:")
for n, s in got: print(f"  {n} {s}B")
print(f"already had: {len(skip)}")
print("failed:")
for n, e in fail: print(f"  {n} {e}")
