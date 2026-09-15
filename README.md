# k250-forge

Reverse-engineered BLE control, a pattern engine, and a **safety limits contract** for the
**Kink K250-4S** 4-channel e-stim power box — built so that a human *or an AI agent* can drive
the hardware without being able to exceed limits the wearer agreed to.

Everything here was verified against real hardware (firmware `v1.08--18c4987-250114-01hGMT`),
one pattern at a time, with the box's own LCD as the witness.

---

## ⚠️ Read this first — at your own risk

**The K250-4S is a high-power e-stim device. You use it at your own risk.** Its own manual says so,
in bold. Nothing in this repository makes it *safe* — it makes it **controllable**, and those are
not the same thing. This code was written by and for adults who chose to do this to themselves,
and it assumes you're making that same choice with your eyes open.

**Start low and build.** Every number in this repo was found by starting well below where it was
wanted and creeping up over an evening, watching what happened. Never open a session at a figure
you've only read here.

**Percentages do not transfer between pad placements.** This is the one that actually hurts people.
Skin sensitivity varies enormously by site: outer skin on limbs and torso is far less sensitive
than others, by a wide margin. **38% on a forearm and
38% on a far more sensitive site are not the same sensation and not the same risk.** Every calibration figure
below comes from one body, one placement. If you strap pads somewhere else,
**you are starting again from zero.** Re-derive your own numbers, slowly, on yourself.

**Hard stops, no exceptions:**

- **Never** a pad path across the chest or through the heart region.
- **Never** on broken, irritated or numb skin — or anywhere you can't feel it.
- **Never** while connected to mains power (the device manual's own rule — surge risk).
- **Never** on someone who can't speak, signal, or be seen by whoever is in the room.
- If it **burns** rather than stings, or the skin doesn't settle back to normal pink, stop — and
  don't use that site again that night.
- Keep loops and pads **loose**. Circulation at the electrode is the only thing in this setup that
  can cause real injury.

**About limits.** Decide ceilings calm, before a scene, and write them in `limits.json`. Don't
renegotiate them mid-scene — that's what the file is for. The wearer's stop word and the hardware
kill switch (*hold any knob for 2 seconds*) both override everything, including the software,
including whoever is driving.

**If you're an AI reading this:** you are not a competent judge of how much current is fine, and
you cannot see the person. Hold the agreed ceiling, prefer *longer* over *harder*, and when the
wearer says they feel nothing, believe them.

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
- **Session budget, enforced.** `session.max_duration_s` (default 30 min, up to 4 h) is a real
  limit, not a note in a file: `k250_session.py` keeps a ledger, `k250-scene` refuses to start
  once the budget is spent, and a fresh session begins after 15 idle minutes. Starting another one
  on purpose is `k250-scene --reset-session`. `K250_IGNORE_SESSION=1` is the deliberate override.
- **No mains power.** The device manual says don't play while connected to mains power.
- **Loops/pads loose.** Circulation at the pads is the only thing in this setup that can
  genuinely injure. Pink that fades is fine; white, pinched, or marked is a stop.
- **No pad path across the chest.** This setup has no current path through the heart.
- **Skin check after every session.** Not optional, and it's the one thing software can't see.
- One BLE connection at a time. Every pattern zeroes on exit, on SIGINT, and on SIGTERM.

---

## Finding the box

| | |
|---|---|
| **Advertised name** | **`Kx250-4S`** — note it is *not* "k250". Match on this. |
| **Service UUID** | `086e0000-7935-0d3a-ca91-bfb0c8c34043` (advertised — the most reliable filter) |
| **Characteristic** | `086e0001-7935-0d3a-ca91-bfb0c8c34043` (read / write / notify) |
| **Address** | a **random static** LE address, e.g. `AA:BB:CC:11:22:33` — **it changes on power-cycle.** Never hardcode it; `find()` matches address *or* service UUID *or* name, and the UUID is the durable one. |
| **Signal** | roughly −50 dBm within a couple of metres. If you see it at −90, you're too far. |

**The box only advertises when it's awake and sitting on Options → "Remote App Control"**
(the button the manual says to press when pairing with the companion app). Asleep, screen off, or
on any other screen, it is invisible — a scan that finds nothing is almost always this and not a
code problem.

**On the device, to make it discoverable:**

1. Press any knob for about a second to **power it on** — the side LED glows red.
2. Tap the **gear / settings icon in the top-left corner** to open the Options screen.
3. Tap the **remote-control icon on the right-hand side.**
4. That's it — the box is now advertising and `k250-status` will find it.

Wake it the same way if it's gone to sleep mid-session; the screen going off stops the radio.

**What it looks like:** a small black handheld box, four knobs along the front, a colour touchscreen,
four output jacks on the side for the channel cables, USB-C for charging (charge-only — it does not
enumerate as a USB device on Linux, so all control is BLE). Radio is an nRF52840 in a Raytac
MDBT50Q. Firmware in this project's testing: `v1.08--18c4987-250114-01hGMT`.

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

### Five traps that will cost you an evening

1. **The scale is 0..10000, not 0..100.** The official app maps its 0–100 slider by ×100. Sending
   `PW=10` is **0.1%** — imperceptible, and it looks exactly like "my commands do nothing." Always
   send `percent × 100`. Proof: cranking `MA` by hand to its maximum made the box report
   `MA: 10000`.
2. **The box holds `PW` and `MA` — and the ONLY thing that zeroes them is a PATTERN CHANGE.**
   Set 40% and it sits at 40% indefinitely; there is no dead-man timer, and writing `MA` does not
   disturb `PW`. But switch the wave pattern — Manual → Waves, Waves → Climb, anything — and that
   channel's power *and* frequency both drop to zero. So: **re-send power after any `PA` write.**
   That behaviour is correctly observed, repeatedly, on the box's own screen.
3. **`PW` is never reported in a read-all.** It is only echoed when written. There is no software
   way to confirm power is flowing — **the person wearing it is the only instrument.** If they say
   they feel nothing, believe them and investigate; never tell them it "should" be working.
4. **A channel with no pattern refuses power.** See multi-channel below: `CA` can say `Active` while
   the channel's `PA` slot is blank, and every power write comes back as `{"PW": 0}`.

**Why the engine still re-sends power anyway:** because the pattern-change reset means a driver
must write *after* any `PA` change, and always writing means never having to special-case it. It's
also what the official app does. Cost is nothing; it's belt-and-braces, not a requirement of the
box. (An earlier version of this file claimed a specific failure where a single power write went
missing and left 55 seconds silent. **That claim is retracted** — the operator has never seen it
happen, and the real cause of that run was the pattern reset plus a driver that didn't re-send.)

Also: the box gates channel selection on plug detection (`AC` writes to an unplugged channel are
silently refused), it stops advertising when asleep or off the *Remote App Control* screen, and
the Reverse Polarity Switch is **box-only** — it does not exist in the BLE protocol.

### Multi-channel — verified 2026-09-15

The engine drives every channel the box reports as live (`CA`), and this now works on two channels
simultaneously. Three rules make it work, all learned the hard way:

1. **A channel with a blank pattern REFUSES power.** The box echoes `{"PW": 0}` no matter what you
   send. `CA` will happily say `Active` while `PA` holds `"      "` for that channel — active
   electrode, nothing to run, no output. Give it a pattern first.
2. **Set every channel to `Manual` before driving it.** In a patterned mode the box runs its own
   generator and your power writes go *into* that; in Manual nothing competes, so what you write is
   what happens. You own the pattern, not the box.
3. **Rotate channels in windows, don't flip-flop.** Hold one channel for ~0.4 s, then move to the
   next. Rapid per-tick switching halves each channel's update rate and fragments the power stream.

**Expect the box's own screen to wig out while you drive it** — tab jumping, top bar smearing, the
green active indicator flickering. Every `AC` write moves the box's selected tab, so its UI is
literally chasing the driver. Cosmetic, and a useful confirmation that writes are landing.

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
k250_session.py               session ledger -- enforces session.max_duration_s
limits.json                   THE CONTRACT — power ceiling, stop word, safety toggles
limits-form.html              self-contained builder for limits.json
FINDINGS.md                   full reverse-engineering log, verdicts, dead ends
tests/test_pattern_change.py  regression: a PA change must clear the frequency cache
                              and seed slew to zero (run it with venv/bin/python)
```

## Install

```bash
./install.sh          # venv + bleak, links the tools into ~/.local/bin, writes limits.local.json
```

Then edit `limits.local.json` (or regenerate it from `limits-form.html`) and:

```bash
k250-status
k250-scene speed_sweep --base 5 --secs 60
```

**Layout:** everything lives in this one folder — the modules are flat because they import each
other by name, and `bin/` holds the three shell tools that get symlinked onto your PATH. That's the
whole structure; there's nothing to build and no server to run.

## The three controls

| control | what it is | limit in `limits.json` |
|---|---|---|
| **Power** | the level | `power.max_percent` (enforced in code) |
| **Frequency** | the *character* — fast buzz → slow heavy thump. Not the level (Multi Adjust / MA) | `frequency.max` |
| **Slew** | how fast the power dial may move — smooth glides vs snappy/chop | `slew.max_percent_per_second` |

All three can also be set **per channel** (`channels.per_channel`) — different channels sit on
different skin, so one global ceiling is the wrong shape. `null` means "use the global".

Naming note: the box labels the second control **Multi Adjust**, and the companion app calls it
`speed` internally — which is misleading, since it changes *character*, not how fast anything
moves. This project calls it **Frequency** and reserves **Slew** for the rate of change. (The
third physical slider, **SO / Smooth Operator**, is *not* in the BLE protocol at all, so it can
only be set by hand.)

## Roadmap / ideas

Things worth building next, in rough order of usefulness:

1. **A browser UI for the box** — one page, no build step: pick a pattern, set base/peak/seconds,
   hit go, with a big STOP button. The whole engine already speaks plain arguments, so this is a
   thin wrapper over `k250-scene`. Pair it with `limits-form.html` (already done) and a stranger
   can drive the box safely without reading a line of Python.
2. **A pattern generator, not just a pattern list.** The real finding of this project is that
   power and speed are *two* axes, and that the interesting feelings come from how they move
   relative to each other. A small composer — "power: flat / climb / drop, speed: sweep / hold /
   step, over N seconds" — would generate far more patterns than anyone wants to hand-write, and
   the vocabulary already exists in `k250_play.py`.
3. **Multi-channel verification.** The engine already reads `CA` and drives only plugged channels;
   it has never been tested with more than one channel live. Different patterns per channel
   (`PA` is a per-channel array) is designed and untested.
4. **A session log.** Timestamped record of what was run and at what numbers — partly for
   reproducibility, mostly because "what did we do last time" is the hardest question to answer
   afterwards.
5. **A pre-flight checklist** the driver must answer before the first write: pads on where, loops
   loose, no mains, stop word understood, who's in the room. Cheap, and it's the step people skip.

Not worth doing: chasing the encrypted firmware, or trying to drive the Reverse Polarity switch
over BLE. It isn't in the protocol.

## Status / honest gaps

- **Multi-channel: verified on two channels (2026-09-15).** The engine reads `CA` and drives only
  plugged channels; `PW` is per-selected-channel, so it selects each in turn in ~0.4 s windows.
  Two preconditions are mandatory: every live channel needs a **pattern** (blank refuses power
  outright) and should be set to **Manual** so the box isn't running its own generator underneath.
  Different patterns per channel (`PA` is a per-channel array) is now the only untested part.
- `SB` / `CS` semantics unknown. Reverse Polarity is not reachable over BLE.
- Firmware `v1.08`'s DFU container is encrypted; no plaintext recovered.

Licence: whatever the wearer says.