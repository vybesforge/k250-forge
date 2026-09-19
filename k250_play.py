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
import os
import subprocess
import math
import random
import signal
import sys
import time

from k250_codec import READ_ALL

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
        self.max_rate = 0.0           # max POWER change, percent per second (0 = unlimited)
        self._last_p = None
        self._last_t = None
        self.channel_window = 0.4     # seconds to hold one channel before rotating
        self._last_rotate = 0.0
        self.channel_caps = {}        # {"1": {"power":50,"speed":2500,"sway":25}, ...}
        # Set by --override-ceiling: the wearer's Manual level wins over the AI
        # limits for the POWER of the channel it drives. Frequency and slew are
        # not lifted (they are not levels), and non-override runs still clamp.
        self.override_power = False
        self._ch_last = {}            # per-channel slew state
        self._pw_sent = {}            # per-channel (value, timestamp) of the last PW write
        # Seconds before an unchanged power value is re-sent anyway as a safety
        # net. Keeps the box's LCD quiet without ever leaving it silently stale.
        # 0 = write power on every tick (the old behaviour).
        self.pw_refresh = float(os.environ.get("K250_PW_REFRESH", "2.0"))

    def cap_for(self, ch: int, kind: str, fallback):
        """Per-channel limit, else the global one. `ch` is 0-based; limits.json
        keys channels 1-4. Different channels are on different skin -- a cap that
        is right for one placement is wrong for another.

        One exception, and only one: with `override_power` set (the wearer asked
        for `--override-ceiling`), a POWER cap does not apply -- the Manual level
        wins for the channel it is driving. Frequency and slew caps still do."""
        if kind == "power" and getattr(self, "override_power", False):
            return fallback
        c = (self.channel_caps or {}).get(str(ch + 1)) or {}
        v = c.get(kind)
        return fallback if v is None else v

    def _slew_ch(self, ch: int, p: float) -> float:
        rate = self.cap_for(ch, "slew", self.max_rate) or 0.0
        if rate <= 0:
            return p
        now = time.time()
        prev = self._ch_last.get(ch)
        if prev is None:
            self._ch_last[ch] = (p, now)
            return p
        lp, lt = prev
        dt = max(now - lt, 1e-6)
        allowed = rate * dt
        if p > lp + allowed:
            p = lp + allowed
        elif p < lp - allowed:
            p = lp - allowed
        self._ch_last[ch] = (p, now)
        return p

    async def set_pattern(self, pa_list):
        """Write `PA` — and invalidate everything the change makes untrue.

        A PATTERN CHANGE ZEROES that channel's power AND frequency. So after one:

        * drop the frequency cache (`_ma`), or `ma()` will skip re-sending and
          leave the box sitting at 0 while the driver believes it is set;
        * clear the per-channel slew accumulators, or the limiter thinks power is
          still up at the old level and refuses to climb from zero.

        Power itself recovers because this method drops the power-write cache, so
        the next `w()` always sends it — the zeroing case is handled explicitly
        rather than by re-sending power on every tick. Used by `prepare_channels()`
        and by anything that changes the wave pattern mid-run."""
        await self.k.send({"PA": pa_list})
        self._ma = None
        # The box is now at ZERO on every channel, so seed the slew state with
        # zero rather than clearing it -- clearing lets the next write pass
        # through unclamped, which would jump straight to the target and defeat
        # the slew limit entirely.
        self._ch_last = {ch: (0.0, time.time()) for ch in self.channels}
        self._pw_sent = {}   # the box is at zero power now: force the next PW write
        await asyncio.sleep(0.2)

    def _slew(self, p: float) -> float:
        """Limit how fast POWER may move, in percent per second.

        Distinct from the ceiling: a slow ceiling reached in one jump still
        hurts. Only power is rate-limited -- frequency (MA) jumps are the
        point of several patterns and must stay instant."""
        if self.max_rate <= 0:
            return p
        now = time.time()
        if self._last_p is None:
            self._last_p, self._last_t = p, now
            return p
        dt = max(now - self._last_t, 1e-6)
        allowed = self.max_rate * dt
        if p > self._last_p + allowed:
            p = self._last_p + allowed
        elif p < self._last_p - allowed:
            p = self._last_p - allowed
        self._last_p, self._last_t = p, now
        return p

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
        """MULTI-CHANNEL DRIVING IS PULLED (2026-09-18). ALWAYS drive ch1 only.

        A two-channel drive went wrong: channel 2 received far more than intended
        while channel 1 was the tuned level — the waveform's base/peak frame was
        channel 1's, but channel 2 sat on its own cap. Until the two-channel
        behaviour is genuinely understood and re-derived, the engine MUST NOT
        power a second channel. This override wins over CA regardless."""
        self.channels = [0]
        return self.channels

    async def prepare_channels(self, pattern: str = "Manual"):
        """Make every live channel drivable, and OURS, before we drive it.

        Two verified reasons:

        1. A channel whose pattern slot is blank REJECTS power outright -- the box
           echoes `{"PW": 0}` no matter what you send. Channel 2 was exactly this:
           CA said Active, PA said "      ", and every power write came back zero.
        2. Operator note (2026-09-15): set every channel to **Manual** before driving it.
           In a patterned mode the box is running its own generator and we're
           writing power into that; in Manual there's no competing waveform, so
           what we write is what happens. We own the pattern, not the box.

        Returns the list of live channel indices."""
        pa = await self.read_state("PA")
        # MULTI-CHANNEL PULLED (2026-09-18): ALWAYS ch1. detect_channels() is the
        # single source of truth; do not let the CA re-read below re-enable ch2.
        self.channels = await self.detect_channels()
        if not isinstance(pa, list):
            return self.channels
        fixed = list(pa)
        changed = []
        for i in self.channels:
            if str(fixed[i]).strip() != pattern:
                changed.append(i + 1)
                fixed[i] = pattern
        if changed:
            await self.set_pattern(fixed)
            print(f"   set channels {changed} to '{pattern}' "
                  f"(blank = refuses power; patterned = the box's own generator "
                  f"runs on top of ours)", flush=True)
        return self.channels

    async def select(self, ch: int):
        if getattr(self, "_ch", None) == ch:
            return
        self._ch = ch
        await self.k.send({"AC": str(ch)})
        await asyncio.sleep(0.04)

    async def w(self, p: float):
        """Write power. Skips the write when the box already holds this value.

        The box HOLDS `PW` once set: no re-sending on a timer, and an `MA` write
        doesn't clear it. What DOES zero it is a **pattern change (`PA`)** — and
        `set_pattern()` drops this cache to force the next write, so that case is
        covered explicitly rather than by brute force.

        Sending it unconditionally was insurance against a "dropped write" that
        never happened (retracted) — and it cost a redundant frame on every tick,
        which is what makes the box's own LCD churn while a pattern runs. A
        keepalive (`pw_refresh`, default 2 s) still re-sends an unchanged value, so
        if anything ever does zero the power silently, the pattern self-heals
        instead of running silent. `K250_PW_REFRESH=0` restores write-every-tick.

        Multi-channel: PW applies to the SELECTED channel, so when more than
        one channel is live we select-and-write each in turn. Every pattern
        therefore drives all plugged channels without knowing about them."""
        p = max(0.0, min(p, self.hardcap))

        # Pick the channel we're writing to.
        # MULTI-CHANNEL PULLED (2026-09-18): the engine drives ch1 ONLY. The
        # rotation branch that walked channels 2-4 is gone entirely — nothing may
        # select-and-write a second channel, whatever CA reports.
        ch = 0
        await self.select(ch)

        # Clamp and rate-limit for THIS channel. Different channels can sit on
        # very different skin, so a single global ceiling is the wrong shape.
        p = max(0.0, min(p, self.cap_for(ch, "power", self.hardcap)))
        p = self._slew_ch(ch, p)

        # Skip the write if the box already holds this exact value on this channel.
        # The box PERSISTS PW (verified on the box): it does not need re-sending, and
        # those redundant frames are what make its own LCD churn while driving.
        # This halves the frame rate on flat-power patterns.
        #
        # A keepalive still goes out every `pw_refresh` seconds, because PW is
        # never read back: if something we don't know about ever zeroes it, the
        # pattern self-heals within the keepalive rather than running silent.
        # K250_PW_REFRESH=0 restores the old write-every-tick behaviour.
        v = pct(p)
        now = time.time()
        last = self._pw_sent.get(ch)
        if last is not None and last[0] == v and (now - last[1]) < self.pw_refresh:
            return
        self._pw_sent[ch] = (v, now)
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

    def ma_cap(self) -> float:
        """Highest frequency allowed across the live channels (MA value)."""
        if not self.channels:
            return self.ma_top
        return min(self.cap_for(ch, "frequency", self.ma_top) for ch in self.channels)

    async def ma(self, value: float):
        """Set FREQUENCY (the box calls it Multi Adjust; the app calls it 'speed',
        which is misleading -- it changes character, not how fast anything moves).

        MA is a beat period: 0 = fastest buzz, 5000 ~= 2 beats/s, 10000 = 1 beat/s
        (period ~= MA/10000 seconds).

        The box holds MA and PW independently — writing MA does not clear PW.
        Only a PATTERN CHANGE (PA) zeroes them."""
        v = str(int(max(0, min(value, self.ma_cap()))))
        if getattr(self, "_ma", None) == v:
            return
        self._ma = v
        await self.k.send({"MA": v})


# ---------------------------------------------------------- research patterns
# Translated from the open-source Howl / Daimonia wave vocabulary
# (Penetration, BJ/engulf, Milker, Lick, and the "Jelly"/"Fangs" wave shapes).
# On the K250 the second axis (MA = beat period) is what carries the character:
# a slow thump (MA~5500) presses, MA=0 buzzes, and sliding between them IS the
# gesture. Power is the shape; MA is what the shape is made of.

async def _ma_glide(pl: Player, a: float, b: float, secs: float, tick: float = 0.05):
    """Smoothly move MA from a to b over `secs` seconds. The box holds MA, so
    this is a gentle interpolation -- the frequency axis glides instead of
    stepping, which is the Daimonia signature (power and frequency move in
    counterpoint, not as discrete jumps)."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop and not pl.expired():
        u = (time.time() - t0) / secs
        u = u * u * (3 - 2 * u)          # smoothstep: ease in and out
        await pl.ma(a + (b - a) * u)
        await asyncio.sleep(tick)


async def p_thrust(pl: Player, base: float, peak: float, secs: float):
    """Fuck: the slow deep penetration stroke. Power ramps in like the thrust,
    holds deep, eases back out. MA glides in counterpoint -- buzzy at entry,
    sliding to slow and heavy at full depth, back to buzzy on the withdraw. The
    character changes with the depth, not just the power."""
    deep = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await _ma_glide(pl, 0, 5500, 0.6)          # entry: buzzy -> slow heavy as it goes in
        await pl.hold(0.3, base * 0.5, tick=0.05)
        await pl.hold(0.3, deep * 0.75, tick=0.05)
        await pl.hold(0.9, deep, tick=0.05)          # deep, slow, heavy
        await _ma_glide(pl, 5500, 0, 0.6)           # withdraw: slow heavy -> buzzy
        await pl.hold(0.3, deep * 0.7, tick=0.05)
        await pl.hold(0.4, base * 0.35, tick=0.05)




async def p_engulf(pl: Player, base: float, peak: float, secs: float):
    """Suck: the mouth works you. Short buzzy licks at MA=0, then it takes you
    deep -- MA glides to slow and heavy as the power climbs -- holds, releases
    back to buzzy, and starts again."""
    deep = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.ma(0)
        for _ in range(3):
            await pl.hold(random.uniform(0.15, 0.25), peak * 0.9, tick=0.05)
            await pl.hold(random.uniform(0.12, 0.22), base * 0.4, tick=0.05)
        await _ma_glide(pl, 0, 6000, 0.8)           # engulf: buzzy -> slow heavy
        await pl.hold(0.5, peak * 0.95, tick=0.05)
        await pl.hold(1.0, deep, tick=0.05)          # deep, slow, held
        await _ma_glide(pl, 6000, 0, 0.6)           # release: slow heavy -> buzzy
        await pl.hold(0.6, base * 0.5, tick=0.05)




async def p_sound(pl: Player, base: float, peak: float, secs: float):
    """Sounding rod: slow, deliberate, deep. MA stays low and buzzy the whole
    time while power creeps up over many seconds into a long hold at depth,
    then withdraws just as slowly. The patience is the sensation."""
    await pl.ma(1200)
    deep = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.25, 0.5, 0.75, 1.0):
            await pl.hold(1.4, base + (deep - base) * frac, tick=0.1)
        await pl.hold(2.5, deep, tick=0.1)
        for frac in (0.75, 0.5, 0.25):
            await pl.hold(1.4, base + (deep - base) * frac, tick=0.1)
        await pl.hold(0.8, base * 0.3, tick=0.1)




async def p_milker(pl: Player, base: float, peak: float, secs: float):
    """Milker: a slow deep pull, then a fast buzz. MA glides to slow and heavy
    for the womp, then drops to 0 for the buzz -- the womp-then-buzz milking
    rhythm, the two axes in full contrast."""
    deep = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await _ma_glide(pl, 0, 5000, 1.2)          # womp: buzzy -> slow heavy
        await pl.hold(1.8, deep, tick=0.05)
        await pl.hold(0.8, deep * 0.5, tick=0.05)
        await _ma_glide(pl, 5000, 0, 0.4)          # buzz: slow heavy -> buzzy
        await pl.hold(0.9, peak * 0.85, tick=0.05)
        await pl.hold(0.4, base * 0.4, tick=0.05)




async def p_flick(pl: Player, base: float, peak: float, secs: float):
    """Lick: quick tongue-flicks. Short buzzy taps (MA=0) with tiny gaps, then
    one long slow sweep where MA glides up and back -- the tongue pressing
    harder, then easing."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.ma(0)
        for _ in range(random.randint(2, 4)):
            await pl.hold(random.uniform(0.12, 0.2), peak * 0.85, tick=0.04)
            await pl.hold(random.uniform(0.1, 0.2), base * 0.35, tick=0.04)
        await _ma_glide(pl, 0, 1500, 0.5)          # sweep in
        await pl.hold(random.uniform(0.7, 1.1), base + (peak - base) * 0.7, tick=0.05)
        await _ma_glide(pl, 1500, 0, 0.4)          # sweep out
        await pl.hold(0.4, base * 0.4, tick=0.05)




async def p_jelly(pl: Player, base: float, peak: float, secs: float):
    """Jelly: a wobbling pulse. Power oscillates fast between a low and a high
    on a soft sine while MA wobbles in counterpoint -- when power is high MA
    drops buzzy, when power is low MA rises. Reads as a shake, like something
    soft moving inside."""
    lo, hi = base, peak * 0.9
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        ph = (time.time() - t0) / 1.6 * 2 * math.pi
        await pl.w(lo + (hi - lo) * (0.5 - 0.5 * math.cos(ph)))
        # counterpoint: power high -> MA low (buzzy), power low -> MA high
        await pl.ma(6000 - 6000 * (0.5 - 0.5 * math.cos(ph)))
        await asyncio.sleep(0.06)




async def p_fangs(pl: Player, base: float, peak: float, secs: float):
    """Fangs: two quick bites per cycle -- a sharp pair of peaks with a small
    valley between, then a long low rest. MA stays low so each bite stays
    sharp and sudden."""
    await pl.ma(0)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.18, peak * 0.95, tick=0.04)
        await pl.hold(0.3, base * 0.5, tick=0.05)
        await pl.hold(0.18, min(pl.hardcap, peak), tick=0.04)
        await pl.hold(random.uniform(1.4, 2.4), base * 0.3, tick=0.08)




# ---------------------------------------------------------- DG-LAB translations
# The 16 official DG-LAB Coyote patterns, translated to the K250's two axes.
# Coyote intensity -> PW (power %); Coyote waveform freq -> MA, INVERTED
# (Coyote low Hz = thumpy = K250 high MA; Coyote high Hz = buzzy = K250 low MA).
# Power is the contract (base/peak); MA is the free axis.

async def p_heartbeat(pl: Player, base: float, peak: float, secs: float):
    """Heartbeat: lub-dub -- two quick surges then a pause, the classic. MA low
    (8000 ~= 1.25 thumps/s) so each beat is counted, not felt as buzz."""
    await pl.ma(8000)
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.18, hi, tick=0.03)          # lub
        await pl.hold(0.14, base * 0.4, tick=0.03)
        await pl.hold(0.26, hi * 0.9, tick=0.03)    # dub
        await pl.hold(random.uniform(1.0, 1.4), base * 0.25, tick=0.08)  # rest


async def p_knead(pl: Player, base: float, peak: float, secs: float):
    """Knead: fast, even 0<->high alternation, like hands kneading. Regular and
    relentless -- no gaps to brace for."""
    await pl.ma(1500)
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.2, hi, tick=0.03)
        await pl.hold(0.2, base * 0.5, tick=0.03)


async def p_crescendo(pl: Player, base: float, peak: float, secs: float):
    """Crescendo: discrete escalating pulses -- each one higher than the last,
    with a short drop between. Builds by steps, not a smooth ramp."""
    await pl.ma(2000)
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.28, 0.52, 0.73, 0.87, 1.0):
            await pl.hold(0.5, base + (hi - base) * frac, tick=0.05)
            await pl.hold(0.3, base * 0.5, tick=0.05)
        await pl.hold(1.0, base * 0.3, tick=0.08)


async def p_compress(pl: Player, base: float, peak: float, secs: float):
    """Compress: power steady and high while MA walks DOWN -- the character sinks
    from buzzy to heavy thump under constant power. The squeeze."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await _ma_glide(pl, 2000, 9000, 4.0)       # sink to thump
        await pl.hold(3.0, hi, tick=0.08)
        await _ma_glide(pl, 9000, 2000, 2.0)       # back up


async def p_friction(pl: Player, base: float, peak: float, secs: float):
    """Friction: rapid granular pulses while MA climbs -- a rasping, dragging
    feel, like something rough moving over you."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for i in range(8):
            await pl.ma(800 + i * 900)
            await pl.hold(random.uniform(0.12, 0.3), hi, tick=0.03)
            await pl.hold(random.uniform(0.1, 0.25), base * 0.4, tick=0.03)


async def p_bounce(pl: Player, base: float, peak: float, secs: float):
    """Bounce: power bounces low->mid->high->low while MA rises, so each bounce
    lands at a different character than the last."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.0, 0.33, 0.66, 1.0, 0.0):
            await pl.hold(0.6, base + (hi - base) * frac, tick=0.05)
        await _ma_glide(pl, 0, 9000, 2.4)


async def p_ripple(pl: Player, base: float, peak: float, secs: float):
    """Ripple: power ripples 0->50->100->73 repeating while MA glides up under
    it -- wave on wave, the surface moving over something deeper."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.0, 0.5, 1.0, 0.73):
            await pl.hold(0.45, base + (hi - base) * frac, tick=0.04)
        await _ma_glide(pl, 0, 9000, 1.8)


async def p_rain(pl: Player, base: float, peak: float, secs: float):
    """Rain: a burst of quick rise-fall taps, then a long steady hold -- the
    shower breaks and then just pours."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.ma(1200)
        for _ in range(random.randint(8, 12)):
            await pl.hold(0.2, base + (hi - base) * random.uniform(0.4, 1.0), tick=0.03)
            await pl.hold(0.2, base * 0.4, tick=0.03)
        await pl.ma(7000)
        await pl.hold(random.uniform(3.0, 4.5), hi, tick=0.1)


async def p_tease(pl: Player, base: float, peak: float, secs: float):
    """Tease: a throttled build that never lands -- the level creeps up, pulls
    back, then stutters at high character, then drops to nothing."""
    hi = min(pl.hardcap, peak)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
            await pl.ma(2500 - frac * 1500)
            await pl.hold(0.7, base + (hi - base) * frac, tick=0.05)
        await pl.ma(0)
        for _ in range(8):
            await pl.hold(0.2, hi, tick=0.03)
            await pl.hold(0.2, base * 0.3, tick=0.03)
        await pl.hold(0.8, 0, tick=0.05)


# ---------------------------------------------------------- Daimonia translations
# The 12 eu.daimonia.app stims, rebuilt for the K250's two axes.
# Daimonia intensity -> PW (power %); the stim's character (pulse/stutter/wave/
# continuous) -> MA + PW timing. Power is the contract (base/peak); MA is free.

async def p_tickles(pl: Player, base: float, peak: float, secs: float):
    """Tickles: very low continuous stim, the 'is it even on?' frustration base.
    Barely-there power, steady buzzy MA."""
    await pl.ma(800)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(2.0, base * 0.4, tick=0.1)


async def p_light_pulse(pl: Player, base: float, peak: float, secs: float):
    """Light pulses: low intensity pulsing, gentle frustration. Slow even on/off."""
    await pl.ma(1500)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.5, base * 0.6, tick=0.05)
        await pl.hold(0.5, base * 0.2, tick=0.05)


async def p_light_stutter(pl: Player, base: float, peak: float, secs: float):
    """Light stutter: low intensity stuttering, uneven gaps -- can't lock on."""
    await pl.ma(1000)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(random.uniform(0.15, 0.3), base * 0.6, tick=0.04)
        await pl.hold(random.uniform(0.2, 0.6), base * 0.15, tick=0.04)


async def p_teasing_pulse(pl: Player, base: float, peak: float, secs: float):
    """Teasing pulses: medium pulsing, pleasant. A rhythm you can settle into."""
    await pl.ma(2500)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.6, base + (peak - base) * 0.6, tick=0.05)
        await pl.hold(0.4, base * 0.3, tick=0.05)


async def p_teasing_stutter(pl: Player, base: float, peak: float, secs: float):
    """Teasing stutter: medium stuttering, playful -- builds then pulls back."""
    await pl.ma(1800)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for _ in range(random.randint(2, 4)):
            await pl.hold(random.uniform(0.2, 0.4), base + (peak - base) * 0.6, tick=0.04)
            await pl.hold(random.uniform(0.15, 0.3), base * 0.3, tick=0.04)
        await pl.hold(random.uniform(0.8, 1.4), base * 0.4, tick=0.08)


async def p_teasing_continuous(pl: Player, base: float, peak: float, secs: float):
    """Teasing continuous: medium continuous, a steady pleasant hum."""
    await pl.ma(2000)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(2.0, base + (peak - base) * 0.5, tick=0.1)


async def p_edging_pulse(pl: Player, base: float, peak: float, secs: float):
    """Edging pulses: high pulsing, pushes toward the edge. Stronger, faster."""
    await pl.ma(4000)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.4, peak, tick=0.04)
        await pl.hold(0.3, base * 0.4, tick=0.04)


async def p_edging_wave(pl: Player, base: float, peak: float, secs: float):
    """Edging waves: high wave stim, the swell that keeps you right at the line."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        ph = (time.time() - t0) / 6.0 * 2 * math.pi
        await pl.ma(3000 + 3000 * (0.5 - 0.5 * math.cos(ph)))
        await pl.w(base + (peak - base) * (0.5 - 0.5 * math.cos(ph)))
        await asyncio.sleep(0.08)


async def p_edging_continuous(pl: Player, base: float, peak: float, secs: float):
    """Edging continuous: high continuous, relentless -- no relief, just the line."""
    await pl.ma(5000)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(2.0, peak * 0.9, tick=0.1)


async def p_pain_stutter(pl: Player, base: float, peak: float, secs: float):
    """Pain stutter: stuttering pain -- sharp, uneven, no rhythm to brace for."""
    await pl.ma(0)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(random.uniform(0.1, 0.25), peak, tick=0.03)
        await pl.hold(random.uniform(0.15, 0.5), base * 0.2, tick=0.03)


async def p_pain_slow_ramp(pl: Player, base: float, peak: float, secs: float):
    """Pain slow ramp: ramps slowly to painful intensity, then holds -- the
    build is the point, the arrival is the punishment."""
    await pl.ma(0)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
            await pl.hold(1.2, base + (peak - base) * frac, tick=0.08)
        await pl.hold(2.0, peak, tick=0.08)
        await pl.hold(1.0, base * 0.3, tick=0.08)


async def p_pain_shocks(pl: Player, base: float, peak: float, secs: float):
    """Pain shocks: single sharp shocks with long gaps -- each one a surprise."""
    await pl.ma(0)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(0.15, peak, tick=0.03)
        await pl.hold(random.uniform(2.0, 4.0), base * 0.2, tick=0.1)


async def p_flat(pl: Player, base: float, peak: float, secs: float):
    """Flat hold: one level, MA pinned at 0, nothing to anticipate and no let-up.

    The pattern is the absence of a pattern. Everything else in this library moves
    something — power, character, or both — so the wearer always has a shape to ride
    or brace against. This gives them nothing: same level, buzziest character, for as
    long as the caller says, which is the point when the scene wants steadiness
    rather than drama."""
    await pl.ma(0)
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        if pl.expired():
            return
        await pl.hold(1.0, peak, tick=0.1)


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
    """Hover just under a level, then push over it for a beat."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.hold(random.uniform(4, 7), base)
        await pl.hold(random.uniform(0.6, 1.4), min(pl.hardcap, peak), tick=0.05)
        await pl.hold(random.uniform(3, 6), base * 0.85)


async def p_verge(pl: Player, base: float, peak: float, secs: float):
    """Edge, sharpened: hover buzzy under the line, then push OVER it in thump
    mode (MA=5000 ~= 2 beats/s) so you can count the beats you can't have."""
    t0 = time.time()
    while time.time() - t0 < secs and not pl.stop:
        await pl.ma(0)
        await pl.hold(random.uniform(4, 7), base)
        await pl.ma(5000)
        await pl.hold(random.uniform(0.9, 1.5), min(pl.hardcap, peak), tick=0.05)
        await pl.ma(0)
        await pl.hold(random.uniform(3, 6), base * 0.85)


async def p_groove(pl: Player, base: float, peak: float, secs: float):
    """Locked to the thump: MA held at 2 beats/s, power pulsing once per beat so
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
    5000 (~2 beats/s) in 20 even steps. Report which steps feel good."""
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
    """verge, with MA compensation: thump segments get +8% power because 2/s
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
    Same power the whole time — only the speed axis moves."""
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
    "thrust": p_thrust, "engulf": p_engulf, "sound": p_sound,
    "milker": p_milker, "flick": p_flick, "jelly": p_jelly,
    "fangs": p_fangs,
    "heartbeat": p_heartbeat, "knead": p_knead, "crescendo": p_crescendo,
    "compress": p_compress, "friction": p_friction, "bounce": p_bounce,
    "ripple": p_ripple, "rain": p_rain, "tease": p_tease,
    "tickles": p_tickles, "light_pulse": p_light_pulse, "light_stutter": p_light_stutter,
    "teasing_pulse": p_teasing_pulse, "teasing_stutter": p_teasing_stutter,
    "teasing_continuous": p_teasing_continuous,
    "edging_pulse": p_edging_pulse, "edging_wave": p_edging_wave,
    "edging_continuous": p_edging_continuous,
    "pain_stutter": p_pain_stutter, "pain_slow_ramp": p_pain_slow_ramp,
    "pain_shocks": p_pain_shocks,
    "flat": p_flat,
}


# ---------------------------------------------------------------------------
# Limits + session: enforced in the ENGINE, not only in the bash wrapper.
#
# bin/k250-scene reads limits.json and passes the ceiling down, which left the
# contract silently absent anywhere the wrapper can't run -- most obviously
# Windows, where there is no bash and the documented entry point is
# `python k250_play.py ...`, whose --hardcap defaulted to 100: no limit at all.
# The engine now reads the same file itself. A command-line ceiling can only
# make things STRICTER, never looser: the file is the contract.
# ---------------------------------------------------------------------------

def find_limits(explicit=None):
    here = os.path.dirname(os.path.abspath(__file__))
    for c in (explicit, os.environ.get("K250_LIMITS"),
              os.path.join(here, "limits.local.json"), os.path.join(here, "limits.json")):
        if c and os.path.isfile(c):
            return c
    return None


def load_limits(path):
    if not path:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"WARNING: could not read limits file {path} ({e}) -- "
              f"continuing with command-line limits only", file=sys.stderr)
        return None


def _cap(a, b):
    """The stricter of two optional limits. None = unspecified, 0 = unlimited."""
    vals = [x for x in (a, b) if x]
    if vals:
        return min(vals)
    return 0.0 if (a == 0 or b == 0) else None


def apply_limits(a, lim, override=False):
    """Merge the limits file into the CLI args. The file is the contract and the
    command line may only tighten it — UNLESS the override is asked for by name.

    `override=True` comes from the explicit `--override-ceiling` flag and exists
    for one reason: the person wearing the electrodes decided, out loud, to drive
    the Manual level above the agreed ceiling. It is never implicit, it never
    comes from the file, and the caller is expected to log it loudly — a ceiling
    that can be raised silently is not a ceiling. Per-channel caps still apply and
    still clamp: this raises the global wall, not the per-channel one."""
    if not lim:
        return (a.hardcap if a.hardcap is not None else 100.0), (a.max_rate or 0.0), \
               a.ma_top, a.channel_caps

    f_power = (lim.get("power") or {}).get("max_percent")
    f_freq = (lim.get("frequency") or {}).get("max")
    f_slew = (lim.get("slew") or {}).get("max_percent_per_second")

    hard = float(f_power) if f_power is not None else 100.0
    if override:
        # The Manual level wins: take --hardcap if it was given, else the level the
        # caller actually asked for. (Without this fallback the override did
        # nothing unless --hardcap happened to be passed — the CLI's --base/--peak
        # are the real level on the direct path, and --hardcap defaults to None.)
        want = a.hardcap if a.hardcap is not None else max(a.base or 0.0, a.peak or 0.0)
        if want is not None and float(want) > hard:
            hard = float(want)
    elif a.hardcap is not None:
        hard = min(float(a.hardcap), hard)

    rate = _cap(a.max_rate, f_slew)
    freq = _cap(a.ma_top, f_freq)
    if freq is None:
        freq = 2500.0

    caps = {}
    for ch, spec in ((lim.get("channels") or {}).get("per_channel") or {}).items():
        if isinstance(spec, dict):
            caps[str(ch)] = {k: v for k, v in spec.items() if v is not None}
    if a.channel_caps:
        try:
            for ch, spec in json.loads(a.channel_caps).items():
                caps.setdefault(str(ch), {}).update(
                    {k: v for k, v in (spec or {}).items() if v is not None})
        except Exception:
            pass
    caps = {k: v for k, v in caps.items() if v}     # drop channels with nothing set
    return hard, (rate or 0.0), freq, (json.dumps(caps) if caps else "")


def session_reserve(here, max_s, seconds):
    """Charge the session timer on the direct path too. Reserves the time up front
    (conservative: an interrupted run still counts). The wrapper does this itself,
    so it sets K250_WRAPPED=1 to avoid double-counting.

    The session budget no longer refuses anything: `check` rolls a spent session
    over into a new one and exits 0, so a run can follow a run with no gap, no
    cooldown and no manual reset. The reservation still happens, so the timer on
    the page shows real time."""
    script = os.path.join(here, "k250_session.py")
    if not os.path.isfile(script):
        return True
    r = subprocess.run([sys.executable, script, "check",
                        "--max", str(max(0.0, max_s - seconds)), "--dir", here],
                       capture_output=True, text=True)
    if r.returncode != 0:            # not a refusal path any more; report if it changes
        print(f"SESSION TIMER: unexpected ledger result — {r.stderr.strip() or r.returncode}",
              file=sys.stderr)
    subprocess.run([sys.executable, script, "add", "--seconds", str(seconds), "--dir", here],
                   capture_output=True, text=True)
    return True


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pattern", nargs="?",
                    help="one of the patterns; `--list` shows them all")
    ap.add_argument("--list", action="store_true",
                    help="print the available patterns and exit (no box needed)")
    ap.add_argument("--limits-show", action="store_true",
                    help="print the active ceilings from the limits file and exit (no box needed)")
    ap.add_argument("--base", type=float, default=18.0)
    ap.add_argument("--peak", type=float, default=26.0)
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--hardcap", type=float, default=None,
                    help="power ceiling. The limits file is the real ceiling; "
                         "a value here can only LOWER it, never raise it")
    ap.add_argument("--override-ceiling", action="store_true",
                    help="NOT for tool or AI-driven runs. This is the WEARER's Manual level, "
                         "set from the page's Manual section by the person in the electrodes; "
                         "it is refused unless the bridge marks the run (K250_WEARER_OVERRIDE=1) "
                         "and is always refused down the k250-scene wrapper path. "
                         "The AI limits in limits.json are changed by the user, never by a driver.")
    ap.add_argument("--limits", default=None,
                    help="path to limits.json (default: limits.local.json or "
                         "limits.json next to this script, else $K250_LIMITS)")
    ap.add_argument("--frequency", type=float, default=None,
                    help="apex of the FREQUENCY axis (Multi Adjust / MA). "
                         "0 = fastest buzz, 2500 typical, 10000 = 1 thump/s")
    ap.add_argument("--slew", type=float, default=None,
                    help="SLEW: how fast the POWER dial may move, percent per second "
                         "(0 = unlimited; low = smooth glides, high = snappy/chop)")
    ap.add_argument("--sweep-period", type=float, default=8.0,
                    help="seconds per full frequency sweep")
    ap.add_argument("--channel-caps", type=str, default="",
                    help='JSON per-channel limits, e.g. '
                         '\'{"1":{"power":50,"frequency":2500,"speed":25}}\'')
    ap.add_argument("--ma-top", type=float, default=None, help="alias of --frequency")
    ap.add_argument("--max-rate", type=float, default=None, help="alias of --speed")
    a = ap.parse_args()
    # The engine lists its own patterns. It used to be the wrapper's job, which left the documented
    # Windows path (python directly, no bash) with no way to find out what the patterns are called.
    if a.list:
        names = sorted(PATTERNS)
        print(f"{len(names)} patterns:")
        for n in names:
            print("  ", n)
        return 0
    # Same information the bash wrapper prints, so the direct path (Windows) can check the contract
    # without running anything. Read-only: no BLE, no box needed.
    if a.limits_show:
        here = os.path.dirname(os.path.abspath(__file__))
        path = find_limits(a.limits)
        lim = load_limits(path) or {}
        pow_ = lim.get("power") or {}
        freq = lim.get("frequency") or {}
        slew = lim.get("slew") or {}
        pc = {k: {kk: vv for kk, vv in (v or {}).items() if vv is not None}
              for k, v in ((lim.get("channels") or {}).get("per_channel") or {}).items()}
        pc = {k: v for k, v in pc.items() if v}
        print(f"limits file : {path or '(none found — pass --limits PATH or set K250_LIMITS)'}")
        print(f"POWER ceiling : {pow_.get('max_percent', 100)}%   (engine will not write above this)")
        print(f"POWER start   : {pow_.get('default_percent', '?')}%")
        print(f"FREQUENCY max : {freq.get('max', 2500)}   (MA / Multi Adjust -- character, not level)")
        print(f"SLEW max      : {slew.get('max_percent_per_second', 0)}%/second  "
              f"(how fast power may move; 0 = unlimited)")
        print(f"stop word     : {lim.get('stop_word', 'red')}")
        print(f"per-channel   : {json.dumps(pc) if pc else '(none -- all channels use the global limits)'}")
        sess = float((lim.get("session") or {}).get("max_duration_s", 1800) or 1800)
        script = os.path.join(here, "k250_session.py")
        if os.path.isfile(script):
            r = subprocess.run([sys.executable, script, "show", "--max", str(sess), "--dir", here],
                               capture_output=True, text=True)
            if r.stdout.strip():
                print(r.stdout.strip())
        return 0
    if not a.pattern:
        print("k250_play.py: no pattern given.", file=sys.stderr)
        print("  python k250_play.py --list        # what you can run", file=sys.stderr)
        print("  python k250_play.py <name> --base 5 --secs 30", file=sys.stderr)
        return 2
    a.ma_top = a.frequency if a.frequency is not None else (
        a.ma_top if a.ma_top is not None else 2500.0)
    a.max_rate = a.slew if a.slew is not None else (
        a.max_rate if a.max_rate is not None else 0.0)
    if a.pattern not in PATTERNS:
        names = sorted(PATTERNS)
        print(f"unknown pattern {a.pattern!r} — {len(names)} available:", file=sys.stderr)
        for n in names:
            print("  ", n, file=sys.stderr)
        return 2

    # The AI limits are the wearer's, and only the wearer changes them. The
    # override flag is how the PAGE carries the wearer's own Manual level — so it
    # is refused anywhere else: no tool, script or agent-driven run may raise the
    # agreed ceiling. Two locks, because the wrapper is the documented entry point
    # every tool uses: (1) the bridge's in-process marker must be present, and
    # (2) the wrapper path (K250_WRAPPED=1) is refused outright.
    if a.override_ceiling:
        why = None
        if os.environ.get("K250_WRAPPED") == "1":
            why = "it was asked for down k250-scene, which is a tool path"
        elif os.environ.get("K250_WEARER_OVERRIDE") != "1":
            why = "the wearer's marker is not present (K250_WEARER_OVERRIDE=1)"
        if why:
            print(f"REFUSED: --override-ceiling — {why}.\n"
                  f"  The AI limits in limits.json are the wearer's to set, from the page, and "
                  f"no tool or AI-driven run may exceed them. Raise power.max_percent (your "
                  f"settings, your file) or drive it yourself from the page's Manual section.",
                  file=sys.stderr)
            return 2

    # The limits file is the contract; this can only make it stricter — unless the
    # wearer asked for the Manual level to win, in which case it is stated loudly.
    _here = os.path.dirname(os.path.abspath(__file__))
    lim_path = find_limits(a.limits)
    lim = load_limits(lim_path)
    file_ceiling = ((lim or {}).get("power") or {}).get("max_percent")
    a.hardcap, a.max_rate, a.ma_top, a.channel_caps = apply_limits(
        a, lim, override=bool(a.override_ceiling))
    if a.override_ceiling and file_ceiling is not None \
            and float(a.hardcap) > float(file_ceiling):
        print(f"!! OVERRIDE — the Manual drive level wins for this run: "
              f"{float(file_ceiling):g}% ceiling in limits.json -> {float(a.hardcap):g}%.\n"
              f"   The wearer asked for this out loud; nothing else may raise it.",
              file=sys.stderr, flush=True)
    # In the wrapper the same figures were already printed from the same file, so
    # only announce them on the direct path (e.g. Windows).
    if not os.environ.get("K250_WRAPPED"):
        print(f"limits : {lim_path or '(none — no limits file found)'}")
        print(f"         power ceiling {a.hardcap:g}%   frequency {a.ma_top:g}   "
              f"slew {(a.max_rate or 0):g}%/s")
        if not lim_path:
            print("         pass --limits PATH or set K250_LIMITS — without a file the "
                  "command line is the only ceiling", file=sys.stderr)

    if not os.environ.get("K250_WRAPPED") and not os.environ.get("K250_IGNORE_SESSION"):
        max_s = float(((lim or {}).get("session") or {}).get("max_duration_s", 1800) or 1800)
        if not session_reserve(_here, max_s, a.secs + 5):
            return 1

    dev = await find()
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    from bleak import BleakClient          # lazy: read-only paths must not pay for it
    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.35)      # let the notify subscription go live
        await k.send(READ_ALL)
        # wait for the box's ANSWER rather than guessing at a fixed sleep
        t0 = time.time()
        while k.last is None and time.time() - t0 < 2.0:
            await asyncio.sleep(0.05)
        log(f"state before: {json.dumps(k.last or {}, ensure_ascii=False)}")

        pl = Player(k, a.hardcap)
        pl.ma_top = a.ma_top
        pl.sweep_period = a.sweep_period
        pl.max_rate = a.max_rate
        pl.override_power = bool(a.override_ceiling)
        if a.channel_caps:
            try:
                pl.channel_caps = json.loads(a.channel_caps)
                log(f"per-channel caps: {pl.channel_caps}")
            except Exception as e:
                log(f"ignoring bad --channel-caps ({e})")
        live = await pl.prepare_channels()
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