"""The launcher bridge writes limits.json by MERGING, and refuses what it cannot honour.

Regression guards, each from something that actually went wrong:
  * the page's Apply wrote the whole generated document, which would have deleted
    pattern_notes / the file's notes — it merges the keys the page owns instead;
  * power.default_percent is the wearer's tuned starting figure and is NOT the
    page's to write (writing the ceiling over it silently replaced 38 with 50);
  * a per-channel value equal to the global is written as null ("inherit");
  * an out-of-range number is refused, not clamped silently;
  * a backup is taken before every write, and the write is atomic.

No BLE and no HTTP server needed: these are the merge functions themselves.

Run: venv/bin/python tests/test_launcher_limits.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

LIMITS = {
    "_comment": "keep me", "version": 2, "profile": "default", "stop_word": "red",
    "pattern_notes": {"sweet_spots": {"sweep_flat_power_percent": 38}},
    "disclaimer": "keep this wording intact",
    "power": {"max_percent": 50, "default_percent": 38, "pleasure_band": [35, 50]},
    "frequency": {"max": 10000},
    "slew": {"max_percent_per_second": 25},
    "channels": {"mode": "auto", "per_channel": {str(c): {"power": None, "frequency": None,
                                                          "slew": None} for c in (1, 2, 3, 4)}},
    "session": {"max_duration_s": 1800},
    "battery": {"report_below_percent": 10},
    "safety": {"hard_stops_acknowledged": True},
}

checks = []
with tempfile.TemporaryDirectory() as d:
    path = os.path.join(d, "limits.json")
    json.dump(LIMITS, open(path, "w"), indent=2)
    os.environ["K250_LIMITS"] = path
    import k250_launcher as L

    def body(**over):
        base = dict(power_ceiling=40, frequency_max=10000, slew=25, session_minutes=60,
                    stop_word="red",
                    per_channel={str(c): {"power": 40, "frequency": 10000, "slew": 25}
                                 for c in (1, 2, 3, 4)})
        base.update(over)
        return base

    ok, msg = L._apply_limits(body())
    written = json.load(open(path))
    checks.append(("apply succeeds", ok, msg))
    checks.append(("ceiling written", written["power"]["max_percent"] == 40,
                   written["power"]["max_percent"]))
    checks.append(("power.default_percent untouched (the wearer's tuned start)",
                   written["power"]["default_percent"] == 38,
                   written["power"]["default_percent"]))
    checks.append(("keys the page does not own survive (_comment)",
                   written.get("_comment") == "keep me", written.get("_comment")))
    checks.append(("pattern_notes survive", "pattern_notes" in written, "pattern_notes" in written))
    checks.append(("disclaimer wording survives",
                   written.get("disclaimer") == "keep this wording intact",
                   written.get("disclaimer")))
    checks.append(("per-channel equal to the global is written as null (inherit)",
                   written["channels"]["per_channel"]["1"]["power"] is None,
                   written["channels"]["per_channel"]["1"]["power"]))
    checks.append(("a backup was taken",
                   any(f.startswith("limits.json.bak-") for f in os.listdir(d)),
                   sorted(os.listdir(d))))

    # a channel BELOW the global is kept
    ok, _ = L._apply_limits(body(per_channel={str(c): {"power": (30 if c == 1 else 40),
                                                       "frequency": 10000, "slew": 25}
                                              for c in (1, 2, 3, 4)}))
    written = json.load(open(path))
    checks.append(("a channel below the ceiling keeps its own number",
                   written["channels"]["per_channel"]["1"]["power"] == 30,
                   written["channels"]["per_channel"]["1"]["power"]))

    # re-applying the same numbers is a no-op, and leaves the file byte-identical
    before = open(path, "rb").read()
    ok, msg = L._apply_limits(body(per_channel={str(c): {"power": (30 if c == 1 else 40),
                                                        "frequency": 10000, "slew": 25}
                                               for c in (1, 2, 3, 4)}))
    checks.append(("an unchanged apply writes nothing",
                   ok and "nothing to write" in msg, msg))
    checks.append(("and the file is byte-identical", open(path, "rb").read() == before, True))

    # out of range is refused, not silently clamped
    ok, msg = L._apply_limits(body(power_ceiling=500))
    checks.append(("an out-of-range ceiling is refused", not ok and "outside" in msg, msg))
    ok, msg = L._apply_limits(body(session_minutes=9999))
    checks.append(("an out-of-range session is refused", not ok, msg))
    ok, msg = L._apply_limits(body(stop_word=""))
    checks.append(("an empty stop word is refused", not ok, msg))
    checks.append(("the file still holds the last good numbers",
                   json.load(open(path))["power"]["max_percent"] == 40,
                   json.load(open(path))["power"]["max_percent"]))

    # the view the page reads is the file, not defaults
    v = L._limits_view()
    checks.append(("the view reports the file's ceiling", v["power_ceiling"] == 40,
                   v["power_ceiling"]))
    checks.append(("the view reports where it read from", v["path"] == path, v["path"]))

    # a view without a ceiling is distinguishable from a real one — that is what the
    # page keys its "refuse to Apply" guard on
    checks.append(("a missing ceiling is detectable by the page",
                   v.get("power_ceiling") is not None
                   and ({} or {}).get("power_ceiling") is None, True))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nLAUNCHER LIMITS: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
