"""Both output axes are firmware-dependent, and getting either wrong fails SILENTLY.

On 2.00.08b:

  * MA tops out at 100. Anything above comes straight back as 100 — measured on the
    box by echo, where MA 150 and MA 2500 both return 100. The patterns are written
    against the 0-10000 axis, so unmapped every MA write lands on the ceiling and the
    frequency axis looks dead: power moves, character never does.
  * PW is the percent itself. v1 wanted percent x100, so an unmapped 5% goes out as
    500 and clamps to the box's top — the page says 5% and the box is driven at full.

Both are checked here, including the claim that the UNMAPPED values land at or above the
box's top, so these tests fail if either bug comes back.

Run: venv/bin/python tests/test_firmware_axes.py
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from k250_play import (Player, ma_box_max_for, pw_scale_for, pct)   # noqa: E402

checks = []


class FakeK:
    """Stands in for the BLE link: records what the engine would put on the wire."""

    def __init__(self):
        self.sent = []

    async def send(self, obj):
        self.sent.append(obj)


async def ma_writes(fv, ma_top, values, force_raw=False):
    k = FakeK()
    pl = Player(k, 100.0)
    pl.ma_top = ma_top
    pl.ma_out_max = None if force_raw else ma_box_max_for(fv)
    for v in values:
        await pl.ma(v)
    return [str(f["MA"]) for f in k.sent if "MA" in f]


async def pw_writes(fv, percents, force_v1=False):
    k = FakeK()
    pl = Player(k, 100.0)
    pl.pw_scale = 100.0 if force_v1 else pw_scale_for(fv)
    for p in percents:
        await pl.w(p)
    return [str(f["PW"]) for f in k.sent if "PW" in f]


# --- firmware detection -------------------------------------------------------
checks.append(("2.00.08b is recognised as a short MA axis",
               ma_box_max_for("2.00.08b--v2.00.08b") == 100.0,
               ma_box_max_for("2.00.08b--v2.00.08b")))
checks.append(("a bare 'v2.0' is recognised too",
               ma_box_max_for("v2.0") == 100.0, ma_box_max_for("v2.0")))
checks.append(("v1.08 keeps the raw MA axis",
               ma_box_max_for("1.08--v1.08") is None, ma_box_max_for("1.08--v1.08")))
checks.append(("an unreadable firmware keeps v1 behaviour (fails safe)",
               ma_box_max_for("") is None and ma_box_max_for(None) is None,
               (ma_box_max_for(""), ma_box_max_for(None))))
checks.append(("v2 PW is the percent itself",
               pw_scale_for("2.00.08b--v2.00.08b") == 1.0, pw_scale_for("2.00.08b--v2.00.08b")))
checks.append(("v1 PW stays percent x100",
               pw_scale_for("1.08--v1.08") == 100.0, pw_scale_for("1.08--v1.08")))
checks.append(("an unreadable firmware keeps percent x100",
               pw_scale_for("") == 100.0, pw_scale_for("")))

# --- the MA mapping -----------------------------------------------------------
v2 = asyncio.run(ma_writes("2.00.08b--v2.00.08b", 10000.0, [0, 2500, 5000, 10000]))
checks.append(("v2: the 0-10000 MA axis maps onto the box's 0-100",
               v2 == ["0", "25", "50", "100"], v2))
v2_top = asyncio.run(ma_writes("2.00.08b--v2.00.08b", 2500.0, [0, 1250, 2500]))
checks.append(("v2: a narrower MA axis maps across the full width too",
               v2_top == ["0", "50", "100"], v2_top))
v1 = asyncio.run(ma_writes("1.08--v1.08", 10000.0, [0, 2500, 10000]))
checks.append(("v1: MA values written raw, exactly as before",
               v1 == ["0", "2500", "10000"], v1))
raw = asyncio.run(ma_writes("2.00.08b--v2.00.08b", 10000.0, [2500, 5000, 10000],
                            force_raw=True))
checks.append(("unmapped, every MA write lands above the box's apex (the bug)",
               all(int(v) > 100 for v in raw) and len(set(raw)) == len(raw), raw))

# --- the PW mapping -----------------------------------------------------------
pw2 = asyncio.run(pw_writes("2.00.08b--v2.00.08b", [5, 25, 30]))
checks.append(("v2: 5% goes out as 5, not 500", pw2 == ["5", "25", "30"], pw2))
pw1 = asyncio.run(pw_writes("1.08--v1.08", [5, 30]))
checks.append(("v1: 5% still goes out as 500", pw1 == ["500", "3000"], pw1))
pw_raw = asyncio.run(pw_writes("2.00.08b--v2.00.08b", [5, 30], force_v1=True))
checks.append(("unmapped, a 5% run is sent as 500 -- i.e. the box's top (the bug)",
               all(int(v) >= 100 for v in pw_raw), pw_raw))
checks.append(("pct() is still the plain v1 form by default",
               pct(5) == "500" and pct(5, 1.0) == "5", (pct(5), pct(5, 1.0))))

width = max(len(n) for n, _, _ in checks)
bad = 0
for name, ok, got in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
    bad += not ok
print(f"\nFIRMWARE AXES: {'PASS' if not bad else f'{bad} FAILED'}")
sys.exit(1 if bad else 0)