#!/usr/bin/env python3
"""Beautify all konnector JS chunks into konnector/pretty/ via jsbeautifier API."""
import glob, os, jsbeautifier

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "konnector")
DST = os.path.join(SRC, "pretty")
os.makedirs(DST, exist_ok=True)

opts = jsbeautifier.default_options()
opts.indent_size = 2

n = 0
for f in sorted(glob.glob(os.path.join(SRC, "*.js"))):
    base = os.path.basename(f)
    out = os.path.join(DST, base)
    try:
        src = open(f, encoding="utf-8", errors="replace").read()
        if not src.strip():
            print(f"SKIP empty: {base}")
            continue
        text = jsbeautifier.beautify(src, opts)
        open(out, "w", encoding="utf-8").write(text)
        n += 1
        print(f"ok {base} {os.path.getsize(out)}B")
    except Exception as e:
        print(f"FAIL {base}: {e!r}")
print(f"beautified {n} files into {DST}")
