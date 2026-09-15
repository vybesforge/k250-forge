"""Write-policy tests: redundant power writes are skipped, but never unsafely.

Run: venv/bin/python tests/test_write_policy.py
"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from k250_play import Player


class StubBox:
    def __init__(self):
        self.sent = []
    async def send(self, obj):
        self.sent.append(obj)
    async def start_notify(self, *a, **k):
        pass
    def pw(self):
        return [s["PW"] for s in self.sent if "PW" in s]


async def flat_power_writes(refresh, secs=1.0):
    os.environ["K250_PW_REFRESH"] = str(refresh)
    box = StubBox()
    pl = Player(box, 100)
    pl.channels = [0]
    await pl.set_pattern(["Manual"])
    box.sent.clear()
    await pl.hold(secs, 30.0, tick=0.1)     # flat power for `secs`
    return len(box.pw())


async def main():
    ticks = 10                              # 1.0s at 0.1s ticks
    every_tick = await flat_power_writes(0, 1.0)
    keepalive = await flat_power_writes(2.0, 1.0)
    print(f"flat power over {ticks} ticks:")
    print(f"  refresh=0 (old behaviour)  : {every_tick} power writes")
    print(f"  refresh=2s (new default)   : {keepalive} power write(s)")

    # the safety net: an unchanged value must still be re-sent eventually
    os.environ["K250_PW_REFRESH"] = "0.4"
    box = StubBox()
    pl = Player(box, 100)
    pl.channels = [0]
    await pl.set_pattern(["Manual"])
    box.sent.clear()
    await pl.hold(1.2, 30.0, tick=0.1)
    refreshed = len(box.pw())
    print(f"  refresh=0.4s over 1.2s     : {refreshed} writes (keepalive fires)")

    # a pattern change zeroes power on the box, so the next write must go out
    os.environ["K250_PW_REFRESH"] = "60"     # long keepalive: only a forced write can explain one
    box2 = StubBox()
    pl2 = Player(box2, 100)
    pl2.channels = [0]
    await pl2.set_pattern(["Manual"])
    await pl2.w(30.0)
    n_before = len(box2.pw())
    await pl2.w(30.0)                        # same value, inside the keepalive -> skipped
    n_same = len(box2.pw())
    await pl2.set_pattern(["Waves"])         # pattern change
    await pl2.w(30.0)                        # MUST re-send: the box is at zero
    n_after = len(box2.pw())
    print(f"\nunchanged value skipped      : {n_before} -> {n_same} writes "
          f"({'yes' if n_same == n_before else 'NO'})")
    print(f"re-sent after pattern change : {n_same} -> {n_after} writes "
          f"({'yes' if n_after > n_same else 'NO'})")

    ok = (every_tick >= ticks and keepalive <= 2 and refreshed >= 2
          and n_same == n_before and n_after > n_same)
    print("\nWRITE POLICY:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
