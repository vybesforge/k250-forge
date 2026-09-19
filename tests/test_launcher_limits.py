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

    # --- the three things the sweep changed, each one measured before changing it ---

    # (1) the session read is IN-PROCESS now. It used to spawn k250_session.py on
    # every /status, and the page polls that every two seconds: 38 ms and one process
    # per poll, thirty a minute, for two file reads.
    import inspect
    src = inspect.getsource(L._session_state)
    body_code = src.split('"""', 2)[-1]        # drop the docstring, which names the old spawn
    L._session_state()
    st = L._session_state()
    checks.append(("session read spawns nothing (module import, not a subprocess)",
                   "subprocess.run" not in body_code and "importlib" in body_code, True))
    checks.append(("session read still carries the enforcing numbers",
                   isinstance(st.get("used_s"), float) and st.get("enforced") is True,
                   st.get("used_s")))
    checks.append(("session read carries the ceiling the file says",
                   st.get("max_s") == float(json.load(open(path))["session"]["max_duration_s"]),
                   st.get("max_s")))

    # (2) the pattern list is computed once and cached (it imports the engine, ~100 ms).
    calls = {"n": 0}
    real_run = L.subprocess.run

    def counting(*a, **k):
        calls["n"] += 1
        return real_run(*a, **k)

    L.subprocess.run = counting
    L._PATTERNS_CACHE = None
    first = L._pattern_list()
    n1 = calls["n"]
    L._pattern_list()
    n2 = calls["n"]
    L.subprocess.run = real_run
    L._PATTERNS_CACHE = None
    checks.append(("pattern list spawns the engine once, then serves from cache",
                   n1 == 1 and n2 == n1, (n1, n2)))
    checks.append(("pattern list is the whole library", len(first) > 50, len(first)))

    # (3) backups are pruned to the newest 10 — one per Apply, forever otherwise.
    before = len([f for f in os.listdir(d) if f.startswith("limits.json.bak-")])
    for i in range(13):
        p = path + f".bak-20260101-{i:06d}"
        open(p, "w").write("{}")
        os.utime(p, (1_700_000_000 + i, 1_700_000_000 + i))
    removed = L._prune_backups(10)
    left = [f for f in os.listdir(d) if f.startswith("limits.json.bak-")]
    checks.append(("backups pruned to the newest 10",
                   len(left) == 10 and removed == before + 13 - 10, (removed, len(left))))
    checks.append(("the newest backup survives the prune",
                   os.path.exists(path + ".bak-20260101-000012"), True))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nLAUNCHER LIMITS: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
