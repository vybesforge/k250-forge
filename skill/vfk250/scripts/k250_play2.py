#!/usr/bin/env python3
"""k250_play2.py — TWO-CHANNEL K250-4S pattern engine (derived 2026-11-19).

Why this exists: the original k250_play.py pins ch1 — multi-channel was pulled
on 2026-09-18 after a two-channel drive sent channel 2 far more than intended
(the waveform's base/peak frame was channel 1's while channel 2 sat on its own
cap). This driver re-derives two-channel behaviour with the incident fixed by
construction:

  * EVERY channel gets its OWN waveform frame (pattern/base/peak/hardcap).
    Channel 2 never inherits channel 1's numbers. Patterns that scale off
    `pl.hardcap` scale off the cap of the channel they are driving.
  * ONE process, ONE BLE link. AC-select + PW/MA write are ATOMIC under a
    single asyncio lock, so a value computed for one channel can never land
    on the other.
  * Per-channel ceilings (--hardcap ch1, --hardcap2 ch2) are clamped on EVERY
    write, on top of the per-channel slew limiter.
  * Ceilings come from the CALLER — the wearer's word this session, or the
    companion web page's sliders. This driver reads no limits file.
  * REFUSES when channel 2 is requested but not Active/plugged (never silently
    drives one channel when two were asked for), and REFUSES unknown firmware.
  * Stop/SIGINT/exit zeroes EVERY driven channel and echoes state.

Usage:
  k250_play2.py PATTERN --base N --peak N --secs N --hardcap N \
      [--pattern2 P2 --base2 N --peak2 N --hardcap2 N] \
      [--frequency F] [--slew S] [--sweep-period S] [--address AA:BB:..]

Without --pattern2 it behaves like a single-channel run on ch1.
"""
import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from k250_codec import READ_ALL
from k250_ble import K250, find
import k250_play as kp
from k250_play import (PATTERNS, FW_AXES, _major, ma_box_max_for, pw_scale_for,
                       pa_writable_for, log, pct)


class DualPlayer(kp.Player):
    """Player with a second ceiling and an atomic select+write per channel."""

    def __init__(self, k, hardcap: float, hardcap2: float):
        super().__init__(k, hardcap)
        self.hardcap2 = float(hardcap2)
        self._lock = asyncio.Lock()
        self._ma_sent: dict[int, tuple[str, float]] = {}

    def hardcap_for(self, ch: int) -> float:
        return self.hardcap if ch == 0 else self.hardcap2

    async def w_ch(self, ch: int, p: float):
        """Write power to ONE channel: clamp, slew, then select+PW under lock."""
        p = max(0.0, min(p, self.hardcap_for(ch)))
        p = self._slew_ch(ch, p)
        if self.stop and p > 0:
            return
        v = pct(p, self.pw_scale)
        async with self._lock:
            now = time.time()
            last = self._pw_sent.get(ch)
            if last is not None and last[0] == v and (now - last[1]) < self.pw_refresh:
                return
            await self.select(ch)
            self._pw_sent[ch] = (v, now)
            await self.k.send({"PW": v})

    async def ma_ch(self, ch: int, value: float):
        """Set frequency on ONE channel, same atomicity and keepalive as PW."""
        num = max(0.0, min(value, self.ma_top))
        if self.ma_out_max:
            num = round(num * self.ma_out_max / (self.ma_top or 1.0))
        v = str(int(num))
        async with self._lock:
            now = time.time()
            last = self._ma_sent.get(ch)
            if last is not None and last[0] == v and (now - last[1]) < self.pw_refresh:
                return
            await self.select(ch)
            self._ma_sent[ch] = (v, now)
            await self.k.send({"MA": v})

    # the original single-channel entry points must never be used here
    async def w(self, p: float):
        raise RuntimeError("DualPlayer: call w_ch(ch, p) — w() is disabled")

    async def ma(self, value: float):
        raise RuntimeError("DualPlayer: call ma_ch(ch, v) — ma() is disabled")


class ChanView:
    """The Player-shaped facade one pattern coroutine sees, bound to ONE channel.

    Exposes every attribute/method the PATTERNS touch, with per-channel values
    where per-channel is the point (hardcap) and shared values elsewhere."""

    def __init__(self, pl: DualPlayer, ch: int):
        self._pl = pl
        self._ch = ch

    # ---- per-channel ----
    @property
    def hardcap(self) -> float:
        return self._pl.hardcap_for(self._ch)

    # ---- shared, read-only ----
    @property
    def stop(self) -> bool:
        return self._pl.stop

    @property
    def ma_top(self) -> float:
        return self._pl.ma_top

    @property
    def ma_out_max(self):
        return self._pl.ma_out_max

    @property
    def pw_scale(self) -> float:
        return self._pl.pw_scale

    @property
    def pa_writable(self) -> bool:
        return self._pl.pa_writable

    @property
    def sweep_period(self) -> float:
        return self._pl.sweep_period

    @property
    def channel_caps(self) -> dict:
        return {}          # page ceilings replace per-channel limit files here

    @property
    def override_power(self) -> bool:
        return False

    @property
    def max_rate(self) -> float:
        return self._pl.max_rate

    @property
    def deadline(self):
        return self._pl.deadline

    # ---- methods ----
    def expired(self) -> bool:
        return self._pl.expired()

    def ma_cap(self) -> float:
        return self._pl.ma_top

    async def w(self, p: float):
        await self._pl.w_ch(self._ch, p)

    async def ma(self, value: float):
        await self._pl.ma_ch(self._ch, value)

    async def hold(self, secs: float, p: float, tick: float = 0.1):
        end = time.time() + secs
        while time.time() < end:
            if self._pl.stop or self.expired():
                return
            await self.w(p)
            await asyncio.sleep(tick)

    async def sleep(self, secs: float):
        await self._pl.sleep(secs)

    async def read_state(self, key: str, timeout: float = 3.0):
        return await self._pl.read_state(key, timeout)

    async def prepare_channels(self, pattern: str = "Manual"):
        return await self._pl.prepare_channels(pattern)

    async def set_pattern(self, pa_list):
        return await self._pl.set_pattern(pa_list)


def _ch_active(ca, i: int) -> bool:
    """CA is the authoritative per-channel status array (Active/Unplugged).
    NEVER use PA for plug detection — a live channel can show a blank/UNPLUG'D
    pattern slot (verified on the box 2026-11-19: CA=['Active','Active',...]
    while PA=['Rhythm',"UNPLUG'D",...])."""
    if isinstance(ca, list) and i < len(ca):
        return "active" in str(ca[i]).strip().lower()
    return False


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pattern", help="channel-1 pattern name (see k250_play.py --list)")
    ap.add_argument("--base", type=float, default=18.0)
    ap.add_argument("--peak", type=float, default=None, help="default: --base")
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--hardcap", type=float, required=True,
                    help="channel-1 ceiling (percent), from the page")
    ap.add_argument("--pattern2", default=None, help="channel-2 pattern (enables ch2)")
    ap.add_argument("--base2", type=float, default=10.0)
    ap.add_argument("--peak2", type=float, default=None, help="default: --base2")
    ap.add_argument("--hardcap2", type=float, default=30.0,
                    help="channel-2 ceiling (percent), from the page")
    ap.add_argument("--frequency", type=float, default=2000.0)
    ap.add_argument("--slew", type=float, default=0.0)
    ap.add_argument("--sweep-period", type=float, default=8.0)
    ap.add_argument("--address", default=None)
    a = ap.parse_args()

    a.peak = a.base if a.peak is None else a.peak
    a.peak2 = a.base2 if a.peak2 is None else a.peak2
    dual = a.pattern2 is not None

    # ---- validation: fail before touching BLE ----
    errs = []
    if a.pattern not in PATTERNS:
        errs.append(f"unknown pattern {a.pattern!r}")
    if dual and a.pattern2 not in PATTERNS:
        errs.append(f"unknown pattern2 {a.pattern2!r}")
    if not (0 <= a.base <= 100 and 0 <= a.peak <= 100):
        errs.append("base/peak out of 0..100")
    if not (0 < a.hardcap <= 100):
        errs.append("hardcap must be 0<x<=100")
    if not (1 <= a.secs <= 3600):
        errs.append("secs must be 1..3600")
    if dual:
        if not (0 <= a.base2 <= 100 and 0 <= a.peak2 <= 100):
            errs.append("base2/peak2 out of 0..100")
        if not (0 < a.hardcap2 <= 100):
            errs.append("hardcap2 must be 0<x<=100")
    if max(a.base, a.peak) > a.hardcap:
        errs.append("ch1 base/peak exceeds hardcap")
    if dual and max(a.base2, a.peak2) > a.hardcap2:
        errs.append("ch2 base2/peak2 exceeds hardcap2")
    if errs:
        print("REFUSED: " + "; ".join(errs), file=sys.stderr, flush=True)
        return 2

    print(f"limits  : PAGE-DRIVEN (limits.json not read on this path). "
          f"CH1 ceiling {a.hardcap:g}%" + (f"   CH2 ceiling {a.hardcap2:g}%" if dual else ""),
          flush=True)

    dev = await find(address=a.address)
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    from bleak import BleakClient
    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.35)
        await k.send(READ_ALL)
        # wait for the firmware field, re-asking — copied from k250_play.main()
        t0 = time.time()
        while not (k.last or {}).get("FV") and time.time() - t0 < 12.0:
            if time.time() - t0 > 3.0 and int((time.time() - t0) / 3.0) != int((time.time() - 0.05 - t0) / 3.0):
                try:
                    await k.send(READ_ALL)
                except Exception:
                    pass
            await asyncio.sleep(0.05)
        log(f"state before: {json.dumps(k.last or {}, ensure_ascii=False)}")

        _fv = (k.last or {}).get("FV")
        if not _fv:
            log("!! REFUSING TO DRIVE: the box never reported its firmware within 12s.")
            log("!! READ TIMEOUT, not a version problem — wake the box and retry.")
            return 1
        if _major(_fv) not in FW_AXES:
            log(f"!! REFUSING TO DRIVE: firmware {_fv} is not in the known table.")
            return 1

        pl = DualPlayer(k, a.hardcap, a.hardcap2)
        pl.ma_top = a.frequency
        pl.ma_out_max = ma_box_max_for(_fv)
        pl.pw_scale = float(pw_scale_for(_fv) or 1.0)
        pl.pa_writable = bool(pa_writable_for(_fv))
        pl.sweep_period = a.sweep_period
        pl.max_rate = a.slew
        pl.override_power = False
        log(f"axes    : firmware {_fv} — MA apex {pl.ma_out_max or 'raw'}, "
            f"PW = percent x{pl.pw_scale:g}, "
            f"PA {'writable' if pl.pa_writable else 'refused by the box'}")

        # ---- channel detection: ask for CA (authoritative), refuse rather than guess ----
        # CA is NOT part of read-all; it must be requested explicitly (same move
        # as k250_stop.py). PA slots lie about plug state.
        state = dict(k.last or {})
        ca = None
        k.last = None
        await k.send({"CA": ""})
        for _ in range(25):
            if k.last and "CA" in k.last:
                ca = k.last["CA"]
                break
            await asyncio.sleep(0.1)
        if not isinstance(ca, list):
            log("!! REFUSING TO DRIVE: channel status (CA) could not be read within 2.5s.")
            return 1
        log(f"channels: CA={ca}")
        ch1_ok = _ch_active(ca, 0)
        ch2_ok = _ch_active(ca, 1)
        if not ch1_ok:
            log("!! REFUSING TO DRIVE: channel 1 does not read Active/plugged. "
                "Seat the pads and check the cable; never raise power to diagnose silence.")
            return 1
        if dual and not ch2_ok:
            log("!! REFUSING TO DRIVE: channel 2 was requested but does not read "
                "Active/plugged. Plug ch2 pads (NEVER across heart or chest) and "
                "set ch2 to Manual by hand on the box — v2 firmware refuses "
                "pattern writes over BLE. Nothing was driven on either channel.")
            return 1
        pl.channels = [0, 1] if dual else [0]
        log(f"live channels: {[c + 1 for c in pl.channels]}")
        if not pl.pa_writable:
            log("pattern : v2 refuses PA writes — set EVERY driven channel to Manual "
                "BY HAND on the box (blank slots reject power; patterned slots run "
                "the box's generator on top of ours)")
        # documented failure mode: a live channel (CA Active) with a blank/UNPLUG'D
        # pattern slot REJECTS power writes — the box echoes PW:0. Surface it loudly
        # so a silent channel is diagnosed, never "fixed" by raising power.
        pa_list = state.get("PA")
        if isinstance(pa_list, list):
            for ch in pl.channels:
                slot = str(pa_list[ch]).strip() if ch < len(pa_list) else ""
                if slot.lower() in ("", "unplugged", "unplug'd", "blank"):
                    log(f"!! WARNING: ch{ch + 1} pattern slot reads '{slot}' — the box may "
                        f"REJECT power writes on it (PW echoes 0). Set that channel to "
                        f"Manual by hand on the box. Never raise power to diagnose silence.")

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

        log(f"PLAY {a.pattern} base={a.base}% peak={a.peak}% secs={a.secs} (ch1)")
        tasks = [PATTERNS[a.pattern](ChanView(pl, 0), a.base, a.peak, a.secs)]  # type: ignore[arg-type]
        if dual:
            log(f"PLAY {a.pattern2} base={a.base2}% peak={a.peak2}% secs={a.secs} (ch2)")
            tasks.append(PATTERNS[a.pattern2](ChanView(pl, 1), a.base2, a.peak2, a.secs))  # type: ignore[arg-type]
        try:
            await asyncio.gather(*tasks)
        finally:
            pl.stop = True
            driven = list(pl.channels)
            for ch in driven:
                await pl.w_ch(ch, 0.0)
            await asyncio.sleep(0.4)
            async with pl._lock:
                for ch in driven:
                    await pl.select(ch)
                    await k.send({"PW": "0"})
                    await asyncio.sleep(0.15)
            await asyncio.sleep(0.8)
            await k.send(READ_ALL)
            await asyncio.sleep(1.0)
            log("PW=*** (ended)")
            log("last:", json.dumps(k.last, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
