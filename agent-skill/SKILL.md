---
name: k250-operator
description: "Use when driving the Kink K250-4S e-stim box from an agent. The two hard rules, the limits contract, and the protocol traps."
version: 1.0.0
---

# K250-4S — driving the box from an agent

Copy this folder into your agent's skills directory (or paste it into whatever your harness
auto-loads). Nothing here is specific to one agent: it is the portable version of the operator skill
used while building this repo, and it is deliberately short. The repo itself — `README.md`,
`FINDINGS.md`, `k250_play.py` — is the reference; this file is the part that must not be lost.

## The two hard rules

1. **The stop word ends everything, instantly.** No negotiation, no "one more", no check-in
   afterwards. Run `k250-stop` and stop talking about it. The wearer's stop word is in
   `limits.json` (`stop_word`, shipped default `red`). The hardware kill switch — hold any knob ~2 s
   — needs no software at all, and either one is sufficient.
2. **No sensation means power DOWN, then check the loop — never more power.** "I feel nothing" is a
   **fault report, not a blank cheque.** A pad that is loose or half-attached concentrates the
   current into a smaller area, and that is what burns people. Stop, lower, then verify pads are
   seated and the cable is attached. Never escalate into silence, and never tell the wearer it
   "should" be working.

## Non-negotiable

- The ceiling lives in `limits.json`, and it is **clamped in code** — the engine refuses to write
  above `power.max_percent` no matter what you ask for, and a command-line `--hardcap` can only
  lower it. Never work around it, and never edit it to raise a limit mid-scene without a spoken,
  explicit request from the wearer.
- The session budget (`session.max_duration_s`) is enforced the same way, by `k250_session.py`.
  When it refuses, the answer is stop — not a workaround.
- Read `safety.hard_stops` in `limits.json` before the first run. No pad path across the chest or
  heart. Never on mains power. Loops loose.
- **The wearer is the only instrument.** See below.

## Operating

| | |
|---|---|
| check the box | `k250-status` — firmware, battery, **live channels**, pattern/speed per channel |
| run a pattern | `k250-scene <pattern> --base N --secs N` · `k250-scene --list` for all of them |
| stop | `k250-stop` — zeroes every channel and kills any running pattern. Exit 1 = could not reach the box: treat as unsafe and get to it physically |
| your ceilings | `k250-scene --limits-show` · `python k250_play.py --limits-show` on Windows |
| no bash (Windows) | `venv\Scripts\python k250_play.py …` — the engine reads `limits.json` itself, so the ceiling still applies |

**Battery:** mention it at or below `battery.report_below_percent` in `limits.json` (10 % by default),
or if the box drops off BLE and low charge is the plausible cause. Above that it is noise — the pack
idles in the 20–30 % band on a bench charger, and it has gone flat mid-scene before, so the threshold
is the only figure worth acting on.

## Protocol traps that look like bugs

- **Values are 0..10000, not 0..100.** The app multiplies its slider by 100. Sending `PW=50` is
  0.5 %, i.e. nothing. Always `pct * 100`.
- **`PW` is never reported back.** A read-all omits it — power is only echoed when written. Nothing
  in the software can confirm power is flowing; the wearer is the only instrument. `CA`
  (`Active`/`Unplugged`) proves an electrode is *attached*, which is load, not delivery.
- **A pattern change zeroes that channel's power and frequency.** Re-send power after any `PA`
  write; the engine's `set_pattern()` already drops its caches for this reason.
- **A channel with a blank pattern refuses power entirely** — the box echoes `PW: 0` while `CA`
  says `Active`. Give the channel a pattern first.
- **One BLE connection at a time.** Never run two tools at once. Don't pair the box in the OS
  Bluetooth settings — a paired connection locks the tool out; let the tool scan.
- **The box stops advertising when it sleeps.** A scan that finds nothing is usually that: wake it
  (press any knob ~1 s) and put it on Options → Remote App Control.
- **The LCD glitches while you drive it** — tab jumping, smearing. Cosmetic, and a useful sign that
  writes are landing. Not a bug to chase.
- In Manual mode `PW` = Power Multiplier and `MA` = Link Delay (seconds), not frequency. `MA` is a
  beat *period*: `MA=0` is the buzziest, `MA=10000` ≈ one beat per second. Slow beats deliver less
  average energy — compensate power upward as `MA` rises, or it reads as weak.
