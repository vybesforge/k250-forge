#!/usr/bin/env python3
"""Extract keyword contexts + notable strings from minified JS."""
import re, sys, os

def contexts(src, kw, before=140, after=260, limit=12):
    out = []
    start = 0
    low = src.lower()
    kwl = kw.lower()
    while True:
        i = low.find(kwl, start)
        if i == -1 or len(out) >= limit:
            break
        lo = max(0, i - before)
        hi = min(len(src), i + after)
        out.append(src[lo:hi].replace("\n", " "))
        start = i + 1
    return out

def main():
    files = sys.argv[1].split(",")
    kws = sys.argv[2].split(",")
    for f in files:
        if not os.path.exists(f):
            print(f"== {f} MISSING")
            continue
        src = open(f, encoding="utf-8", errors="replace").read()
        print(f"\n######## {f} ({len(src)}B) ########")
        for kw in kws:
            ctxs = contexts(src, kw)
            if ctxs:
                print(f"\n=== keyword: {kw} ({len(ctxs)} shown)")
                for c in ctxs:
                    print("  |", c)

if __name__ == "__main__":
    main()
