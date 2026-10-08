#!/usr/bin/env python3
"""The firmware axes must fail SAFE when the firmware is unknown.

Background: PW units changed between firmware generations. v1 takes a percent x100;
v2 takes the percent itself. If the box's firmware cannot be read and the scale
defaults to v1's x100, then on v2 hardware a 24% request is written as 2400 and the
box pins to 100 -- full power, on a person, from a number that said 24.

So: unknown firmware => the SMALL units, and the drivers refuse to drive at all.
This test is written to BITE if anyone reintroduces the old default.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import k250_play as P  # noqa: E402

FAILURES = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
        print(f"  FAIL  {label}")
    else:
        print(f"  ok    {label}")


print("firmware axes fail safe")
print()

# --- unknown firmware must take the SMALL units ---------------------------------------
print("unknown firmware:")
for fv in (None, "", "garbage", "0", "3.0.0", "9.99.9"):
    check(P.pw_scale_for(fv) == 1.0,
          f"pw_scale({fv!r}) == 1.0 (small units, never v1's x100)")
    check(P.ma_box_max_for(fv) == 100.0,
          f"ma_apex({fv!r}) == 100.0 (small apex, not raw)")
    check(P.pa_writable_for(fv) is False,
          f"pa_writable({fv!r}) is False (do not claim a PA write)")

# --- the dangerous value must be unreachable for anything we cannot identify ----------
print()
print("the fatal direction is unreachable:")
for fv in (None, "", "garbage"):
    check(P.pw_scale_for(fv) != 100.0,
          f"pw_scale({fv!r}) is NOT 100.0 -- 100.0 means full power on v2 hardware")

# --- the KNOWN firmwares keep their measured behaviour --------------------------------
print()
print("known firmware unchanged:")
check(P.pw_scale_for("1.08") == 100.0, "v1 keeps x100 (measured)")
check(P.pw_scale_for("2.00.08b--v2.00.08b") == 1.0, "v2 takes the percent itself")
check(P.pw_scale_for("2.0") == 1.0, "v2 short form")
check(P.ma_box_max_for("1.08") is None, "v1 keeps the raw MA axis")
check(P.ma_box_max_for("2.00.08b") == 100.0, "v2 caps MA at its 100 apex")
check(P.pa_writable_for("1.08") is True, "v1 accepts PA")
check(P.pa_writable_for("2.00.08b") is False, "v2 refuses PA")

# --- a percent must never exceed what the caller asked for, on any firmware -----------
print()
print("the scale multiplies, never invents:")
for fv in ("1.08", "2.00.08b", None, "garbage"):
    for pct in (1, 5, 24, 64):
        check(P.pw_scale_for(fv) * pct >= pct,
              f"{pct}% x{P.pw_scale_for(fv):g} on {fv!r} is at least the request")

# --- the drivers must WAIT for FV and REFUSE when it never arrives --------------------
print()
print("drivers wait for FV and refuse to guess:")
for name in ("k250_play.py", "k250_stim_play.py"):
    src = (ROOT / name).read_text()
    check('not (k.last or {}).get("FV")' in src or 'not (kq.last or {}).get("FV")' in src,
          f"{name} waits for FV, not merely for a reply")
    check("REFUSING TO DRIVE" in src,
          f"{name} refuses to drive when the firmware is unknown")
    check('_major(_fv) not in FW_AXES' in src,
          f"{name} refuses a firmware that is not in the table")
    check('FW_AXES' in src, f"{name} knows the firmware table")

# the axes line must not be gated on the scale being non-default
for name in ("k250_play.py", "k250_stim_play.py"):
    src = (ROOT / name).read_text()
    check("if pl.ma_out_max or pl.pw_scale != 100.0:" not in src,
          f"{name} does not silently skip the axes log when the scale is the default")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED of {CHECKS} checks:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
print(f"all {CHECKS} checks passed")
