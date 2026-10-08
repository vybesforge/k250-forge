"""The merged catalogue the page renders: no duplicates, no orphans, no guessing.

One list drives everything — the page grid, `k250-play --list`, and the key
resolution behind `k250-play <key>`. The rules this guards, in the order they were
learned:
  * the same stim must not appear twice under two names;
  * every key offered must actually be runnable (a real pattern, or a stim that
    resolves), so the page never shows a button that cannot fire;
  * a key the caller asks for must resolve to exactly one thing — an ambiguous
    query is refused with the candidates, never silently guessed;
  * the engine patterns the catalogue claimed to carry must really be in it.

Run: venv/bin/python tests/test_stim_catalog.py
"""
import collections
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import stim_catalog as SC          # noqa: E402
import stim_translate as T         # noqa: E402
import k250_play as K              # noqa: E402

checks = []
groups = SC.merged_groups()
items = [it for g in groups for it in g["items"]]

# --- 1. no duplicate labels, and no duplicate playable signals -----------------
labels = collections.Counter(it["label"] for it in items)
dupe_labels = sorted(l for l, c in labels.items() if c > 1)
checks.append(("no two keys show the same label", not dupe_labels, dupe_labels))

cat = T.load_catalog()
sigs = collections.defaultdict(list)
for it in items:
    if it["src"] != "stim":
        continue
    _, entry = T.find_stim(cat, it["key"])
    if not entry:
        continue
    try:
        frames, _ = T.stim_timeline(entry)
    except Exception:
        continue                      # the randomised stims cannot be hashed
    sigs[hashlib.sha1(json.dumps([[round(f, 2), round(v, 2)]
                                  for _, f, v in frames]).encode()).hexdigest()].append(it["key"])
dupe_sigs = {s: k for s, k in sigs.items() if len(k) > 1}
checks.append(("no two keys play the identical signal",
               not dupe_sigs, {k[0]: k for k in dupe_sigs.values()}))

# --- 2. every offered key is runnable -----------------------------------------
orphans = []
for it in items:
    if it["src"] == "engine":
        if it["key"] not in K.PATTERNS:
            orphans.append(it["key"])
    else:
        _, entry = T.find_stim(cat, it["key"])
        if entry is None:
            orphans.append(it["key"])
checks.append(("every offered key resolves to something runnable", not orphans, orphans))

# --- 3. the patterns the page used to hide are carried ------------------------
want = {"crawl", "pain_edge"}       # creep is superseded by crawl; flat went with Calibration
have = {it["key"] for it in items if it["src"] == "engine"}
missing = sorted(want - have)
checks.append(("the patterns the page used to hide are offered", not missing, missing))

checks.append(("the Calibration group is gone",
               not any(g["name"] == "Calibration" for g in groups),
               [g["name"] for g in groups]))

ugly = sorted(it["label"] for it in items
              if it["src"] == "engine"
              and ("_" in it["label"] or it["label"] != it["label"].capitalize()))
checks.append(("engine patterns display as words, not raw keys", not ugly, ugly))

# --- 4. the Intro staircase is whole, and named by its own numbers ------------
intro = sorted(int(it["label"].split()[1]) for it in items
               if it["label"].startswith("Intro ") and it["label"].endswith(" Hz"))
checks.append(("Intro 1-7 present, each labelled with its own frequency",
               intro == [1, 2, 3, 4, 5, 6, 7], intro))

# --- 5. resolve() finds each kind of key, and refuses to guess -----------------
src, key, _ = SC.resolve("tide")
checks.append(("resolve: a pattern name -> the engine", (src, key) == ("engine", "tide"),
               (src, key)))

src, key, _ = SC.resolve("EB-1-P/Intro6longPulseShortPause")
checks.append(("resolve: a category/name stim key", (src, key) == ("stim", "EB-1-P/Intro6longPulseShortPause"),
               (src, key)))

src, key, _ = SC.resolve("Intro 6")
checks.append(("resolve: the label the page shows", (src, key) == ("stim", "EB-1-P/Intro6longPulseShortPause"),
               (src, key)))

for query in ("pain", "stutter irregular"):
    try:
        got = SC.resolve(query)
        checks.append((f"resolve: '{query}' is refused as ambiguous", False, got))
    except LookupError as e:
        checks.append((f"resolve: '{query}' is refused as ambiguous",
                       "matches" in str(e), str(e)[:60]))

try:
    got = SC.resolve("no-such-key-anywhere")
    checks.append(("resolve: an unknown key is refused", False, got))
except LookupError as e:
    checks.append(("resolve: an unknown key is refused", "nothing" in str(e), str(e)[:60]))

# --- report -------------------------------------------------------------------
width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nSTIM CATALOGUE: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
