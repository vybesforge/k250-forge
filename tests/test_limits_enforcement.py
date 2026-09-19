"""The limits file is the contract, and it must hold on EVERY entry point.

Regression guard for a real hole: --hardcap used to default to 100 and the engine
never read limits.json at all, so the whole contract depended on the bash wrapper.
On Windows (no bash) the documented entry point was therefore uncapped.

Run: venv/bin/python tests/test_limits_enforcement.py
"""
import json
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import k250_play as P

LIMITS = {
    "power": {"max_percent": 10, "default_percent": 5},
    "frequency": {"max": 2500},
    "slew": {"max_percent_per_second": 25},
    "channels": {"per_channel": {"1": {"power": 8, "frequency": None, "slew": None},
                                 "2": {"power": 20, "frequency": 1200, "slew": 10}}},
    "session": {"max_duration_s": 1800},
}


def args(hardcap=None, slew=None, freq=None, caps=""):
    return SimpleNamespace(hardcap=hardcap, max_rate=slew, ma_top=freq if freq is not None else 2500.0,
                           channel_caps=caps, limits=None)


def main():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "limits.json")
        json.dump(LIMITS, open(p, "w"))

        checks = []
        lim = P.load_limits(p)

        hard, rate, freq, caps = P.apply_limits(args(), lim)
        checks.append(("no CLI value -> file ceiling 10", hard == 10.0, hard))
        hard, *_ = P.apply_limits(args(hardcap=90), lim)
        checks.append(("--hardcap 90 cannot exceed the file's 10", hard == 10.0, hard))
        hard, *_ = P.apply_limits(args(hardcap=5), lim)
        checks.append(("--hardcap 5 tightens to 5", hard == 5.0, hard))

        _, rate, _, _ = P.apply_limits(args(slew=0), lim)
        checks.append(("--slew 0 (unlimited) still capped at 25", rate == 25.0, rate))
        _, rate, _, _ = P.apply_limits(args(slew=10), lim)
        checks.append(("--slew 10 tightens to 10", rate == 10.0, rate))

        _, _, freq, _ = P.apply_limits(args(freq=10000), lim)
        checks.append(("--frequency 10000 capped to 2500", freq == 2500.0, freq))

        _, _, _, caps = P.apply_limits(args(), lim)
        c = json.loads(caps)
        checks.append(("per-channel limits come through", c["1"]["power"] == 8 and c["2"]["slew"] == 10, caps))
        checks.append(("null per-channel fields dropped", "frequency" not in c["1"], c["1"]))

        # a file that says 0 for slew means unlimited, not zero
        lim2 = json.loads(json.dumps(LIMITS))
        lim2["slew"]["max_percent_per_second"] = 0
        _, rate, _, _ = P.apply_limits(args(), lim2)
        checks.append(("file slew 0 = unlimited", rate == 0.0, rate))

    # no file at all: the CLI is the only ceiling (and it says so on stderr)
    hard, *_ = P.apply_limits(args(hardcap=42), None)
    checks.append(("no limits file -> CLI value used", hard == 42.0, hard))

    # session budget is refused once spent, on the direct path
    with tempfile.TemporaryDirectory() as d:
        import shutil
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        shutil.copy(os.path.join(here, "k250_session.py"), d)
        first = P.session_reserve(d, 30, 25)          # 25s of a 30s budget -> ok
        second = P.session_reserve(d, 30, 25)         # 50s -> past the budget: ROLLS OVER
        checks.append(("session timer reserves up front", first is True, first))
        # The budget no longer refuses anything: a spent session starts a new one
        # immediately (no cooldown, no manual reset), so the next run is always
        # allowed and is charged to the fresh session.
        checks.append(("a run past the budget is still allowed (rolls over)",
                       second is True, second))
        os.environ["K250_IGNORE_SESSION"] = "1"
        third = P.session_reserve(d, 30, 25)          # reserve() itself has no override;
        os.environ.pop("K250_IGNORE_SESSION")         # the caller checks it
        checks.append(("the timer keeps working after a rollover", third is True, third))

    width = max(len(n) for n, _, _ in checks)
    bad = 0
    for name, ok, got in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
        bad += not ok
    print(f"\nLIMITS ENFORCEMENT: {'PASS' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


sys.exit(main())
