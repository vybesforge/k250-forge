"""Regression: a pattern change must force frequency + slew state to be re-sent."""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from k250_play import Player


class StubBox:
    def __init__(self):
        self.sent = []
        self.last = {}
    async def send(self, obj):
        self.sent.append(obj)
    async def start_notify(self, *a, **k):
        pass


async def main():
    box = StubBox()
    pl = Player(box, 100)
    pl.channels = [0]
    pl.ma_top = 2500.0
    pl.max_rate = 25.0

    # pretend we already set the frequency and power, so both caches are warm
    await pl.ma(2500)
    pl._ma = "2500"
    pl._slew_ch(0, 40.0)
    pl._ch_last[0] = (40.0, time.time())
    print("before pattern change: _ma =", pl._ma, "| slew state =", bool(pl._ch_last))

    # the pattern change: box zeroes power AND frequency
    await pl.set_pattern(["Waves", "Manual", "UNPLUG'D", "UNPLUG'D"])

    print("after  pattern change: _ma =", pl._ma, "| slew state =", bool(pl._ch_last))
    print("PA write was sent    :", any("PA" in s for s in box.sent))

    # now ma(2500) must actually WRITE again, not skip as a duplicate
    box.sent.clear()
    await pl.ma(2500)
    resent = any(s.get("MA") == "2500" for s in box.sent)
    print("ma() re-sent after PA:", resent)

    # and the slew limiter must start from zero, not from the stale 40%
    box.sent.clear()
    await pl.w(40.0)
    pw = [s.get("PW") for s in box.sent if "PW" in s]
    print("first power write    :", pw, "(should be ~0, climbing from zero)")

    ok = resent and pw and 0 < float(pw[0]) < 1500   # climbing from zero, not jumping to 40%
    print("\nRESULT:", "PASS" if ok else "FAIL")
    print(f"(first write {float(pw[0])/100:.2f}% after ~0.24s at 25%/s = a real climb from zero)")
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
