#!/usr/bin/env python3
"""K250-4S Manual-mode pattern engine.

In Manual mode the Power Multiplier (PW) is the only continuous control, so a
"pattern" here is a time-series of PW values. MA = Link Delay (Sec) in this
mode; the Reverse Polarity Switch is box-only (not in the BLE protocol).

VALUE SCALE: PW is 0..10000 on the box. This tool speaks PERCENT (1% = 100).
So --peak 30 sends PW=3000.

Usage:
  python k250_play.py <pattern> --base 18 --peak 26 --secs 30
  patterns: tide stutter trap climb dice edge

Safety: every run ends with PW=0, SIGINT sends PW=0 immediately, and a hard
--hardcap (default 100%) refuses to write above it.
"""
import argparse
import asyncio
import json
import math
import random
import signal
import sys
import time

from bleak import BleakClient
from k250_codec import CHR, READ_ALL

from k250_ble import K250, find

T0 = time.time()


def log(*a):
    print(f"[{time.time()-T0:6.1f}]", *a, flush=True)


def pct(p: float) -> str:
    return str(int(round(p * 100)))


class Player:
    def __init__(self, k: K250, hardcap: float):
        self.k = k
        self.hardcap = hardcap
        self.stop = False
        self.last = None
        self.ma_top = 2500.0          # apex of the speed axis (MA 0..2500)
        self.sweep_period = 8.0       # seconds per full speed sweep
        self.channels = [0]           # live channels (set by detect_channels)
        self._ch = None
        self.deadline = None          # absolute time the run must end by

    async def read_state(self, key: str, timeout: float = 3.0):
        """Ask the box for one key and wait for the reply. Returns None on timeout."""
        self.k.last = None
        await self.k.send({key: ""})
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.k.last and key in self.k.last:
                return self.k.last[key]
            await asyncio.sleep(0.1)
        return None

    async def detect_channels(self):
        """Which channels does the box say are actually plugged in?

        CA is e.g. ["Active","Unplugged","Unplugged","Unplugged"]. The box
        REFUSES to select an unplugged channel (AC write is silently ignored),
        so writing power to one is pointless — drive only the live ones."""
        ca = await self.read_state("CA")
        if not isinstance(ca, list):
            self.channels = [0]
            return self.channels
        self.channels = [i for i, s in enumerate(ca)
                         if str(s).strip().lower() != "unplugged"]
        if not self.channels:
            self.channels = [0]
        return self.channels

    async def select(self, ch: int):
        if getattr(self, "_ch", None) == ch:
            return
        self._ch = ch
        await self.k.send({"AC": str(ch)})
        await asyncio.sleep(0.04)

    async def w(self, p: float):
        """Write power. ALWAYS writes — no dedupe/cache.

        Do NOT reintroduce a "skip if unchanged" cache here: a pattern that
        held power flat while walking MA wrote PW exactly once and the box went
        silent for the whole run (operator felt nothing for 55 s), while an
        identical level re-sent periodically stayed alive. Treat PW as
        something the box must be told repeatedly.

        Multi-channel: PW applies to the SELECTED channel, so when more than
        one channel is live we select-and-write each in turn. Every pattern
        therefore drives all plugged channels without knowing about them."""
        p = max(0.0, min(p, self.hardcap))
        v = pct(p)
        for ch in self.channels:
            await self.select(ch)
            await self.k.send({"PW": v})

    async def hold(self, secs: float, p: float, tick: float = 0.1):
        end = time.time() + secs
        t = time.time()
        while time.time() < end:
            if self.stop or self.expired():
                return
            await self.w(p)
            await asyncio.sleep(tick)

    async def sleep(self, secs: float):
        end = time.time() + secs
        while time.time() < end and not self.stop and not self.expired():
            await asyncio.sleep(0.05)

    def expired(self) -> bool:
        """True once the run's overall deadline has passed. Patterns that loop a
        whole composition must check this or they overshoot the requested time."""
        return self.deadline is not None and time.time() > self.deadline

    async def ma(self, value: float):
        """Set Multi Adjust (frequency). MA=0 = buzziest, MA=5000 ~= 2 thump/s,
        MA=10000 ~= 1 thump/s (period ~= MA/10000 seconds).

        IMPORTANT: any setting-level write (MA/PA) can zero PW on the box, so
        drop the power cache and force the next tick to re-send it."""
        v = str(int(max(0, min(value, 10000))))
        if getattr(self, "_ma", None) == v:
            return
        self._ma = v
        self.last = None            # force PW re-send after the MA change
        await self.k.send({"MA": v})


# ---------------------------------------------------------------- patterns
# Each returns when done; every one must leave the box at 0 (done by caller).

async def p_tide(pl: Player, base: float, peak: float, secs: float):
    """Slow swell: smooth up-and-down between base and peak."""
    period = 8.0
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        ph = (time.time() - t0) / period * 2 * math.pi
        v = base + (peak - base) * (0.5 - 0.5 * math.cos(ph))
        await pl.w(v)
        await asyncio.sleep(0.1)


async def p_stutter(pl: Player, base: float, peak: float, secs: float):
    """Irregular bursts: short spikes, uneven gaps, no rhythm to lock onto."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        on = random.uniform(0.12, 0.35)
        off = random.uniform(0.18, 0.9)
        await pl.hold(on, random.uniform(peak * 0.85, peak), tick=0.05)
        await pl.hold(off, random.uniform(0, base * 0.4), tick=0.05)


async def p_trap(pl: Player, base: float, peak: float, secs: float):
    """Frustration loop: hold, drop to near-nothing for an UNKNOWN time,
    then come back higher than where it left. Relief is never predictable."""
    t0 = time.time()
    lvl = base
    while time.time() - t0 < secs and not pl.stop:
        await pl.hold(random.uniform(5, 8), lvl)
        await pl.hold(random.uniform(2.5, 7.0), random.uniform(0, max(1.0, base * 0.25)),
                      tick=0.05)
        lvl = min(peak, lvl + random.uniform(1.5, 3.0))
        await pl.hold(random.uniform(0.8, 1.6), min(peak + 3, lvl + 4), tick=0.05)


async def p_climb(pl: Player, base: float, peak: float, secs: float):
    """Sawtooth: creep up in small steps, snap back to base. Repeat."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        lvl = base
        while lvl < peak and not pl.stop:
            await pl.hold(1.6, lvl, tick=0.1)
            lvl += (peak - base) / 5.0
        await pl.hold(0.4, base, tick=0.05)


async def p_dice(pl: Player, base: float, peak: float, secs: float):
    """Random walk with surprise zeros — keeps you guessing where it sits."""
    t0 = time.time()
    lvl = base
    while time.time() - t0 < secs and not pl.stop:
        lvl = max(base * 0.5, min(peak, lvl + random.uniform(-4, 5)))
        if random.random() < 0.22:
            await pl.hold(random.uniform(0.2, 0.5), 0, tick=0.05)
        await pl.hold(random.uniform(0.6, 2.2), lvl, tick=0.1)


async def p_edge(pl: Player, base: float, peak: float, secs: float):
    """Hover just under a level, then push over it for a beat. """
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.hold(random.uniform(4, 7), base)
        await pl.hold(random.uniform(0.6, 1.4), min(pl.hardcap, peak), tick=0.05)
        await pl.hold(random.uniform(3, 6), base * 0.85)


async def p_verge(pl: Player, base: float, peak: float, secs: float):
    """Edge, sharpened: hover buzzy under the line, then push OVER it in thump
    mode (MA=5000 ~= 1 beat/s) so you can count the beats you can't have."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(0)
        await pl.hold(random.uniform(4, 7), base)
        await pl.ma(5000)
        await pl.hold(random.uniform(0.9, 1.5), min(pl.hardcap, peak), tick=0.05)
        await pl.ma(0)
        await pl.hold(random.uniform(3, 6), base * 0.85)


async def p_groove(pl: Player, base: float, peak: float, secs: float):
    """Locked to the thump: MA held at 1 beat/s, power pulsing once per beat so
    each thump lands heavier. Slow, deliberate, unavoidable."""
    await pl.ma(5000)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        lvl = random.uniform(peak * 0.88, peak)
        await pl.hold(0.45, lvl, tick=0.05)          # on the beat
        await pl.hold(0.55, base, tick=0.05)          # off the beat


async def p_switchback(pl: Player, base: float, peak: float, secs: float):
    """Climb, but the snap-back is not relief — it's a thump. Steps up buzzy,
    then drops into slow heavy beats before starting over."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(0)
        lvl = base
        while lvl < peak and not pl.stop:
            await pl.hold(1.6, lvl, tick=0.1)
            lvl += (peak - base) / 5.0
        await pl.ma(5000)
        for _ in range(3):
            await pl.hold(0.5, min(pl.hardcap, peak), tick=0.05)
            await pl.hold(0.5, base * 0.6, tick=0.05)
        await pl.ma(0)
        await pl.hold(0.5, base, tick=0.05)


async def p_map(pl: Player, base: float, peak: float, secs: float):
    """CALIBRATION: hold power flat at `base`, step MA from 0 (buzziest) up to
    5000 (~1 beat/s) in 20 even steps. Report which steps feel good."""
    steps = 20
    await pl.ma(0)
    await pl.hold(1.0, base, tick=0.1)
    for i in range(steps):
        if pl.stop:
            return
        v = int(5000 * i / (steps - 1))
        await pl.ma(v)
        await pl.hold(secs / steps, base, tick=0.1)
        if i % 5 == 0:
            print(f"      MA={v} ({v/100:.0f}%)", flush=True)


async def p_lift(pl: Player, base: float, peak: float, secs: float):
    """verge, with MA compensation: thump segments get +8% power because 1/s
    delivers less average energy. Beat count varies 1-3 so it's never the same."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(0)
        await pl.hold(random.uniform(5, 9), base)
        await pl.ma(5000)
        comp = min(pl.hardcap, peak + 8)
        for _ in range(random.randint(1, 3)):
            await pl.hold(random.uniform(0.55, 1.1), comp, tick=0.05)
            await pl.hold(random.uniform(0.5, 0.9), base * 0.5, tick=0.05)
        await pl.ma(0)
        await pl.hold(random.uniform(3, 6), base * 0.8)


async def p_dread(pl: Player, base: float, peak: float, secs: float):
    """Scarcity: long stretches near nothing, then a SINGLE heavy beat. The
    reward is rare enough that you start waiting for it."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(0)
        await pl.hold(random.uniform(9, 17), max(4, base * 0.35))
        await pl.ma(5000)
        await pl.hold(random.uniform(1.0, 1.8), min(pl.hardcap, peak + 8), tick=0.05)
        await pl.ma(0)
        await pl.hold(random.uniform(2, 4), base * 0.6)


async def p_pulse(pl: Player, base: float, peak: float, secs: float):
    """Mid-range MA with irregular power pulses — busier than groove, less
    chaotic than stutter. The rhythm drifts instead of repeating."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(random.choice([1500, 2200, 2800, 3400]))
        await pl.hold(random.uniform(0.25, 0.6), random.uniform(peak * 0.8, peak),
                      tick=0.05)
        await pl.hold(random.uniform(0.3, 0.9), random.uniform(base * 0.7, base * 1.1),
                      tick=0.05)


async def p_drift(pl: Player, base: float, peak: float, secs: float):
    """Slow power swell (tide) while MA drifts 1000->4000 and back — the
    character changes underneath you without the power ever jumping."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        ph = (time.time() - t0) / 20.0 * 2 * math.pi
        m = 2500 + 1500 * math.sin(ph)
        await pl.ma(m)
        v = base + (peak - base) * (0.5 - 0.5 * math.cos(ph / 2.5))
        await pl.w(v)
        await asyncio.sleep(0.12)


async def p_metronome(pl: Player, base: float, peak: float, secs: float):
    """Slow heavy beat (MA=8000 ~= 1.25/s), power surging on it, and the surge
    creeps UP over the run because slow beats deliver less average energy."""
    await pl.ma(8000)
    t0 = time.time()
    lvl = base
    period = 0.8
    while time.time() - t0 < secs and not pl.stop:
        lvl = min(pl.hardcap, lvl + 0.6)
        await pl.hold(period * 0.5, lvl, tick=0.05)
        await pl.hold(period * 0.5, base * 0.55, tick=0.05)


async def p_ration(pl: Player, base: float, peak: float, secs: float):
    """Discrete rations: a random 2-5 beats at peak in the mid band, then a long
    silence. You always know it's coming back — you never know when."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(random.choice([2000, 3000, 4000]))
        for _ in range(random.randint(2, 5)):
            await pl.hold(random.uniform(0.4, 0.8), random.uniform(peak * 0.85, peak),
                          tick=0.05)
            await pl.hold(random.uniform(0.3, 0.7), base * 0.4, tick=0.05)
        await pl.ma(0)
        await pl.hold(random.uniform(5, 9), max(3, base * 0.25), tick=0.1)


async def p_creep(pl: Player, base: float, peak: float, secs: float):
    """Frequency staircase: POWER HELD FLAT, MA walks up in small steps then
    drops back. Only the character changes — an axis we've never isolated."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        for i in range(12):
            if pl.stop:
                return
            await pl.ma(500 + i * 400)
            await pl.hold(1.8, (base + peak) / 2, tick=0.12)
        await pl.ma(0)


async def p_cooldown(pl: Player, base: float, peak: float, secs: float):
    """Ration's best feature, made the whole point: a burst of beats in the
    mid band, then MA drops to 0 and power falls to a low BUZZ that just sits
    there and hums. The cooldown is the reward, not the absence of one."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(random.uniform(1200, 2500))
        for _ in range(random.randint(4, 6)):
            await pl.hold(random.uniform(0.35, 0.7), random.uniform(peak * 0.85, peak),
                          tick=0.05)
            await pl.hold(random.uniform(0.25, 0.55), base * 0.5, tick=0.05)
        await pl.ma(0)
        print(f"      COOLDOWN at {time.time()-t0:.0f}s", flush=True)
        await pl.hold(random.uniform(3.5, 5.5), base * 0.35, tick=0.1)
        await pl.hold(random.uniform(1.0, 2.0), 0, tick=0.1)


async def p_buzzline(pl: Player, base: float, peak: float, secs: float):
    """Held in the 0-2500 band: power climbs smoothly while MA wanders inside
    the band every ~1.5 s. Busy, continuous, never silent."""
    t0 = time.time()
    t_next = 0.0
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        if el >= t_next:
            await pl.ma(random.uniform(900, 2500))
            t_next = el + 1.5
        v = base + (peak - base) * (0.5 - 0.5 * math.cos(el / 7.0 * 2 * math.pi))
        await pl.w(v)
        await asyncio.sleep(0.12)


async def p_crawl(pl: Player, base: float, peak: float, secs: float):
    """The honest redo of creep: power HELD FLAT, MA ping-pongs 0 -> 2500 -> 0
    in 250-steps while power keeps getting re-sent after each change."""
    hold_p = (base + peak) / 2
    t0 = time.time()
    step = 0
    direction = 1
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(step * 250)
        await pl.hold(2.0, hold_p, tick=0.1)
        step += direction
        if step >= 10:
            direction = -1
        if step <= 0:
            direction = 1


async def p_heavy(pl: Player, base: float, peak: float, secs: float):
    """Slow end done right: MA=8000, but power COMPENSATED hard (+18%) because
    1.25 beats/s delivers far less average energy than the buzzy end."""
    await pl.ma(8000)
    comp = min(pl.hardcap, peak + 18)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        for _ in range(random.randint(3, 6)):
            await pl.hold(random.uniform(0.5, 0.9), comp, tick=0.05)
            await pl.hold(random.uniform(0.5, 0.8), base * 0.45, tick=0.05)
        await pl.hold(random.uniform(2.5, 4.5), base * 0.5, tick=0.1)


async def p_sweep_drop(pl: Player, base: float, peak: float, secs: float):
    """The hard edge the sine sweep only hints at: climb speed 0 -> ma_top over
    ~6 s, sit at the top, then DROP INSTANTLY back to 0 and hold there. The
    landing is a step, not a glide."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        steps = 20
        for i in range(steps + 1):
            if pl.stop:
                return
            await pl.ma(pl.ma_top * i / steps)
            await pl.w(base)
            await asyncio.sleep(0.3)
        await pl.hold(3.5, base, tick=0.1)          # sit at the top
        await pl.ma(0)                              # THE DROP
        print(f"      DROP to 0 at {time.time()-t0:.0f}s", flush=True)
        await pl.hold(3.0, base * 0.85, tick=0.1)


async def p_sweep_hold_zero(pl: Player, base: float, peak: float, secs: float):
    """Sine sweep, but it DWELLS at both ends — long at 0 (buzzy, humming) and
    long at ma_top (slow), and refuses to linger in the middle."""
    period = pl.sweep_period * 1.6
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        ph = (el / period * 2 * math.pi) % (2 * math.pi)
        u = 0.5 - 0.5 * math.cos(ph)
        u = u ** 0.55 * (1 - u) ** 0.45 / 0.5      # push toward the extremes
        u = max(0.0, min(1.0, u))
        await pl.ma(pl.ma_top * u)
        await pl.w(base)
        await asyncio.sleep(0.1)


async def p_speed_sweep(pl: Player, base: float, peak: float, secs: float):
    """SPEED SWEEP: power held flat, MA sine-sweeps 0 <-> ma_top continuously.
    Same power the whole time — only the speed axis moves. """
    period = pl.sweep_period
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        u = 0.5 - 0.5 * math.cos(el / period * 2 * math.pi)
        await pl.ma(pl.ma_top * u)
        await pl.w(base)
        await asyncio.sleep(0.1)


async def p_speed_step(pl: Player, base: float, peak: float, secs: float):
    """SPEED STAIRCASE: power flat, MA steps 0 -> ma_top in 5 plateaus, then
    resets. Discrete speeds instead of a glide."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        for i in range(6):
            if pl.stop:
                return
            await pl.ma(pl.ma_top * i / 5.0)
            await pl.hold(2.2, base, tick=0.1)


async def p_lie(pl: Player, base: float, peak: float, secs: float):
    """The lie: POWER FALLS from peak to base while MA ramps 0 -> ma_top. The
    number on the display drops and the feel climbs anyway."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        u = min(1.0, el / (secs * 0.85))
        await pl.ma(pl.ma_top * u)
        await pl.w(peak - (peak - base) * u)
        await asyncio.sleep(0.1)


async def p_dual(pl: Player, base: float, peak: float, secs: float):
    """Staged: power climbs in 5 discrete steps while MA glides smoothly up to
    ma_top. They arrive together but by different routes."""
    t0 = time.time()
    stage = 0
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        u = min(1.0, el / (secs * 0.85))
        stage = min(5, int(u * 5))
        await pl.ma(pl.ma_top * u)
        await pl.w(base + (peak - base) * (stage / 5.0))
        await asyncio.sleep(0.1)


async def p_ascend(pl: Player, base: float, peak: float, secs: float):
    """BOTH axes rising together: MA smooth 0 -> 2500 while power ramps
    base -> peak. Verdict source: crawl proved MA lifts the feel with power
    held flat; this adds the power rise on top of it."""
    ma_top = pl.ma_top
    t0 = time.time()
    rise = secs * 0.75
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        u = min(1.0, el / rise)
        u = u * u * (3 - 2 * u)                      # smoothstep
        await pl.ma(ma_top * u)
        await pl.w(base + (peak - base) * u)
        await asyncio.sleep(0.1)


async def p_swell(pl: Player, base: float, peak: float, secs: float):
    """ascend, but it comes back down: MA 0->ma_top->0 and power base->peak->base.
    A full breath in and out."""
    ma_top = pl.ma_top
    t0 = time.time()
    up = secs * 0.45
    down = secs * 0.45
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        if el < up:
            u = el / up
        elif el < up + secs * 0.1:
            u = 1.0
        else:
            u = max(0.0, 1.0 - (el - up - secs * 0.1) / down)
        u = u * u * (3 - 2 * u)
        await pl.ma(ma_top * u)
        await pl.w(base + (peak - base) * u)
        await asyncio.sleep(0.1)


async def p_arc(pl: Player, base: float, peak: float, secs: float):
    """COMPOSED ARC (all four verified winners in one motion):
      1. two speed sweeps at your calibrated power (flat, ~13 s travel)
      2. climb to the top of the speed axis, sit, then HARD DROP to 0
      3. cooldown: MA=0 soft buzz that just hums at you
      4. back into the sweep, slightly hotter, and repeat
    """
    t0 = time.time()
    period = 13.0
    while time.time() - t0 < secs and not pl.stop:
        # --- 1. sweeps at base power
        for _ in range(2):
            s = time.time()
            while time.time() - s < period and not pl.stop:
                u = 0.5 - 0.5 * math.cos((time.time() - s) / period * 2 * math.pi)
                await pl.ma(pl.ma_top * u)
                await pl.w(base)
                await asyncio.sleep(0.1)
        if pl.stop:
            return
        # --- 2. climb, sit at the top, hard drop
        for i in range(21):
            if pl.stop:
                return
            await pl.ma(pl.ma_top * i / 20.0)
            await pl.w(base)
            await asyncio.sleep(0.3)
        await pl.hold(3.0, base, tick=0.1)
        await pl.ma(0)
        print(f"      DROP to 0 at {time.time()-t0:.0f}s", flush=True)
        await pl.hold(2.5, base * 0.92, tick=0.1)
        # --- 3. cooldown hum
        print(f"      COOLDOWN at {time.time()-t0:.0f}s", flush=True)
        await pl.hold(5.0, base * 0.72, tick=0.1)
        # --- 4. back into the sweep, hotter
        s = time.time()
        while time.time() - s < period and not pl.stop:
            u = 0.5 - 0.5 * math.cos((time.time() - s) / period * 2 * math.pi)
            await pl.ma(pl.ma_top * u)
            await pl.w(peak)
            await asyncio.sleep(0.1)


async def p_high_sweep(pl: Player, base: float, peak: float, secs: float):
    """HIGH SLOW SWEEP — both axes moving, both slowly, both out of phase.
    Power breathes 35 <-> 50 over 20 s while speed breathes 0 <-> ma_top over
    the same 20 s, offset a quarter turn so they never peak together."""
    T = 20.0
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        su = 0.5 - 0.5 * math.cos(el / T * 2 * math.pi)
        pu = 0.5 - 0.5 * math.cos(el / T * 2 * math.pi + math.pi / 2)
        await pl.ma(pl.ma_top * su)
        await pl.w(base + (peak - base) * pu)
        await asyncio.sleep(0.1)


async def p_power_sweep(pl: Player, base: float, peak: float, secs: float):
    """POWER SWEEP — speed parked mid-dial so the power axis is isolated.
    Power glides base <-> peak slowly; nothing else moves."""
    await pl.ma(pl.ma_top * 0.5)
    T = 20.0
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        el = time.time() - t0
        u = 0.5 - 0.5 * math.cos(el / T * 2 * math.pi)
        await pl.w(base + (peak - base) * u)
        await asyncio.sleep(0.1)


async def p_pain_edge(pl: Player, base: float, peak: float, secs: float):
    """Lives in the pleasure band, then takes a scheduled excursion above it.
    Slow speed throughout; the power steps out of the window for a few seconds
    and comes back down. Above the window is the point."""
    t0 = time.time()
    await pl.ma(pl.ma_top * 0.45)
    while time.time() - t0 < secs and not pl.stop:
        await pl.hold(random.uniform(6, 10), random.uniform(base, peak), tick=0.12)
        over = min(pl.hardcap, peak + random.uniform(6, 10))
        print(f"      ABOVE THE WINDOW: {over:.0f}% at {time.time()-t0:.0f}s", flush=True)
        await pl.hold(random.uniform(1.5, 3.0), over, tick=0.06)
        await pl.hold(random.uniform(2.0, 4.0), base * 0.9, tick=0.1)


async def p_power_sweep_rich(pl: Player, base: float, peak: float, secs: float):
    """POWER SWEEP, with a shape instead of a sine:
      - rise slowly to peak (7 s)
      - DWELL at the top (4 s) while speed drifts 8 -> 16, so the peak gets
        heavier the longer it sits there
      - fall faster than it rose (5 s)
      - rest just above base (2 s)
    Speed also wanders gently through the rise so it isn't a static axis."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        cyc = time.time() - t0
        ph = cyc % 18.0
        if ph < 7.0:                                  # rise
            u = ph / 7.0
            u = u * u * (3 - 2 * u)
            v = base + (peak - base) * u
            await pl.ma(pl.ma_top * (0.32 + 0.06 * math.sin(cyc / 3.0)))
        elif ph < 11.0:                               # dwell, speed climbs
            u = (ph - 7.0) / 4.0
            v = peak
            await pl.ma(pl.ma_top * (0.32 + 0.32 * u))
        elif ph < 16.0:                               # fall, faster
            u = 1.0 - (ph - 11.0) / 5.0
            v = base + (peak - base) * u
            await pl.ma(pl.ma_top * 0.64 * (1 - (ph - 11.0) / 5.0 * 0.4))
        else:                                         # rest
            v = base * 1.03
            await pl.ma(pl.ma_top * 0.2)
        await pl.w(v)
        await asyncio.sleep(0.08)


async def p_signature(pl: Player, base: float, peak: float, secs: float):
    """SIGNATURE — every element that worked, assembled in the order it flowed.

    1. open: speed sweep, power FLAT at base (~38%), 0<->25 over 13 s, x2
    2. high breath: power base<->peak (~38-50%) with speed out of phase
    3. climb to the top of the speed axis, sit, then HARD DROP to 0
    4. beats at the top after the landing
    5. cooldown: MA=0 soft hum that just sits there
    6. one deliberate excursion ABOVE the pleasure window, then back
    7. return to the sweep
    """
    T = pl.sweep_period
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop and not pl.expired():
        # 1. open sweeps
        print(f"      [1] SWEEP x2 at {base}%  ({time.time()-t0:.0f}s)", flush=True)
        for _ in range(2):
            s = time.time()
            while time.time() - s < T and not pl.stop and not pl.expired():
                u = 0.5 - 0.5 * math.cos((time.time() - s) / T * 2 * math.pi)
                await pl.ma(pl.ma_top * u)
                await pl.w(base)
                await asyncio.sleep(0.1)
        if pl.stop:
            return
        # 2. high breath, axes out of phase
        print(f"      [2] HIGH BREATH {base}-{peak}%  ({time.time()-t0:.0f}s)", flush=True)
        s = time.time()
        while time.time() - s < 20.0 and not pl.stop and not pl.expired():
            el = time.time() - s
            await pl.ma(pl.ma_top * (0.5 - 0.5 * math.cos(el / 20.0 * 2 * math.pi)))
            await pl.w(base + (peak - base) *
                       (0.5 - 0.5 * math.cos(el / 20.0 * 2 * math.pi + math.pi / 2)))
            await asyncio.sleep(0.1)
        if pl.stop:
            return
        # 3. climb, sit, hard drop
        for i in range(21):
            if pl.stop:
                return
            await pl.ma(pl.ma_top * i / 20.0)
            await pl.w(base)
            await asyncio.sleep(0.3)
        await pl.hold(3.0, base, tick=0.1)
        await pl.ma(0)
        print(f"      [3] HARD DROP to 0  ({time.time()-t0:.0f}s)", flush=True)
        # 4. beats on the landing
        for _ in range(2):
            await pl.hold(0.6, min(pl.hardcap, peak * 0.98), tick=0.05)
            await pl.hold(0.6, base * 0.55, tick=0.05)
        # 5. cooldown hum
        print(f"      [5] COOLDOWN hum  ({time.time()-t0:.0f}s)", flush=True)
        await pl.hold(5.0, base * 0.72, tick=0.1)
        # 6. excursion above the window
        over = min(pl.hardcap, 56.0)
        print(f"      [6] ABOVE THE WINDOW {over:.0f}%  ({time.time()-t0:.0f}s)", flush=True)
        await pl.hold(2.0, over, tick=0.06)
        await pl.hold(2.5, base * 0.95, tick=0.1)
        # 7. one sweep to close the loop
        s = time.time()
        while time.time() - s < T and not pl.stop and not pl.expired():
            u = 0.5 - 0.5 * math.cos((time.time() - s) / T * 2 * math.pi)
            await pl.ma(pl.ma_top * u)
            await pl.w(base)
            await asyncio.sleep(0.1)


PATTERNS = {
    "tide": p_tide, "stutter": p_stutter, "trap": p_trap,
    "climb": p_climb, "dice": p_dice, "edge": p_edge,
    "verge": p_verge, "groove": p_groove, "switchback": p_switchback,
    "map": p_map, "lift": p_lift, "dread": p_dread, "pulse": p_pulse,
    "drift": p_drift, "metronome": p_metronome, "ration": p_ration,
    "creep": p_creep,
    "cooldown": p_cooldown, "buzzline": p_buzzline, "crawl": p_crawl,
    "heavy": p_heavy, "ascend": p_ascend, "swell": p_swell,
    "speed_sweep": p_speed_sweep, "speed_step": p_speed_step,
    "lie": p_lie, "dual": p_dual,
    "sweep_drop": p_sweep_drop, "sweep_hold_zero": p_sweep_hold_zero,
    "arc": p_arc,
    "high_sweep": p_high_sweep, "power_sweep": p_power_sweep,
    "pain_edge": p_pain_edge, "power_sweep_rich": p_power_sweep_rich,
    "signature": p_signature,
}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern")
    ap.add_argument("--base", type=float, default=18.0)
    ap.add_argument("--peak", type=float, default=26.0)
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--hardcap", type=float, default=100.0)
    ap.add_argument("--ma-top", type=float, default=2500.0,
                    help="apex of the speed axis (MA value, 2500 = '25')")
    ap.add_argument("--sweep-period", type=float, default=8.0,
                    help="seconds per full speed sweep")
    a = ap.parse_args()
    if a.pattern not in PATTERNS:
        print("patterns:", ", ".join(PATTERNS))
        return 2

    dev = await find()
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.7)
        await k.send(READ_ALL)
        await asyncio.sleep(1.0)
        log(f"state before: {json.dumps(k.last, ensure_ascii=False)}")

        pl = Player(k, a.hardcap)
        pl.ma_top = a.ma_top
        pl.sweep_period = a.sweep_period
        live = await pl.detect_channels()
        log(f"live channels: {[c + 1 for c in live]}")
        pl.deadline = time.time() + a.secs

        def sigint(*_):
            pl.stop = True
            log("!! ABORT — zeroing")
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop = asyncio.get_running_loop()
                loop.add_signal_handler(sig, sigint)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, sigint)

        log(f"PLAY {a.pattern} base={a.base}% peak={a.peak}% secs={a.secs}")
        try:
            await PATTERNS[a.pattern](pl, a.base, a.peak, a.secs)
        finally:
            await pl.w(0)
            await asyncio.sleep(0.4)
            await k.send({"PW": "0"})
            await asyncio.sleep(0.8)
            await k.send(READ_ALL)
            await asyncio.sleep(1.0)
            log("PW=0 (ended)")
            log("last:", json.dumps(k.last, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))