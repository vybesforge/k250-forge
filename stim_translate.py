#!/usr/bin/env python3
"""Imported stim  ->  K250 / Coyote translation.

Reads the imported stim catalogue (`stim_library/dictionnary_estims.json`) and
turns a named stim into a time-series of (frequency, intensity), then maps it onto:

  * K250     : PW (power %) + MA (beat period). PW = level * intensity; MA driven
               from the stim's pulse rate. Goes through the engine's Player, so the
               wearer's limits.json still clamps everything.
  * Coyote V3: native 20-byte `B0` frames (see stim_library/SIGNAL-FORMAT.md) — the
               app's own language, so this is near-lossless.

Pure stdlib, no BLE. Offline-testable: `python3 stim_translate.py --demo`.
"""
import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "stim_library", "dictionnary_estims.json")

# ---- tuning constants (documented, one place to change) ---------------------
# The source `volume` is dB (observed range ~ -34..-14). Map linearly to 0..1.
DB_MIN, DB_MAX = -34.0, -14.0
# K250 MA band we like: 0 = fastest buzz, ~2500 = slow thump. Lower MA = faster.
MA_BAND_TOP = 2500.0          # apex of the mapped beat axis (also bounded by limits)
# effectFrequence is the stim's modulation rate (1..5 typical). Faster -> lower MA.
def ma_from_rate(ef: float) -> float:
    ef = max(0.5, float(ef or 1.0))
    return max(0.0, min(MA_BAND_TOP, 2000.0 / ef))


def load_catalog(path=CATALOG):
    with open(path) as f:
        return json.load(f)


def flat(x, i=0):
    if isinstance(x, list):
        return x[i] if len(x) > i else (x[0] if x else None)
    return x


def _interp(keyframes, t):
    """Linear interp of {toVal, atTime} keyframes at local time t (seconds)."""
    kf = []
    for k in keyframes or []:
        v = k.get("toVal")
        if isinstance(v, list):           # pre-baked curve sample
            v = v[0] if v else 0.0
        try:
            kf.append((float(k.get("atTime", 0)), float(v)))
        except (TypeError, ValueError):
            pass
    if not kf:
        return None
    kf.sort()
    if t <= kf[0][0]:
        return kf[0][1]
    if t >= kf[-1][0]:
        return kf[-1][1]
    for (t0, v0), (t1, v1) in zip(kf, kf[1:]):
        if t0 <= t <= t1:
            if t1 <= t0:
                return v1
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return kf[-1][1]


def stim_timeline(entry, step=0.1, max_s=300.0):
    """Named stim -> [(t_s, freq_hz, intensity_db)] sampled every `step` seconds.

    A stim with a `sequence` is a list of steps, each lasting `baseTime` seconds,
    carrying `volume` and `frequency` keyframes. A stim without one is a held
    carrier at its base `frequence`/`volume`.
    """
    ch = flat(entry.get("channels"), 0)
    base_freq = flat(entry.get("frequence"), 0) or 1000.0
    base_vol = flat(entry.get("volume"), 0)
    base_vol = -20.0 if base_vol is None else base_vol
    seq = entry.get("sequence")

    out = [(0.0, float(base_freq), float(base_vol))]
    if not seq:
        return out, float(base_vol)
    t = 0.0
    for s in seq:
        if not isinstance(s, dict):
            continue
        dur = float(s.get("baseTime", 1) or 1)
        data = s.get("data") or {}
        vols = data.get("volume") if isinstance(data, dict) else None
        freqs = data.get("frequency") if isinstance(data, dict) else None
        tt = 0.0
        while tt < dur and t < max_s:
            v = _interp(vols, tt)
            fr = _interp(freqs, tt)
            out.append((t, float(fr if fr is not None else base_freq),
                        float(v if v is not None else base_vol)))
            tt += step
            t += step
        if t >= max_s:
            break
    return out, float(base_vol)


def db_to_pct(db):
    """Source volume (dB) -> 0..1 intensity."""
    u = (float(db) - DB_MIN) / (DB_MAX - DB_MIN)
    return max(0.0, min(1.0, u))


def to_k250(timeline, level, ma_top=MA_BAND_TOP, effect=1.0):
    """-> [(t_s, pw_percent, ma)] for the K250. `level` is the wearer's drive level (%).

    PW  = level * intensity (the engine then clamps to limits.json).
    MA  = from the stim's modulation rate. Intensity travels; frequency is character.
    """
    ma = ma_from_rate(effect)
    ma = max(0.0, min(ma, ma_top))
    return [(t, round(level * db_to_pct(db), 2), ma) for (t, _fr, db) in timeline]


def to_coyote(timeline, str_a=100, str_b=100, freq_scale=0.25, int_scale=2.0):
    """-> list of 20-byte hex B0 frames, one per 100ms tick (Coyote V3 native)."""
    hexv = lambda v: f"{int(max(0, min(255, v))) & 0xFF:02X}"
    base = "B0" + "0F" + hexv(str_a) + hexv(str_b)
    frames = []
    for (t, fr, db) in timeline:
        f = int(max(0, min(255, (fr or 1000) * freq_scale)))          # carrier -> byte
        i = int(max(0, min(100, db_to_pct(db) * 100 * int_scale)))    # dB -> intensity %
        i = int(max(0, min(100, i)))
        f4 = [f] * 4
        i4 = [i] * 4
        frames.append(base + "".join(hexv(x) for x in f4 + i4 + f4 + i4))
    return frames


def find_stim(catalog, name):
    """Resolve 'category/name' or a bare unique name -> entry."""
    if "/" in name:
        cat, n = name.split("/", 1)
        for e in catalog.get(cat, []):
            if isinstance(e, dict) and (e.get("name") == n or e.get("type") == n):
                return cat, e
        return None, None
    for cat, entries in catalog.items():
        for e in entries:
            if isinstance(e, dict) and (e.get("name") == name or e.get("type") == name):
                return cat, e
    return None, None


def list_stims(catalog):
    out = []
    for cat, entries in catalog.items():
        for e in entries:
            if not isinstance(e, dict):
                continue
            out.append({
                "category": cat,
                "name": e.get("name") or e.get("type") or "",
                "typeEstim": e.get("typeEstim", ""),
                "freq": flat(e.get("frequence"), 0),
                "volume": flat(e.get("volume"), 0),
                "shape": flat(e.get("shape"), 0),
                "effectFrequence": flat(e.get("effectFrequence"), 0),
                "has_sequence": bool(e.get("sequence")),
                "steps": len(e.get("sequence") or []),
            })
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--stim")
    ap.add_argument("--level", type=float, default=30)
    ap.add_argument("--secs", type=float, default=10)
    a = ap.parse_args()
    cat = load_catalog()
    if a.demo or a.stim:
        name = a.stim or "edge/edge_2_pulse_unsynced"
        c, e = find_stim(cat, name)
        print(f"stim: {c}/{e.get('name') if e else None}")
        tl, bv = stim_timeline(e)
        tl = [s for s in tl if s[0] <= a.secs]
        ef = flat(e.get("effectFrequence"), 0) or 1.0
        k = to_k250(tl, a.level, effect=ef)
        co = to_coyote(tl, str_a=int(a.level), str_b=int(a.level))
        print(f"samples: {len(tl)}  (base volume {bv} dB, effectFreq {ef})")
        print("  t(s)   freq_hz  db     -> PW%    MA     | coyote frame")
        for (t, fr, db), (_, pw, ma), cf in list(zip(tl, k, co))[::10]:
            print(f"  {t:5.1f}  {fr:7.0f}  {db:6.1f} -> {pw:5.1f}  {ma:5.0f}  | {cf}")
        print("frames total:", len(co))
    else:
        stims = list_stims(cat)
        print(f"{len(stims)} stims. Example: --stim {stims[0]['category']}/{stims[0]['name']}")
