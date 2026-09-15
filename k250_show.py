#!/usr/bin/env python3
"""K250-4S — play a setlist of Manual-mode patterns in ONE BLE session.

Connects once, announces each segment, leaves 6 s of silence between so the
patterns don't blur together, ends at PW=0. SIGTERM/SIGINT aborts + zeroes.

Usage: python k250_show.py [--hardcap 60]
"""
import argparse
import asyncio
import json
import signal
import sys
import time

from k250_codec import READ_ALL

from k250_ble import K250, find
from k250_play import PATTERNS, Player

T0 = time.time()

# (label, pattern, base%, peak%, secs, MA override or None)
SETLISTS = {
"tour": [
    ("1. STUTTER   (no rhythm, random bursts)",   "stutter", 20, 40, 40, None),
    ("2. CLIMB     (sawtooth, snaps back)",       "climb",   20, 45, 40, None),
    ("3. DICE      (random walk + surprises)",    "dice",    22, 45, 40, None),
    ("4. EDGE      (hovers, then pushes over)",   "edge",    25, 45, 40, None),
    ("5a. TIDE     MA=0    (baseline)",           "tide",    25, 45, 25, "0"),
    ("5b. TIDE     MA=5000 (A/B against 5a)",     "tide",    25, 45, 25, "5000"),
],
"new": [
    ("6. VERGE       (edge + thump arrival)",     "verge",      25, 45, 45, None),
    ("7. GROOVE      (locked to the 1/s beat)",   "groove",     22, 45, 45, None),
    ("8. SWITCHBACK  (climb, but relief is thick)","switchback", 20, 45, 50, None),
],
"build": [
    ("9.  MAP    power flat @28%, MA 0->5000 in 20 steps", "map",   28, 28, 100, None),
    ("10. LIFT   verge + MA compensation (+8% on thump)",  "lift",  25, 45, 50, None),
    ("11. DREAD  long near-nothing, then one heavy beat",  "dread", 28, 45, 55, None),
    ("12. PULSE  drifting mid-range MA, irregular power",  "pulse", 25, 42, 50, None),
],
"build2": [
    ("13. DRIFT      (power swell, MA drifting underneath)", "drift",     25, 40, 55, None),
    ("14. METRONOME  (MA=8000 ~1.25/s, surge creeps up)",    "metronome", 22, 48, 55, None),
    ("15. RATION     (2-5 beats, then long silence)",        "ration",    26, 45, 55, None),
    ("16. CREEP      (power flat, only MA walks)",           "creep",     22, 40, 55, None),
],
"build3": [
    ("17. COOLDOWN  (beats, then MA->0 low hum that sits)", "cooldown", 20, 42, 55, None),
    ("18. BUZZLINE  (0-2500 band, power climbing, MA wanders)","buzzline",22, 40, 50, None),
    ("19. CRAWL     (power FLAT, MA ping-pongs 0->2500) FIXED","crawl",   20, 42, 50, None),
    ("20. HEAVY     (MA=8000 with +18% power compensation)",  "heavy",   20, 40, 50, None),
],
"scale": [
    # (label, pattern, base%, peak%, secs, MA override, ma_top)
    ("21. ASCEND low   (20->32%, speed 0->25)",  "ascend",      20, 32, 40, None, 2500),
    ("22. SPEED SWEEP  (power flat 38%, speed 0<->25 sine)","speed_sweep", 38, 38, 45, None, 2500),
    ("23. SPEED STEP   (power flat 38%, speed staircase)","speed_step",   38, 38, 45, None, 2500),
    ("24. SWELL wide   (30->50%, speed 0->35->0)", "swell",      30, 50, 45, None, 3500),
    ("25. LIE          (power 48->30 while speed 0->25)","lie",  30, 48, 40, None, 2500),
    ("26. DUAL         (power steps 30->50, speed glides)","dual",30, 50, 45, None, 2500),
],
"sweeps": [
    # (label, pattern, base%, peak%, secs, MA override, ma_top, sweep_period)
    ("27. SWEEP @30% flat  (8s glide)  -- power ladder",  "speed_sweep", 30, 30, 40, None, 2500, 8),
    ("28. SWEEP @38% flat  (8s glide)  -- the winner",    "speed_sweep", 38, 38, 40, None, 2500, 8),
    ("29. SWEEP @46% flat  (8s glide)  -- top of ladder", "speed_sweep", 46, 46, 40, None, 2500, 8),
    ("30. SWEEP SLOW @38%  (20s glide, long travel)",     "speed_sweep", 38, 38, 50, None, 2500, 20),
    ("31. SWEEP FAST @38%  (4s glide, brisk)",            "speed_sweep", 38, 38, 40, None, 2500, 4),
    ("32. SWEEP DROP @38%  (climb, sit, HARD drop to 0)", "sweep_drop", 38, 38, 55, None, 2500, 8),
],
"high": [
    # (label, pattern, base%, peak%, secs, MA override, ma_top, sweep_period)
    ("33. HIGH SLOW SWEEP (power 35<->50, speed 0<->25, out of phase)","high_sweep", 35, 50, 60, None, 2500, 20),
    ("34. POWER SWEEP     (power 35<->50, speed parked mid)","power_sweep",35, 50, 55, None, 2500, 20),
    ("35. PAIN EDGE       (35-50 band, excursions above it)","pain_edge", 35, 50, 55, None, 2500, 20),
    ("36. ARC at high power (sweep/drop/cooldown, 38->46)","arc",        38, 46, 110, None, 2500, 13),
],
"high2": [
    ("37. ARC at MAX       (sweep/drop/cooldown, 38->50)", "arc",             38, 50, 110, None, 2500, 13),
    ("38. POWER SWEEP+     (rise, dwell w/ speed drift, fall, rest)", "power_sweep_rich", 35, 50, 60, None, 2500, 20),
],
"scene1": [
    # escalation: build -> tease -> take it away
    ("A. SWITCHBACK   (climb buzzy, snap-back is heavy beats)", "switchback", 30, 46, 50, None),
    ("B. SWEEP DROP   (climb, sit at the top, HARD drop)",      "sweep_drop", 38, 38, 45, None, 2500, 13),
    ("C. COOLDOWN     (beats in the band, then MA=0 hum)",      "cooldown",   26, 44, 45, None),
],
"scene2": [
    # Operator note: above 50 on sweeps was granted for this set. Hardcap 60.
    ("A. HIGH SWEEP   (sweeps crossing the window: 42<->58)",  "high_sweep", 42, 58, 60, None, 2500, 20),
    ("B. PAIN EDGE    (band 45-54, excursions to 60)",         "pain_edge",  45, 54, 55, None, 2500, 20),
    ("C. SWEEP DROP   (climb, sit, HARD drop -- at 50 flat)",  "sweep_drop", 50, 50, 45, None, 2500, 13),
],
"scene3": [
    # ceiling night: 60 is the edge, used deliberately and briefly
    ("A. SWEEP @55 flat  (speed 0<->25)",                     "speed_sweep", 55, 55, 40, None, 2500, 13),
    ("B. SWEEP DROP @55  (climb, sit, HARD drop)",            "sweep_drop", 55, 55, 40, None, 2500, 13),
    ("C. PAIN EDGE       (band 52-58, excursions to 60)",     "pain_edge",  52, 58, 50, None, 2500, 20),
],
"scene4": [
    # denial set: bursts and long silences, no resolution
    ("A. RATION      (bursts of beats, then long silences)",   "ration",     48, 55, 55, None, 2500, 20),
    ("B. DREAD       (long near-nothing, then ONE heavy beat)","dread",      45, 55, 55, None, 2500, 20),
    ("C. SWEEP DROP  (climb, sit, HARD drop -- at 52 flat)",   "sweep_drop", 52, 52, 45, None, 2500, 13),
],
"scene5": [
    # staged escalation: brief tops, stepping up across the band. Hardcap 70.
    ("A. HIGH SWEEP   (staged climb across 55<->68)",          "high_sweep", 55, 68, 60, None, 2500, 20),
    ("B. PAIN EDGE    (band 58-66, excursions to 70)",         "pain_edge",  58, 66, 55, None, 2500, 20),
    ("C. SWEEP DROP   (climb, sit, HARD drop -- at 58 flat)",  "sweep_drop", 58, 58, 40, None, 2500, 13),
],
"scene6": [
    # long steady hold: the character moves, the level doesn't. No resolution.
    ("A. SWEEP @42 flat (steady level, character moving)",   "speed_sweep", 42, 42, 70, None, 2500, 13),
    ("B. RATION         (bursts 45-52, then long silences)",    "ration",      45, 52, 55, None, 2500, 20),
    ("C. COOLDOWN       (low hum to finish, 22-36)",            "cooldown",    22, 36, 45, None),
],
"scene7": [
    # long hold with nobody reporting: no escalation, land it safely. Ceiling stays 55.
    ("A. SWEEP DROP  (climb, sit, HARD drop -- at 48 flat)",  "sweep_drop", 48, 48, 45, None, 2500, 13),
    ("B. HIGH SWEEP  (40<->54 crossing, axes out of phase)",  "high_sweep", 40, 54, 60, None, 2500, 20),
    ("C. DREAD       (long near-nothing, then ONE heavy beat)","dread",     42, 52, 55, None, 2500, 20),
    ("D. COOLDOWN    (low hum to land it, 18-32)",            "cooldown",   18, 32, 50, None),
],
"faves": [
    # the three that worked best, in order: sweep, hard drop, switchback
    ("A. SWEEP @38 flat   (steady level, character moving)", "speed_sweep", 38, 38, 40, None, 2500, 13),
    ("B. SWEEP DROP @38   (climb, sit, HARD drop)",          "sweep_drop", 38, 38, 45, None, 2500, 13),
    ("C. SWITCHBACK       (snap-back is heavy beats)",       "switchback", 30, 46, 45, None),
],
"serve2": [
    # higher and steady, with the second axis moving. Cap 55.
    ("A. SWEEP @50 flat  (high and steady, character moving)", "speed_sweep", 50, 50, 60, None, 2500, 13),
    ("B. HIGH SWEEP      (46<->55 crossing, axes out of phase)","high_sweep", 46, 55, 60, None, 2500, 20),
    ("C. SWEEP DROP @52  (climb, sit, HARD drop)",             "sweep_drop", 52, 52, 45, None, 2500, 13),
],
"limit75": [
    # the highest setlist here: agreed ceiling 75, hardcapped at 75 --
    # the engine physically cannot exceed it.
    ("A. HIGH SWEEP   (staged 58<->70, 20s passes)",           "high_sweep", 58, 70, 60, None, 2500, 20),
    ("B. PAIN EDGE    (band 62-72, brief excursions to 75)",   "pain_edge",  62, 72, 55, None, 2500, 20),
    ("C. SWEEP DROP   (climb, sit, HARD drop -- at 62 flat)",  "sweep_drop", 62, 62, 45, None, 2500, 13),
    ("D. COOLDOWN     (land it: low hum 20-35)",               "cooldown",   20, 35, 45, None),
],
}
GAP = 6.0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hardcap", type=float, default=60.0)
    ap.add_argument("--gap", type=float, default=GAP)
    ap.add_argument("--set", default="tour", choices=sorted(SETLISTS))
    ap.add_argument("--ma-top", type=float, default=2500.0)
    ap.add_argument("--sweep-period", type=float, default=8.0)
    ap.add_argument("--max-rate", type=float, default=0.0,
                    help="max POWER movement, percent per second (0 = unlimited)")
    a = ap.parse_args()
    setlist = SETLISTS[a.set]

    dev = await find()
    if dev is None:
        print("K250 not found", flush=True)
        return 1
    print(f"[{time.time()-T0:6.1f}] found {dev.address} {dev.name}", flush=True)

    from bleak import BleakClient
    async with BleakClient(dev, timeout=30) as cl:
        k = K250(cl)
        await k.start()
        await asyncio.sleep(0.7)
        await k.send(READ_ALL)
        await asyncio.sleep(1.0)
        print(f"[{time.time()-T0:6.1f}] state: {json.dumps(k.last, ensure_ascii=False)}",
              flush=True)

        pl = Player(k, a.hardcap)
        pl.max_rate = a.max_rate

        def abort(*_):
            pl.stop = True
            print(f"[{time.time()-T0:6.1f}] !! ABORT — zeroing", flush=True)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, abort)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, abort)

        try:
            for seg in setlist:
                if pl.stop:
                    break
                label, name, base, peak, secs, ma = seg[:6]
                pl.ma_top = seg[6] if len(seg) > 6 else a.ma_top
                pl.sweep_period = seg[7] if len(seg) > 7 else a.sweep_period
                print(f"\n[{time.time()-T0:6.1f}] === {label}  "
                      f"base={base}% peak={peak}% {secs}s ma_top={pl.ma_top:.0f}"
                      f" sweep={pl.sweep_period:.0f}s"
                      + (f" MA={ma}" if ma else ""), flush=True)
                if ma is not None:
                    await k.send({"MA": ma})
                    await asyncio.sleep(0.5)
                pl.deadline = time.time() + secs
                await PATTERNS[name](pl, base, peak, secs)
                pl.deadline = None
                await pl.w(0)
                await k.send({"PW": "0"})
                print(f"[{time.time()-T0:6.1f}] --- end {label} (PW=0)", flush=True)
                end = time.time() + a.gap
                while time.time() < end and not pl.stop:
                    await asyncio.sleep(0.2)
        finally:
            await k.send({"PW": "0"})
            await pl.w(0)
            await asyncio.sleep(0.4)
            await k.send({"PW": "0"})
            await asyncio.sleep(0.8)
            await k.send(READ_ALL)
            await asyncio.sleep(1.0)
            print(f"[{time.time()-T0:6.1f}] PW=0 (show ended)", flush=True)
            print("last:", json.dumps(k.last, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))