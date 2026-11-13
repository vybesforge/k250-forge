#!/usr/bin/env python3
"""hr_modes.py — HR-driven K250 game modes (raw PW/MA frames through the held controller).

Each mode reads the live HR trace that `hr_drive.py --feed-only --trace FILE` writes, and
drives the box with RAW frames over the held-link controller's FIFO (so PW and MA are in
box units directly, no pattern scaling). The controller must be running (k250_ctl.py); the
ceiling is read from the wearer's limits.json, never hard-coded.

Safety, baked into every mode:
  - PW clamped 0..(power.max_percent from limits.json) — the wearer's own ceiling
  - MA clamped 1..99 (v2: 1 = slow ~0.25 beat/s, 99 = fast ~4 beat/s — HIGHER = FASTER)
  - a stale HR trace (>STALE s) holds LOW and never escalates on silence
  - the live channel (ch1) is forced before the first power write — the stop tool walks
    AC 0..3 and can leave the box on a dead channel, so power lands nowhere otherwise
  - STOP = rest at PW 1% (link held, no drop), not PW 0 and not a disconnect
  - SIGINT/SIGTERM/exit -> rest before leaving

Environment:
  K250_HR_TRACE   path to the HR JSONL trace (default ~/.cache/k250/hr-live.jsonl)
  K250_RUNTIME    overrides the runtime dir that holds ctl.fifo (see k250_codec.runtime_dir)

Usage:
  ./venv/bin/python hr_modes.py mirror   --secs 40
  ./venv/bin/python hr_modes.py punish   --secs 40 [--lo-pct -8 --hi-pct 18]
  ./venv/bin/python hr_modes.py earned   --secs 40 [--thr-pct 12 --hold 8]
  ./venv/bin/python hr_modes.py gate     --secs 40 [--limit-pct 22 --settle-pct 8]
  ./venv/bin/python hr_modes.py cooldown --secs 40
  ./venv/bin/python hr_modes.py mixed    --secs 40
  ./venv/bin/python hr_modes.py reverse  --secs 40
  ./venv/bin/python hr_modes.py tour     --secs 30   # all modes back to back
"""
import json, os, signal, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from k250_codec import runtime_dir  # noqa: E402


def _ceiling_from_limits():
    """The wearer's power ceiling from limits.json. Never hard-code 85 — that is one
    person's tuning, not the code's. Falls back to the file default if unreadable."""
    for name in ("limits.json",):
        path = os.path.join(HERE, name)
        try:
            with open(path) as f:
                d = json.load(f)
            return float(d["power"]["max_percent"])
        except Exception:
            continue
    return 5.0        # the shipped file default, NOT a personal value


FIFO = os.path.join(runtime_dir(), "ctl.fifo")
TRACE = os.path.expanduser(
    os.environ.get("K250_HR_TRACE", "~/.cache/k250/hr-live.jsonl"))
CEILING = _ceiling_from_limits()
PW_FLOOR = 8.0          # never send fiddly 1-2% noise; below this reads as off
REST_PW = 1.0           # the "stopped" resting level: barely on, link held, no drop
MA_MIN, MA_MAX = 1, 99
TICK = 0.3
STALE = 6.0

_stop = {"flag": False}


def log(msg):
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _fifo(line):
    fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def send(pw=None, ma=None, ac=None):
    """Write raw box-unit frames. PW in percent (0..ceiling), MA in box units (1..99),
    ac = 1-based channel number (converted to the box's 0-indexed AC)."""
    if ac is not None:
        _fifo('{"AC":"%d"}' % int(max(0, min(3, ac - 1))))
    if pw is not None:
        p = int(max(0, min(CEILING, round(pw))))
        _fifo('{"PW":"%d"}' % p)
    if ma is not None:
        m = int(max(MA_MIN, min(MA_MAX, round(ma))))
        _fifo('{"MA":"%d"}' % m)


def rest():
    """Bring the box to rest (PW=1%, slowest) WITHOUT dropping the link, on ch1."""
    send(ac=1)
    send(pw=REST_PW, ma=MA_MIN)


def hr_now():
    """Newest (hr, age_s) from the trace, or (None, None)."""
    try:
        with open(TRACE) as f:
            line = f.readlines()[-1]
        rec = json.loads(line)
        hr = float(rec["hr"])
        age = time.time() - float(rec.get("t", 0))
        return hr, age
    except Exception:
        return None, None


# --- v2 MA mapping: beats/sec <-> box MA (1 -> 0.25/s, 99 -> 4/s) -------------
def beats_to_ma(bps):
    return max(MA_MIN, min(MA_MAX, 1 + (bps - 0.25) / (3.75 / 98.0)))


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def pos_of(hr, lo, hi):
    return clamp((hr - lo) / max(1.0, (hi - lo)), 0.0, 1.0)


def calibrate(secs=6.0):
    """Sample the live HR for `secs`, return a stable baseline (median of samples).
    Everything downstream is expressed as a % of this, so it adapts to today's body."""
    samples = []
    t0 = time.time()
    while time.time() - t0 < secs and not _stop["flag"]:
        hr, age = hr_now()
        if hr is not None and age is not None and age <= STALE:
            samples.append(hr)
        time.sleep(0.4)
    if not samples:
        return None
    samples.sort()
    return samples[len(samples) // 2]      # median, robust to a stray spike


def band_of(base, lo_pct, hi_pct):
    """Turn a baseline HR + percentage offsets into an absolute (lo, hi) band."""
    return base * (1 + lo_pct / 100.0), base * (1 + hi_pct / 100.0)


# ============================ MODES ==================================
# Each mode keeps a dict of state across ticks and returns (pw, ma).
# Modes that use thresholds read st["base"] (set by calibrate()) and derive their
# bands as percentages of it, so they fit whatever HR the wearer is at right now.

def m_mirror(st, hr, base=None):
    """One stroke per heartbeat. Power rides gently with HR too."""
    bps = hr / 60.0
    ma = beats_to_ma(bps)
    lo, hi = band_of(base or hr, -10, 25)
    pw = 22 + 30 * pos_of(hr, lo, hi)
    st["ma"] = ma
    return pw, ma


def m_punish(st, hr, base=None, lo_pct=-8, hi_pct=18):
    """Hold a band AROUND your baseline to be rewarded. Drop well below baseline or
    spike past +hi%, and it punishes. Band straddles the baseline so rest sits inside."""
    lo, hi = band_of(base or hr, lo_pct, hi_pct)
    lvl = st.get("lvl", 30.0)
    if lo <= hr <= hi:
        lvl += 1.2            # in the sweet spot: reward, creep up slowly
        st["hit"] = st.get("hit", 0) + 1
    else:
        lvl -= 12.0           # out of band: punish, sharp
    st["lvl"] = clamp(lvl, PW_FLOOR, CEILING)
    return st["lvl"], 40


def m_earned(st, hr, base=None, thr_pct=12, hold=8.0, step=6.0, back=14.0):
    """Stay calm under baseline+thr% for `hold` seconds to unlock more. Spike resets+ drops."""
    thr = (base or hr) * (1 + thr_pct / 100.0)
    lvl = st.get("lvl", 25.0)
    calm = st.get("calm", 0.0)
    if hr < thr:
        calm += TICK
        if calm >= hold:
            lvl += step            # earned the next level
            calm = 0.0
            st["last_unlock"] = time.time()
    else:
        calm = 0.0
        lvl -= back                # spike: lose ground
    st["calm"] = calm
    st["lvl"] = clamp(lvl, PW_FLOOR, CEILING)
    return st["lvl"], 35


def m_gate(st, hr, base=None, limit_pct=22, settle_pct=8):
    """Power creeps toward the ceiling; cross baseline+limit_pct -> slam to ZERO and
    latch denied until you settle under baseline+settle_pct."""
    limit = (base or hr) * (1 + limit_pct / 100.0)
    settle = (base or hr) * (1 + settle_pct / 100.0)
    if st.get("denied"):
        if hr < settle:
            st["denied"] = False
            st["lvl"] = 20.0       # resume from low, climb again
            return st["lvl"], 30
        return 0.0, MA_MIN         # still denied: dead, slow
    lvl = st.get("lvl", 25.0) + 0.9
    if hr >= limit:
        st["denied"] = True
        log(f"  !! GATE CLOSED @ HR {hr:.0f} (limit {limit:.0f}) — slammed to zero")
        return 0.0, MA_MIN
    st["lvl"] = clamp(lvl, PW_FLOOR, CEILING)
    return st["lvl"], 30


def m_cooldown(st, hr, base=None):
    """Aftercare: the calmer you get (lower HR), the softer AND slower — taper to nothing."""
    lo, hi = band_of(base or hr, -20, 25)
    p = pos_of(hr, lo, hi)
    pw = PW_FLOOR + (CEILING - PW_FLOOR) * p
    ma = beats_to_ma(0.5 + 2.5 * p)   # slow when calm, faster only if you rise
    return pw, ma


def m_mixed(st, hr, base=None):
    """Quiet but frantic: power DROPS as HR climbs, speed RISES. Nasty edge."""
    lo, hi = band_of(base or hr, -10, 30)
    p = pos_of(hr, lo, hi)
    pw = CEILING - (CEILING - 28) * p      # ceiling calm -> 28 worked
    ma = 20 + (90 - 20) * p                # slow calm -> fast worked
    return pw, ma


def m_reverse(st, hr, base=None):
    """REVERSE — both ease as HR climbs: high HR = power DOWN and strokes SLOW.
    The pure inverted edging map (calm = strong and fast, worked = weak and slow)."""
    lo, hi = band_of(base or hr, -10, 28)
    p = pos_of(hr, lo, hi)
    pw = CEILING - (CEILING - 25) * p      # ceiling calm -> 25 worked
    ma = 90 - (90 - 15) * p                # fast (90) calm -> slow (15) worked
    return pw, ma


MODES = {
    "mirror":   m_mirror,
    "punish":   m_punish,
    "earned":   m_earned,
    "gate":     m_gate,
    "cooldown": m_cooldown,
    "mixed":    m_mixed,
    "reverse":  m_reverse,
}

BLURB = {
    "mirror":   "MIRROR — one stroke per heartbeat; your pulse IS the beat",
    "punish":   "PUNISH — hold a band around your baseline (±%); drift out and it drops",
    "earned":   "EARNED — stay calm (under baseline+12%) 8s to unlock more; spike and lose it",
    "gate":     "GATE — power creeps up; cross baseline+22% and it SLAMS to zero until you settle",
    "cooldown": "COOLDOWN — calmer = softer & slower, tapering to nothing",
    "mixed":    "MIXED — power DROPS as HR climbs, but speed RISES (quiet, frantic)",
    "reverse":  "REVERSE — power DOWN and strokes SLOW as HR climbs (pure inverted edging)",
}


def run_mode(name, secs, calib=True, **kw):
    fn = MODES[name]
    log(f"=== {BLURB[name]} ===")
    st = {}
    base = None
    if calib:
        log("  calibrating: reading your HR for ~6s to set the baseline ...")
        base = calibrate(6.0)
        if base is None:
            log("  !! no fresh HR to calibrate — aborting (box left at rest)")
            return
        log(f"  baseline HR = {base:.0f} bpm — bands are % of this")
    st["base"] = base
    t0 = time.time()
    # FORCE the live channel before anything else. The stop tool walks AC 0..3 and
    # leaves the box selected on a dead channel; power written without pinning AC the
    # lands nowhere -> "I feel nothing though it logs power". ch1 (AC 0) is where pads go.
    send(ac=1)
    time.sleep(0.4)
    send(pw=PW_FLOOR, ma=40)
    last_print = 0
    while time.time() - t0 < secs and not _stop["flag"]:
        hr, age = hr_now()
        if hr is None or (age is not None and age > STALE):
            send(pw=PW_FLOOR, ma=MA_MIN)          # stale: hold LOW, never escalate
            time.sleep(TICK)
            continue
        pw, ma = fn(st, hr, base=base, **kw)
        send(pw=pw, ma=ma)
        if time.time() - last_print >= 4:
            last_print = time.time()
            extra = ""
            if name == "earned":
                extra = f" calm={st.get('calm',0):.0f}/{float(kw.get('hold', 8.0)):.0f}"
            if name == "gate":
                extra = " DENIED" if st.get("denied") else ""
            log(f"  t={time.time()-t0:4.0f}s HR={hr:.0f} -> PW={int(pw)} MA={int(ma)}{extra}")
        time.sleep(TICK)
    rest()
    log(f"=== {name} done — rested at {int(REST_PW)}% (link still held) ===")


def _sig(*_):
    _stop["flag"] = True


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    name = args[0]
    secs = 40.0
    kw = {}
    # simple --key val parsing (percentage offsets, plus --hold for earned)
    i = 1
    while i < len(args):
        a = args[i]
        if a == "--secs" and i + 1 < len(args):
            secs = float(args[i + 1]); i += 2; continue
        if a == "--lo-pct" and i + 1 < len(args):
            kw["lo_pct"] = float(args[i + 1]); i += 2; continue
        if a == "--hi-pct" and i + 1 < len(args):
            kw["hi_pct"] = float(args[i + 1]); i += 2; continue
        if a == "--thr-pct" and i + 1 < len(args):
            kw["thr_pct"] = float(args[i + 1]); i += 2; continue
        if a == "--limit-pct" and i + 1 < len(args):
            kw["limit_pct"] = float(args[i + 1]); i += 2; continue
        if a == "--settle-pct" and i + 1 < len(args):
            kw["settle_pct"] = float(args[i + 1]); i += 2; continue
        if a == "--hold" and i + 1 < len(args):
            kw["hold"] = float(args[i + 1]); i += 2; continue
        i += 1

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)
    try:
        if name == "tour":
            order = ["mirror", "punish", "earned", "gate", "cooldown", "mixed", "reverse"]
            for m in order:
                if _stop["flag"]:
                    break
                run_mode(m, secs)
                time.sleep(1.5)
            rest()
            log("=== TOUR complete — rested at 1% (link held) ===")
        elif name in MODES:
            run_mode(name, secs, **kw)
        else:
            print(f"unknown mode {name!r}. one of: {', '.join(MODES)} | tour")
            return 2
    finally:
        try:
            rest()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
