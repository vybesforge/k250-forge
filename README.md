# k250-forge

Reverse-engineered BLE control, a pattern engine, and a **safety limits contract** for the
**Kink K250-4S** 4-channel e-stim power box — built so that a human *or an AI agent* can drive
the hardware without being able to exceed limits the wearer agreed to.

Everything here was verified against real hardware (firmware `v1.08--18c4987-250114-01hGMT`),
one pattern at a time, with the box's own LCD as the witness.

---

## Why the limits file exists

An AI driving an e-stim box is a genuinely new failure mode. Not malice — **drift**. A model that
gets a "more" from one turn will happily carry it into the next, and a percentage that crept up
while nobody was watching is how people get hurt.

So the ceiling is not in a prompt, it is in the **code**:

```
k250-scene speed_sweep --base 40 --hardcap 90
NOTE: --hardcap 90 exceeds the agreed cap 50% — clamped to 50%.
```

`power.max_percent` in `limits.json` is the contract. A command-line `--hardcap` can only *lower*
it. `limits-form.html` generates the file. A driver that can edit its own config is a driver that
can raise its own ceiling, so keep the limits file owned by the human, not the agent.

**The hardware kill switch is the real backstop: hold any knob on the box for 2 seconds and it
powers off.** Give that to whoever is physically in the room. It needs no software, no network,
and no AI in the loop.

## Safety rules the code assumes

- **Never above the wearer's spoken ceiling.** Tonight's session: 38 → 55 → 60 → 70 → 75. Each
  raise was asked for out loud, in the moment, by the person wearing the electrodes.
- **If the wearer can't report, don't raise power.** Hold the ceiling and prefer *longer* over
  *harder*. A blank cheque from a non-verbal sub is not consent to escalate.
- **No mains power.** The device manual says don't play while connected to mains power.
- **Loops/pads loose.** Circulation at the pads is the only thing in this setup that can
  genuinely injure. Pink that fades is fine; white, pinched, or marked is a stop.
- **No pad path across the chest.** No current through the heart.
- **Skin check after every session.** Not optional, and it's the one thing software can't see.
- One BLE connection at a time. Every pattern zeroes on exit, on SIGINT, and on SIGTERM.

---

## The protocol (what was actually reverse-engineered)

Service `086e0000-7935-0d3a-ca91-bfb0c8c34043`, characteristic
`086e0001-7935-0d3a-ca91-bfb0c8c34043` (read/write/notify). Not VESC. The radio is an nRF52840
in a Raytac MDBT50Q. USB-C does not enumerate on Linux — all traffic is BLE. JSON text, compact,
on that characteristic.

| key | meaning | value |
|---|---|---|
| `PW` | power | **0..10000**, i.e. 1% = 100 |
| `MA` | Multi Adjust — **frequency/character** | **0..10000** |
| `PA` | pattern, one per channel | `["Waves","UNPLUG'D",...]` |
| `AC` | selected channel — `PW` applies to this one | `0..3` |
| `MP` | max power level (system cap) | `5..100` → LCD `L-05..L-100` |
| `CA` | per-channel plugged status | `["Active","Unplugged",...]` |
| `FV` / `BC` / `SB` / `CS` | firmware / battery % / read-only / unknown | — |

### Four traps that will cost you an evening

1. **The scale is 0..10000, not 0..100.** The official app maps its 0–100 slider by ×100. Sending
   `PW=10` is **0.1%** — imperceptible, and it looks exactly like "my commands do nothing." Always
   send `percent × 100`. Proof: cranking `MA` by hand to its maximum made the box report
   `MA: 10000`.
2. **Never trust the box to hold power — keep writing it.** A pattern that held 31% flat while
   walking `MA` wrote `PW` exactly *once* and the box went silent for 55 seconds. Any
   "skip if unchanged" cache is a bug: `w()` always writes, ~10×/second is fine.
3. **Changing `PA` zeroes `PW` and `MA`.** Re-send power after every pattern change.
4. **`PW` is never reported in a read-all.** It is only echoed when written. There is no software
   way to confirm power is flowing — **the person wearing it is the only instrument.** If they say
   they feel nothing, believe them and investigate; never tell them it "should" be working.

Also: the box gates channel selection on plug detection (`AC` writes to an unplugged channel are
silently refused), it stops advertising when asleep or off the *Remote App Control* screen, and
the Reverse Polarity Switch is **box-only** — it does not exist in the BLE protocol.

## The two axes — this is the whole instrument

Power is level. `MA` is **character**: `0` = fast buzz, `25` = slow heavy thump, and the period in
seconds ≈ `value ÷ 10000`. It is not a garnish — power can sit dead still while `MA` moves and the
sensation changes completely. That's the trick this box has that most e-stim rigs don't.

What testing settled, in the order it turned out to matter:

- **Flat 38% with speed sweeping 0↔25 over ~13 s is the favourite motion.** Sweep period 8–20 s;
  4 s is too brisk to sit in.
- **The hard drop is the money moment:** speed `25 → 0` in **one step**, not a glide. Sit at the top
  for a few seconds first; the landing lands harder.
- **Slow beats need more power** to feel equal — compensate upward as `MA` rises.
- **Cooldown** = `MA=0` at ~⅓ of base power: a low hum that just sits there. It's a landing, not
  a gap.
- **Pleasure band ≈ 35–50%.** Above that reads as pain — wanted sometimes, but deliberate.
- Compositions beat single motions: `arc` = sweep ×2 → climb/sit/hard-drop → cooldown hum → sweep
  hotter.

## Tools

```
k250-scene <pattern> [--base N --peak N --secs N --hardcap N --ma-top N --sweep-period N]
k250-scene --list                 # all patterns
k250-scene --limits-show          # the active ceiling
k250-stop                         # INSTANT STOP: kills the pattern, zeroes every live channel
k250-status                       # read-only: battery, LIVE CHANNELS, pattern/speed per channel
```

Patterns (`k250_play.py`) cover sweeps, hard drops, denial loops (`trap`, `ration`, `dread`),
climbs (`climb`, `switchback`), chaos (`stutter`, `dice`) and compositions (`arc`, `signature`).
`k250_show.py --set <setlist>` plays a whole scene in one connection.

`k250_ctl.py` holds a persistent link driven from a FIFO — the tool for *exploring* the protocol
rather than performing.

## Layout

```
k250_ble.py / k250_codec.py   one-shot control + the wire codec
k250_play.py                  pattern engine (the 35 patterns)
k250_show.py                  setlists / whole scenes
k250_ctl.py                   persistent FIFO-driven controller
k250_status.py                read-only status
k250_stop.py                  panic stop (kills patterns, zeroes all channels)
limits.json                   THE CONTRACT — power ceiling, stop word, safety toggles
limits-form.html              self-contained builder for limits.json
FINDINGS.md                   full reverse-engineering log, verdicts, dead ends
```

## Install

```bash
python3 -m venv venv && ./venv/bin/pip install bleak
cp limits.json limits.json.bak          # it's yours; edit it
ln -sf "$PWD"/k250-scene "$PWD"/k250-stop "$PWD"/k250-status ~/.local/bin/
```

`limits-form.html` opens in any browser — no server, no build step.

## Status / honest gaps

- **Multi-channel: unverified.** The engine reads `CA` and drives only plugged channels, and
  `PW` is per-selected-channel, so it *should* drive all four — but only one channel has ever had
  pads on it. Different patterns per channel (`PA` is a per-channel array) is designed, untested.
- `SB` / `CS` semantics unknown. Reverse Polarity is not reachable over BLE.
- Firmware `v1.08`'s DFU container is encrypted; no plaintext recovered.

Licence: whatever the wearer says.