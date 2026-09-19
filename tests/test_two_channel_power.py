"""The engine drives ONLY channel 1. No second channel is ever powered.

MULTI-CHANNEL DRIVING IS PULLED (2026-09-18). A two-channel drive went wrong:
channel 2 received far more than intended — the waveform base/peak frame was
channel 1's, but channel 2 rode its own cap. That behaviour is gone until it is
genuinely understood and re-derived. This test is the contract: whatever CA
reports, only channel 1 gets a PW write.

Run: venv/bin/python tests/test_two_channel_power.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import k250_play as K


class FakeK:
    """A stand-in for the real box: records the last write, never sleeps."""

    def __init__(self):
        self.sent = []
        self.last = None
        self.ac = None

    async def send(self, payload):
        key, val = next(iter(payload.items()))
        self.sent.append((key, val))
        self.last = val


async def collect_writes(channels, caps, level, hardcap):
    """Drive Player.w(level) for enough ticks and return every write."""
    k = FakeK()
    pl = K.Player(k, hardcap)
    pl.channels = channels
    pl.channel_caps = caps
    pl.max_rate = 0.0          # slew off: we test the cap, not the glide
    pl.channel_window = 0.01   # (legacy: rotation is gone, kept for clarity)
    pl._ma = None
    for _ in range(60):
        await pl.w(level)
        await asyncio.sleep(0)
    return k.sent


def pw_channels(sent):
    """Every PW write and which channel was selected just before it."""
    cur = None
    out = []
    for key, val in sent:
        if key == "AC":
            cur = int(val)
        elif key == "PW":
            out.append((cur, int(val) / 100.0))   # box speaks 0..10000 -> %
    return out


checks = []
hardcap = 75.0
caps = {"1": {"power": 70.0}, "2": {"power": 10.0}}

# --- even with TWO channels reported live, only ch1 is ever selected ---
sent = asyncio.run(collect_writes([0, 1], caps, 40.0, hardcap))
sel = [int(v) for k, v in sent if k == "AC"]
w = pw_channels(sent)
checks.append(("the engine never selects a second channel",
               sel and set(sel) == {0}, f"selected={sorted(set(sel))}"))
checks.append(("every PW write goes to ch1 only",
               w and all(c == 0 for c, _ in w), f"chans={sorted({c for c, _ in w})}"))
checks.append(("no PW write ever targets ch2",
               not any(c == 1 for c, _ in w), f"chans={sorted({c for c, _ in w})}"))
checks.append(("ch1's write is clamped to its own cap at most",
               w and max(v for _, v in w) <= 70.0, f"max={max(v for _, v in w) if w else None}"))

# --- even a CAPS map that raises ch2's cap does not resurrect the channel ---
sent = asyncio.run(collect_writes([0, 1], {"1": {"power": 70.0}, "2": {"power": 70.0}},
                                  40.0, hardcap))
w = pw_channels(sent)
checks.append(("a raised ch2 cap is ignored — ch2 still never powered",
               not any(c == 1 for c, _ in w), f"chans={sorted({c for c, _ in w})}"))

# --- single channel still works, and detect_channels always yields ch1 ---
async def _det():
    k = FakeK()
    pl = K.Player(k, hardcap)
    return await pl.detect_channels()
checks.append(("detect_channels reports ch1 even before reading the box",
               asyncio.run(_det()) == [0], asyncio.run(_det())))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nSINGLE-CHANNEL ONLY: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)