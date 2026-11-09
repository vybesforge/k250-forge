"""`hr_follow` — both axes ride the wearer's heart rate, direction configurable.

The point of this pattern is coupling: POWER and SPEED (MA) must both MOVE with
the sampled heart rate, not just one. Direction `up` means effort escalates (HR up
=> power up, buzz faster => MA down); direction `down` means strain eases (HR up =>
power down, buzz slower => MA up). And it must NEVER escalate on a stale/missing
heart rate — silence holds at the calm end. Those are the three things this guards,
plus registration and the ceiling.

Runs entirely off a trace file / env bpm — no radio.

Run: venv/bin/python tests/test_hr_follow.py
"""
import asyncio
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import k250_play as K


class Mock:
    """Enough of Player for a pattern: records writes, clamps like the real one."""

    def __init__(self, hardcap=80.0, ma_top=2500.0):
        self.hardcap = hardcap
        self.ma_top = ma_top
        self.stop = False
        self.deadline = None
        self.channels = [0]
        self.channel_caps = {}
        self.override_power = False
        self.max_rate = 0.0
        self.trace = []

    def expired(self):
        return self.deadline is not None and time.time() > self.deadline

    def cap_for(self, ch, kind, fallback):
        return fallback

    async def w(self, p):
        self.trace.append(("w", round(min(max(0.0, p), self.hardcap), 3)))

    async def ma(self, v):
        self.trace.append(("ma", round(v, 3)))

    async def hold(self, secs, p, tick=0.1):
        end = time.time() + secs
        while time.time() < end and not self.stop and not self.expired():
            await self.w(p)
            await asyncio.sleep(tick)

    async def sleep(self, s):
        await asyncio.sleep(0)


def run_with(env, trace_body, base, peak, secs, hardcap=80.0):
    """Write a trace file, point the pattern at it via env, run, return writes."""
    saved = {k: os.environ.get(k) for k in list(os.environ) if k.startswith("K250_HR_")
             or k.startswith("K250_MIRROR_")}
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        fh.write(trace_body)
    try:
        for k in list(os.environ):
            if k.startswith("K250_HR_") or k.startswith("K250_MIRROR_"):
                del os.environ[k]
        os.environ["K250_HR_TRACE"] = path
        os.environ.update(env)
        pl = Mock(hardcap=hardcap)
        pl.deadline = time.time() + secs
        asyncio.run(K.PATTERNS["hr_follow"](pl, base, peak, secs))
        pws = [v for k, v in pl.trace if k == "w"]
        mas = [v for k, v in pl.trace if k == "ma"]
        return pws, mas
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
        for k in list(os.environ):
            if k.startswith("K250_HR_") or k.startswith("K250_MIRROR_"):
                del os.environ[k]
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


checks = []

# --- registration ------------------------------------------------------------
checks.append(("it is registered", "hr_follow" in K.PATTERNS, "hr_follow" in K.PATTERNS))

# --- direction = up: high HR => high power, low MA (buzzy) -------------------
pws, mas = run_with({"K250_HR_DIR": "up", "K250_HR_LO": "100", "K250_HR_HI": "150"},
                    '{"hr":150}\n', 10, 60, 1.0)
checks.append(("UP@highHR: power near the top", pws and max(pws) > 50, max(pws) if pws else None))
checks.append(("UP@highHR: MA driven toward fast (low)", mas and min(mas) < 600,
               min(mas) if mas else None))

pws, mas = run_with({"K250_HR_DIR": "up", "K250_HR_LO": "100", "K250_HR_HI": "150"},
                    '{"hr":100}\n', 10, 60, 1.0)
checks.append(("UP@calmHR: power near the bottom", pws and max(pws) < 20, max(pws) if pws else None))
checks.append(("UP@calmHR: MA toward slow (high)", mas and max(mas) > 1800,
               max(mas) if mas else None))

# --- direction = down: high HR => LOWER power (ease off) ---------------------
pws, mas = run_with({"K250_HR_DIR": "down", "K250_HR_LO": "100", "K250_HR_HI": "150"},
                    '{"hr":150}\n', 10, 60, 1.0)
checks.append(("DOWN@highHR: power eases toward the floor", pws and max(pws) < 20,
               max(pws) if pws else None))
checks.append(("DOWN@highHR: MA eases toward slow", mas and max(mas) > 1800,
               max(mas) if mas else None))

# the two directions must be OPPOSITE on power at the same HR
pws_up, _ = run_with({"K250_HR_DIR": "up"}, '{"hr":145}\n', 10, 60, 1.0)
pws_dn, _ = run_with({"K250_HR_DIR": "down"}, '{"hr":145}\n', 10, 60, 1.0)
checks.append(("up and down are opposite at the same HR",
               max(pws_up) > max(pws_dn), (max(pws_up), max(pws_dn))))

# --- stale / missing HR: hold low, never escalate ----------------------------
pws, _ = run_with({"K250_HR_DIR": "up"}, "", 10, 60, 0.8)   # empty trace => no HR
checks.append(("no HR: never writes above base", pws and max(pws) <= 10.0,
               max(pws) if pws else None))

# --- ceiling still clamps ----------------------------------------------------
pws, _ = run_with({"K250_HR_DIR": "up", "K250_HR_LO": "100", "K250_HR_HI": "150"},
                  '{"hr":150}\n', 10, 200, 1.0, hardcap=45.0)
checks.append(("the ceiling still clamps it", pws and max(pws) <= 45.0,
               max(pws) if pws else None))

# --- stop flag ends it -------------------------------------------------------
pl = Mock()
pl.stop = True
os.environ["K250_HR_TRACE"] = "/nonexistent"
asyncio.run(K.PATTERNS["hr_follow"](pl, 10, 60, 5))
os.environ.pop("K250_HR_TRACE", None)
checks.append(("a stop flag ends it immediately", pl.trace == [], pl.trace))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nHR-FOLLOW: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
