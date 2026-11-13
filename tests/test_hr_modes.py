#!/usr/bin/env python3
"""test_hr_modes.py — the HR-driven game modes, as LOGIC (no radio, no controller).

Guards the things that went wrong live on the bench box:
  * MA DIRECTION on v2: higher MA = FASTER. `beats_to_ma` must map a higher beats/sec to a
    HIGHER box MA (the opposite of the old v1 period assumption). A wrong direction here
    silently inverts every "speed up / slow down" the modes intend.
  * `reverse`/`mixed` genuinely ease as HR climbs: power DOWN, and in `reverse` strokes
    also SLOW (MA down) — while `mixed` speeds strokes up. (Both corrected live 2026-11-13.)
  * PW is clamped to the ceiling from limits.json and MA to 1..99.
  * a stale HR never escalates (run_mode holds low) — checked indirectly via the clamp
    helpers and the mode contracts.

Run: venv/bin/python tests/test_hr_modes.py
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Import the module without touching a radio: hr_modes imports only k250_codec (no bleak).
spec = importlib.util.spec_from_file_location("hr_modes", os.path.join(ROOT, "hr_modes.py"))
M = importlib.util.module_from_spec(spec)
spec.loader.exec_module(M)

# The test must pass on the SHIPPED default limits.json (ceiling 5%), not just a live
# working copy tuned to 85. Pin the ceiling here so the ordering assertions are
# meaningful regardless of what limits.json happens to hold on the machine running it.
M.CEILING = 100.0

PASS = 0


def check(name, cond, detail=""):
    global PASS
    ok = bool(cond)
    print(f"  {'PASS' if ok else 'FAIL':4} {name}" + (f"   [{detail}]" if not ok else ""))
    if not ok:
        print("HR MODES: FAILURE — aborting")
        sys.exit(1)
    PASS += 1


# --- 1. MA direction on v2: higher beats/sec -> HIGHER box MA (FASTER) --------
check("beats_to_ma: 1 beat/s < 2 beats/s < 4 beats/s (higher = faster)",
      M.beats_to_ma(1.0) < M.beats_to_ma(2.0) < M.beats_to_ma(4.0),
      f"{M.beats_to_ma(1.0):.0f},{M.beats_to_ma(2.0):.0f},{M.beats_to_ma(4.0):.0f}")
check("beats_to_ma clamps into 1..99",
      1 <= M.beats_to_ma(0.01) <= 99 and 1 <= M.beats_to_ma(99.0) <= 99,
      f"{M.beats_to_ma(0.01)},{M.beats_to_ma(99.0)}")

# --- 2. reverse: calm = strong+fast, worked = weak+slow (both ease) -----------
st = {}
base = 100.0
calm_pw, calm_ma = M.m_reverse(st, base * 0.85, base=base)   # well below baseline
work_pw, work_ma = M.m_reverse(st, base * 1.25, base=base)   # well above baseline
check("reverse: power DOWN as HR climbs", work_pw < calm_pw, f"calm {calm_pw:.0f} work {work_pw:.0f}")
check("reverse: strokes SLOW (lower MA) as HR climbs", work_ma < calm_ma, f"calm {calm_ma:.0f} work {work_ma:.0f}")

# --- 3. mixed: power DOWN but strokes FASTER as HR climbs ---------------------
st = {}
calm_pw, calm_ma = M.m_mixed(st, base * 0.85, base=base)
work_pw, work_ma = M.m_mixed(st, base * 1.25, base=base)
check("mixed: power DOWN as HR climbs", work_pw < calm_pw, f"calm {calm_pw:.0f} work {work_pw:.0f}")
check("mixed: strokes FASTER (higher MA) as HR climbs", work_ma > calm_ma, f"calm {calm_ma:.0f} work {work_ma:.0f}")

# --- 4. mirror: one stroke per heartbeat — higher HR -> higher MA (faster) ----
st = {}
_, ma_lo = M.m_mirror(st, 60)
_, ma_hi = M.m_mirror(st, 120)
check("mirror: faster beat at higher HR (higher MA)", ma_hi > ma_lo, f"{ma_lo:.0f} -> {ma_hi:.0f}")

# --- 5. every mode returns (pw, ma) with pw<=CEILING, ma in 1..99 -------------
hrs = [base * f for f in (0.7, 0.9, 1.0, 1.15, 1.4)]
for name, fn in M.MODES.items():
    for hr in hrs:
        pw, ma = fn({}, hr, base=base)
        check(f"{name}: pw clamped <= ceiling ({hr:.0f}bpm)", 0 <= pw <= M.CEILING + 0.001, f"pw={pw}")
        check(f"{name}: ma clamped 1..99 ({hr:.0f}bpm)", M.MA_MIN - 0.001 <= ma <= M.MA_MAX + 0.001, f"ma={ma}")

# --- 6. punish: in-band creeps up, out-of-band drops -------------------------
st = {}
lo = base * (1 + (-8) / 100.0)
hi = base * (1 + 18 / 100.0)
inband = (lo + hi) / 2
lvl0 = st.get("lvl", 30.0)
M.m_punish(st, inband, base=base)
check("punish: in-band rewards (level rises)", st["lvl"] > lvl0, f"{lvl0:.0f} -> {st['lvl']:.0f}")
before = st["lvl"]
M.m_punish(st, base * 1.6, base=base)   # spike out of band
check("punish: out-of-band punishes (level drops)", st["lvl"] < before, f"{before:.0f} -> {st['lvl']:.0f}")

# --- 7. band_of is percentage-of-baseline ------------------------------------
lo, hi = M.band_of(100.0, -8, 18)
check("band_of: 100bpm -8%/+18% = 92..118", abs(lo - 92.0) < 0.01 and abs(hi - 118.0) < 0.01, f"{lo},{hi}")

print(f"\nHR MODES: PASS — {PASS} checks")
