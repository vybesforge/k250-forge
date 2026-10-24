"""Prove the two k250_stim_play fixes against a fake box (no radio).

1. channel_caps: run() must hand the per-channel caps from limits.json to the
   Player, so cap_for() actually clamps. Before the fix pl.channel_caps stayed
   {} and the ch1 power cap (30%) was silently ignored.
2. stop latency: a stop set during an event gap must land before the next write,
   not up to one event gap late.

Run: venv/bin/python tests/test_stim_play_fixes.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import k250_ble
import k250_stim_play as SP


class FakeClient:
    """Records every write; echoes a firmware state so the FV wait resolves."""
    def __init__(self):
        self.writes = []
    async def start_notify(self, *a):
        pass
    async def write_gatt_char(self, *a, **k):
        payload = a[1] if len(a) > 1 else a[0]
        obj = payload if isinstance(payload, dict) else json.loads(payload)
        self.writes.append(obj)


# --- wire the fake box into k250_ble.K250 ---
def _fake_init(self, client):
    self.cl = client
    self.buf = ""
    self.last = None
k250_ble.K250.__init__ = _fake_init
k250_ble.K250.start = lambda self: asyncio.sleep(0)
async def _fake_send(self, obj):
    await self.cl.write_gatt_char(obj)
    self.last = {"FV": "2.00.08b--v2.00.08b", "AC": 0,
                 "PA": ["Stroke", "UNPLUG'D", "UNPLUG'D", "UNPLUG'D"],
                 "CA": ["Active", "Unplugged", "Unplugged", "Unplugged"]}
k250_ble.K250.send = _fake_send


class FakeDev:
    address = "AA:BB:CC:11:22:33"
    name = "Kx250-4S"


async def fake_find(**k):
    return FakeDev()


def make_harness(limits, level=20.0, secs=2.0):
    """Return (run_args, client) with the fake box wired into SP's namespace."""
    client = FakeClient()

    import bleak
    class FakeBleakClient:
        def __init__(self, dev, timeout=30):
            pass
        async def __aenter__(self):
            return client
        async def __aexit__(self, *a):
            return False
    bleak.BleakClient = FakeBleakClient

    SP.find = fake_find

    d = tempfile.mkdtemp()
    lim_path = os.path.join(d, "limits.json")
    json.dump(limits, open(lim_path, "w"))

    a = SimpleNamespace(
        stim="edge/edge_2_pulse_unsynced", level=level, secs=secs,
        base=18.0, peak=26.0, hardcap=None, override_ceiling=False,
        limits=lim_path, address=None, frequency=None, ma_top=None,
        slew=None, max_rate=None, channel_caps="",
    )
    a.ma_top = 2500.0
    a.max_rate = 0.0
    return a, client


def main():
    os.environ["K250_IGNORE_SESSION"] = "1"
    checks = []

    # ---- 1. channel_caps flows from limits.json into the Player ----
    # Use a HIGH level so the stim's PW band exceeds the 30% ch1 cap. If the cap
    # is ignored (the bug), writes go up to the global 50% ceiling. If enforced,
    # they stay <= 30%.
    limits = {
        "power": {"max_percent": 50, "default_percent": 5},
        "frequency": {"max": 2500},
        "slew": {"max_percent_per_second": 25},
        "channels": {"per_channel": {"1": {"power": 30, "frequency": None, "slew": None}}},
        "session": {"max_duration_s": 1800},
    }
    a, client = make_harness(limits, level=45.0, secs=2.0)
    asyncio.run(SP.run(a))
    pw = [w["PW"] for w in client.writes if "PW" in w and str(w["PW"]) != ""]
    pw_pct = [float(p) for p in pw]
    checks.append(("channel_caps: PW writes stay within the 30% ch1 cap",
                   pw_pct and max(pw_pct) <= 30.0, f"max={max(pw_pct) if pw_pct else None}"))

    # ---- 2. stop lands promptly, not one event gap late ----
    # Drive the REAL run() loop. The bug: a stop set during the `sleep(dt)` between
    # events only lands on the NEXT write, so it can be up to one event gap late.
    # No real stim has a long gap (all are 0.1s sampled), so we patch asyncio.sleep
    # to inject the stop partway through the first gap, then count whether any
    # power write lands AFTER the stop flag is already set.
    #
    # With the fix, the loop re-checks stop right after the sleep and breaks, so
    # zero writes land after the stop. Without it, the loop writes the next event
    # before noticing, so at least one write lands after the stop.
    import asyncio as _asyncio
    real_sleep = _asyncio.sleep
    from k250_play import Player
    orig_w = Player.w
    state = {"pl": None, "stop_injected": False, "writes_after_stop": 0}

    async def w_track(self, p):
        if getattr(self, "stop", False) and p > 0:
            state["writes_after_stop"] += 1
        state["pl"] = self
        await orig_w(self, p)

    async def sleep_inject(dt):
        await real_sleep(dt)
        pl = state.get("pl")
        if pl is not None and not state["stop_injected"] and dt > 0.05:
            state["stop_injected"] = True
            pl.stop = True

    Player.w = w_track
    _asyncio.sleep = sleep_inject

    a2, client2 = make_harness(limits, level=20.0, secs=2.0)
    asyncio.run(SP.run(a2))
    _asyncio.sleep = real_sleep
    Player.w = orig_w

    checks.append(("stop latency: no power write lands after the stop flag is set",
                   state["writes_after_stop"] == 0,
                   f"writes after stop={state['writes_after_stop']}"))

    width = max(len(n) for n, _, _ in checks)
    bad = 0
    for name, ok, got in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
        bad += not ok
    print(f"\nSTIM_PLAY FIXES: {'PASS' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
