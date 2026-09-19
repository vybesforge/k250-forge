"""`flat` — one level, MA pinned at zero, nothing to anticipate.

The pattern is the absence of a pattern: every other pattern in the library moves
something (power, character, or both), which always gives the wearer a shape to ride
or brace against. `flat` gives them nothing, which is the point. This guards that it
really is flat, really pins MA, and still obeys the ceiling like everything else.

Run: venv/bin/python tests/test_flat_pattern.py
"""
import asyncio
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import k250_play as K


class Mock:
    """Enough of Player for a pattern: records writes, clamps like the real one."""

    def __init__(self, hardcap=50.0, ma_top=2500.0):
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

    async def hold(self, secs, p, tick=0.1):
        end = time.time() + secs
        while time.time() < end and not self.stop and not self.expired():
            await self.w(p)
            await asyncio.sleep(tick)

    async def ma(self, v):
        self.trace.append(("ma", v))

    async def sleep(self, s):
        await asyncio.sleep(0)


def run(base, peak, secs, hardcap=50.0):
    pl = Mock(hardcap=hardcap)
    pl.deadline = time.time() + secs
    asyncio.run(K.PATTERNS["flat"](pl, base, peak, secs))
    pws = [v for k, v in pl.trace if k == "w"]
    mas = [v for k, v in pl.trace if k == "ma"]
    return pws, mas


checks = []

pws, mas = run(40, 40, 0.6)
checks.append(("it is registered", "flat" in K.PATTERNS, "flat" in K.PATTERNS))
checks.append(("MA is pinned at zero, and set once", mas == [0], mas))
checks.append(("every power write is the same number",
               len(set(pws)) == 1, sorted(set(pws))))
checks.append(("that number is the level it was asked for", pws and pws[0] == 40.0, pws[:1]))
checks.append(("it keeps writing for the duration", len(pws) > 3, len(pws)))

pws, _ = run(90, 90, 0.5, hardcap=45.0)
checks.append(("the ceiling still clamps it", set(pws) == {45.0}, sorted(set(pws))))

pws, _ = run(0, 0, 0.4)
checks.append(("zero is written as zero, not skipped", pws and set(pws) == {0.0}, sorted(set(pws))))

# it must respect the pattern contract the engine relies on: stop and deadline
pl = Mock()
pl.stop = True
asyncio.run(K.PATTERNS["flat"](pl, 40, 40, 5))
checks.append(("a stop flag ends it immediately",
               [v for k, v in pl.trace if k == "w"] == [], pl.trace))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nFLAT PATTERN: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)
