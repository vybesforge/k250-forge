# k250-forge

A **BLE driver and pattern engine for the Kink K250-4S** 4-channel e-stim power box, driven from a
computer.

It does two things the stock companion app doesn't: it talks to the box **directly over Bluetooth
LE**, and it **generates patterns** — timed compositions of power and frequency — rather than only
replaying the handful the box ships with. Everything here was measured against real hardware
(firmware `v1.08--18c4987-250114-01hGMT`), one pattern at a time, with the box's own LCD as the
witness.

Built so a human **or an AI agent** can drive the hardware without being able to exceed limits the
wearer agreed to. [Install](#install) is next; the full safety contract is under
[Safety & limits](#safety--limits), and the reverse-engineering detail after that.

---

## ⚠️ At your own risk — read this first

**At your own risk.** High-power e-stim device — this makes it **controllable**, not safe. **Start low
and build.** Percentages don't transfer between pad placements: outer skin is far less sensitive than
others, so the same figure is a different sensation and a different risk.

**The hard stops — every session, no exceptions:**

- **Never** a pad path across the chest or through the heart.
- **Never** on broken, irritated or numb skin — or anywhere they can't feel it.
- **Never** while plugged into mains power.
- **Never** on someone who can't speak or signal, or who can't be seen by whoever is in the room.
- Keep loops and pads **loose** — circulation at the electrode is the one thing here that can cause
  real injury.
- If it **burns rather than stings**, or the skin doesn't settle back to normal, stop — and don't
  use that site again that night.
- If they feel **nothing**, turn the power **down** before you touch anything else, then check the
  loop: pads properly seated, cable attached, connection complete. A pad that's loose or half-attached
  concentrates the current into a smaller area — and that is what burns people. Feeling nothing is a
  **fault, not an invitation to push harder**.

---

## Install

**Every platform needs:** Python 3 with `pip` and `venv`, and working Bluetooth LE on the host.
That's it — no app, no server, no build step. `bleak` is the only dependency, and it speaks to each
platform's native Bluetooth stack.

There is **no fixed Python floor**. `pip install bleak` resolves the newest release that runs on
*your* interpreter: an older Python (e.g. 3.9) gets an older bleak (1.1.1) that installs and runs
fine, a newer one gets the current release. The installer just lets pip resolve and then confirms
`import bleak` loads — so a genuinely too-old Python fails with pip's own honest error, not a guess.
Don't install a second Python hoping to satisfy a version floor that isn't there.

### Linux

```bash
git clone https://github.com/vybesforge/k250-forge.git
cd k250-forge
./install.sh
```

`install.sh` builds a venv, installs `bleak`, symlinks `k250-scene` / `k250-stop` / `k250-status`
into `~/.local/bin`, and writes a conservative `limits.local.json`.

- Needs **BlueZ running** (`systemctl status bluetooth`) and `~/.local/bin` on your `PATH`.
- If scanning finds nothing: `rfkill list`, then say the word — Bluetooth turned off is the usual cause.
- **Don't run as root.** Root often can't reach BlueZ on a desktop session; your normal user can.
- **The venv is not optional, and not a style choice.** Debian, Ubuntu and Fedora block `pip install`
  into the system Python (PEP 668, `externally-managed-environment`). `install.sh` builds its own venv,
  so don't `pip install bleak` system-wide first — if you already tried, that error is the reason.
- `install.sh` is bash and needs **bash 3.2 or newer** (any distro's default; on Alpine:
  `apk add bash python3 python3-venv`). The tools themselves are the same on any of them.
- **Headless is fine.** There is no GUI and no desktop session anywhere in this: the tools speak BLE
  over BlueZ and print text. (The only desktop-ish thing in it is `limits-form.html`, a plain file you
  open wherever you like.)

### macOS

```bash
git clone https://github.com/vybesforge/k250-forge.git && cd k250-forge
./install.sh
```

macOS needs no extra packages — `bleak` uses CoreBluetooth. The stock `python3` is often Xcode's old
3.9, but there is **no version floor to clear**: pip just resolves the newest bleak that runs on it.
If you'd rather run the current bleak (or a newer Python 3), `brew install python@3.12`, then either
put it first (`export PATH="$(brew --prefix)/bin:$PATH"`) or run the installer as
`PATH="$(brew --prefix)/bin:$PATH" ./install.sh`.

- **Grant Bluetooth permission** to whatever app runs the commands (Terminal, iTerm, your editor).
  The prompt appears once on first scan; if it was dismissed, re-enable it in
  System Settings → Privacy & Security → Bluetooth.
- Everything runs on macOS's **stock `/bin/bash` (3.2)** — no Homebrew bash needed. That is checked in
  `tests/test_shell_compat.py`, because bash 3.2 has sharp edges newer bash does not.
- `install.sh` links into `~/.local/bin`, which isn't on macOS's `PATH` by default. Add it
  (`export PATH="$HOME/.local/bin:$PATH"`), or just call the tools as
  `./venv/bin/python k250_play.py …`.

### Windows

In PowerShell, in the folder you want:

```powershell
git clone https://github.com/vybesforge/k250-forge.git
cd k250-forge
py -3 -m venv venv
.\venv\Scripts\pip install bleak
```

- `bleak` uses the WinRT Bluetooth stack — **no extra drivers**.
- **Check which Python `py -3` means.** It picks your newest Python 3; pip then resolves the newest
  bleak that runs on it, so there's no floor to chase. With several installed, name the one you want:
  `py -3.12 -m venv venv`.
- Turn **Bluetooth on** and make sure the box is awake before scanning.
- `install.sh` is a bash script and won't run natively. Call the engine directly — **it reads
  `limits.json` itself**, so the ceiling and the session budget apply here exactly as they do through
  the wrapper (a command-line `--hardcap` can only *lower* the file's ceiling):

```powershell
.\venv\Scripts\python k250_play.py --list                  # the 35 patterns
.\venv\Scripts\python k250_play.py --limits-show           # the ceilings that apply here
.\venv\Scripts\python k250_play.py speed_sweep --base 5 --secs 60
.\venv\Scripts\python k250_stop.py
.\venv\Scripts\python k250_status.py
```

  It looks for `limits.local.json`, then `limits.json`, next to the script — or wherever
  `--limits PATH` / the `K250_LIMITS` environment variable points.
- **What else works natively:** `k250_show.py` (setlists) and `k250_status.py` / `k250_stop.py`, as
  above. `k250_stop.py` kills a running pattern with PowerShell's `Get-CimInstance Win32_Process`
  before zeroing the pads — and if it cannot list processes it says so, rather than reporting a clean
  stop it did not achieve.
- **What doesn't:** the shell wrappers in `bin/` (they're bash) and `k250_ctl.py` (it drives a FIFO,
  which Windows doesn't have — it tells you that and exits, rather than throwing). Neither is needed:
  the engine and the stop tool are the whole contract, and both enforce limits themselves.
- **Verified on Windows on real hardware** (Sep 2026): the install, the engine, the limits contract,
  and driving the box over BLE. The one branch not exercised there is the stop tool's *process-kill* line.
- Git Bash / WSL works too, and `install.sh` will run there.

### All platforms — the two rules that catch everyone

- **One BLE connection at a time.** Don't pair the box in the OS Bluetooth settings — let the tool scan
  and connect. If the phone app is connected, nothing here can reach it.
- **The box only advertises when it's awake and on the right screen** — see below.

### Make the box discoverable

Do this before your first scan, and any time the box has gone quiet:

1. Press any knob for about a second to **power it on** — the side LED glows red.
2. Tap the **gear / settings icon in the top-left corner** to open the Options screen.
3. Tap the **remote-control icon on the right-hand side.**
4. That's it — the box is now advertising and `k250-status` will find it.

Wake it the same way if it's gone to sleep mid-session; the screen going off stops the radio.

---

## Safety & limits

The full contract: how the ceiling is enforced, and what else the code assumes.

### Beyond the hard stops

The hard stops are at the top of this file. Two rules that keep them working:

- **Decide ceilings calm, before a session, and write them in `limits.json`.** Don't renegotiate them
  mid-scene — that's what the file is for. The wearer's stop word and the hardware kill switch both
  override everything, including the software, including whoever is driving.
- **Skin check after every session.** Not optional, and it's the one thing software can't see. Skin that
  settles back to normal is fine; pinched or marked is a stop.

**The hardware kill switch is the real backstop: hold any knob on the box for 2 seconds and it powers
off.** Give that to whoever is physically in the room. It needs no software, no network, and no AI in
the loop.

### Why the limits file exists

An AI driving an e-stim box is a genuinely new failure mode. Not malice — **drift**. A model that
gets a "more" from one turn will happily carry it into the next, and a percentage that crept up while
nobody was watching is how people get hurt.

So the ceiling is not in a prompt, it is in the **code**:

```
k250-scene speed_sweep --base 40 --hardcap 90
NOTE: --hardcap 90 exceeds the agreed cap 50% — clamped to 50%.
```

`power.max_percent` in `limits.json` is the contract. A command-line `--hardcap` can only *lower* it.
`limits-form.html` generates the file. A driver that can edit its own config is a driver that can
raise its own ceiling, so keep the limits file owned by the human, not the agent.

The session budget is enforced the same way: `k250_session.py` keeps a ledger and refuses to start
once `session.max_duration_s` is spent, whoever is asking. Default 30 min, up to 4 h; a fresh session
begins after 15 idle minutes, or with `k250-scene --reset-session`. `K250_IGNORE_SESSION=1` is the
deliberate override.

### The three controls

| control | what it is | limit in `limits.json` |
|---|---|---|
| **Power** | the level | `power.max_percent` (enforced in code) |
| **Frequency** | the *character* — fast buzz → slow heavy thump (the box calls it Multi Adjust) | `frequency.max` |
| **Slew** | how fast the power dial may move — smooth glides vs snappy/chop | `slew.max_percent_per_second` |

All three can also be set **per channel** (`channels.per_channel`) — different channels sit on
different skin, so one global ceiling is the wrong shape. `null` means "use the global".

Naming note: the companion app calls the second control `speed` internally, which is misleading —
it changes *character*, not how fast anything moves. This project calls it **Frequency** and reserves
**Slew** for the rate of change. (The third physical slider, **SO / Smooth Operator**, is *not* in the
BLE protocol at all, so it can only be set by hand.)

### About the battery

Battery is **not a limit — a reporting rule**. The pack idles in the 20–30 % band during bench work,
so a charge figure above the threshold says nothing useful. Mention it at or below
`battery.report_below_percent` (default 10 %), or if the box drops off BLE and low charge is the
plausible cause. It has gone flat mid-scene before.

### If you're an AI reading this

You are not a competent judge of how much current is fine, and you cannot see the person. Hold the
agreed ceiling, prefer *longer* over *harder*. **If they feel nothing, cut the power and check the
loop** — never escalate into silence.

Two rules any skill built from this repo must carry, in the skill itself:

1. **The stop word ends everything instantly**, with no discussion and no check-in afterwards.
2. **No sensation means stop and check the loop — never more power.**

Neither needs software in the loop: the stop word is absolute, and so is the hardware kill switch.
Both are already written that way in **[`agent-skill/SKILL.md`](agent-skill/SKILL.md)** — see
[For an AI agent](#for-an-ai-agent-or-anyone-writing-a-skill-from-this).

---

## Finding the box

| | |
|---|---|
| **Advertised name** | **`Kx250-4S`** — note it is *not* "k250". Match on this. |
| **Service UUID** | `086e0000-7935-0d3a-ca91-bfb0c8c34043` (advertised — the most reliable filter) |
| **Characteristic** | `086e0001-7935-0d3a-ca91-bfb0c8c34043` (read / write / notify) |
| **Address** | a **random static** LE address (it looks like any MAC, e.g. `AA:BB:CC:11:22:33`) — **it changes on power-cycle.** Never hardcode it; `find()` matches address *or* service UUID *or* name, and the UUID is the durable one. |
| **Signal** | roughly −50 dBm within a couple of metres. If you see it at −90, you're too far. |

**The box only advertises when it's awake and sitting on Options → "Remote App Control"** — the button
the manual says to press when pairing with the companion app. The sequence is under
[Make the box discoverable](#make-the-box-discoverable). On any other screen, asleep, or with the
screen off, it is invisible: a scan that finds nothing is almost always this, not a code problem. It
does not enumerate as a USB device either — **all control is BLE** (USB-C is charge-only on Linux).

**What it looks like:** a small black handheld box, four knobs along the front, a colour touchscreen,
four output jacks on the side for the channel cables, USB-C for charging. Radio is an **nRF52840** in
a Raytac MDBT50Q (FCC ID `SH6MDBT50`).

## The protocol (what was actually reverse-engineered)

JSON text, compact, on the characteristic above. Not VESC. Fields:

| key | meaning | value |
|---|---|---|
| `PW` | power | **0..10000**, i.e. 1% = 100 |
| `MA` | Multi Adjust — **frequency/character** | **0..10000** |
| `PA` | pattern, one per channel | `["Waves","UNPLUG'D",...]` |
| `AC` | selected channel — `PW` applies to this one | `0..3` |
| `MP` | max power level (system cap) | `5..100` → LCD `L-05..L-100` |
| `CA` | per-channel plugged status | `["Active","Unplugged",...]` |
| `FV` / `BC` / `SB` / `CS` | firmware / battery % / read-only / unknown | — |

Read-all (app sync):
`{"AC":"","PW":"","MA":"","GP":"","PA":"","CA":"","MP":"","SB":"","BC":"","CS":"","FV":"","ER":"0"}`.
Notifications are pretty JSON, numbers sometimes unquoted, and may split across packets — strip
`\n`, `\r`, `\x00` and debounce ~60 ms before parsing.

### Other Tech Notes:

1. **The scale is 0..10000, not 0..100.** The official app maps its 0–100 slider by ×100. Sending
   `PW=10` is **0.1 %** — imperceptible, and it looks exactly like "my commands do nothing." Always
   send `percent × 100`. Proof: cranking `MA` by hand to its maximum made the box report `MA: 10000`.
2. **The box holds `PW` and `MA` — and the ONLY thing that zeroes them is a PATTERN CHANGE.** Set 40 %
   and it sits there indefinitely; there is no dead-man timer, and writing `MA` does not disturb `PW`.
   But switch the wave pattern — Manual → Waves, Waves → Climb, anything — and that channel's power
   *and* frequency both drop to zero. So: **re-send power after any `PA` write.** Observed repeatedly
   on the box's own screen.
3. **`PW` is never reported in a read-all.** It is only echoed when written. There is no software way
   to confirm power is *flowing* — **the person wearing it is the only instrument.** What you *can*
   detect is load: `CA` reports per-channel `Active` / `Unplugged`, the box's own continuity sense.
   Current, impedance and actual delivery are not answerable from here.
4. **A channel with no pattern refuses power.** `CA` can say `Active` while the channel's `PA` slot is
   blank, and every power write comes back as `{"PW": 0}`. See multi-channel below.
5. **The box's own screen wigs out while you drive it** — tab jumping, top bar smearing, the green
   active indicator flickering. It redraws its UI on *every command it receives*: measured on the real
   box, at ~7 BLE frames per second the top bar visibly jumps on essentially every write. Cosmetic and
   harmless — and a useful confirmation that your writes are landing at all. It is firmware behaviour,
   not something the driver does wrong. The only way to reduce it is to send fewer frames, which trades
   display calm for pattern resolution.

**Why the engine still re-sends power anyway:** because the pattern-change reset means a driver must
write *after* any `PA` change, and always writing means never having to special-case it. It's also what
the official app does. Cost is nothing; belt-and-braces, not a requirement of the box.

Also: the box gates channel selection on plug detection (`AC` writes to an unplugged channel are
silently refused), and the Reverse Polarity Switch is **box-only** — it does not exist in the BLE
protocol.

### Multi-channel — verified 2026-09-15

The engine drives every channel the box reports as live (`CA`), and this works on two channels
simultaneously.

1. **A channel with a blank pattern refuses power** — see the blank-pattern note above. Give it a pattern first.
2. **Set every channel to `Manual` before driving it.** In a patterned mode the box runs its own
   generator and your power writes go *into* that; in Manual nothing competes, so what you write is
   what happens. You own the pattern, not the box.
3. **Rotate channels in windows, don't flip-flop.** Hold one channel for ~0.4 s, then move to the next.
   Rapid per-tick switching halves each channel's update rate and fragments the power stream.

**Still untested:** different patterns per channel. `PA` is a per-channel array, but the live channels
have only ever been driven with one pattern between them.

## The two axes — this is the whole instrument

Power is level. `MA` is **character**: `0` = fast buzz, `25` = slow heavy thump, and the period in
seconds ≈ `value ÷ 10000`. It is not a garnish — power can sit dead still while `MA` moves and the
sensation changes completely. That's the trick this box has that most e-stim rigs don't.

What testing settled, in the order it turned out to matter:

- **A flat level with the speed axis sweeping 0↔25 over ~13 s is the motion this box does best.**
  Sweep periods of 8–20 s sit well; 4 s is too brisk.
- **The hard drop is the money moment:** speed `25 → 0` in **one step**, not a glide. Sit at the top for
  a few seconds first; the landing lands harder.
- **Slow beats need more power** to feel equal — compensate upward as `MA` rises.
- **Cooldown** = `MA=0` at ~⅓ of base power: a low hum that just sits there. It's a landing, not a gap.
- **These are starting points, not transferable numbers** — see the placement note at the top and in
  `limits.json`.
- Compositions beat single motions: `arc` = sweep ×2 → climb/sit/hard-drop → cooldown hum → sweep hotter.

## Tools

```
k250-scene <pattern> [--base N --peak N --secs N --hardcap N --ma-top N --sweep-period N]
k250-scene --list                 # all patterns (same as: python k250_play.py --list)
k250-scene --limits-show          # the active ceiling
k250-stop                         # INSTANT STOP: kills the pattern, zeroes every live channel
k250-status                       # read-only: battery, LIVE CHANNELS, pattern/speed per channel
```

Patterns (`k250_play.py`) cover sweeps, hard drops, denial loops (`trap`, `ration`, `dread`), climbs
(`climb`, `switchback`), chaos (`stutter`, `dice`) and compositions (`arc`, `signature`).
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
LICENSE                       Apache-2.0
agent-skill/SKILL.md          portable operator skill — two hard rules, drop-in for an agent
tests/                        eight suites, all runnable from any clone:
  test_pattern_change.py        regression: a PA change must clear the frequency cache
                                and seed slew to zero
  test_limits_enforcement.py    the limits file beats the command line, on every entry point
  test_write_policy.py          redundant power writes are skipped, but never unsafely
  test_portability.py           nothing shipped points at the author's machine; POSIX-only
                                calls are guarded; the shipped skill still has both rules
  test_wrapper_cli.py           the wrapper tools work from any cwd, and fail loudly
  test_shell_compat.py          no bash-4-only syntax; arrays guarded for macOS's bash 3.2
  test_limits_form.py           limits-form.html still generates a contract the engine reads
                                (needs node; skips cleanly without it)
  test_stop_parsers.py          k250-stop finds the right processes on POSIX and Windows
```

Run them all: `for t in tests/test_*.py; do venv/bin/python "$t"; done`

Everything lives in one folder — the modules are flat because they import each other by name, and
`bin/` holds the shell tools that get symlinked onto your `PATH`.

## First steps

Do these in order, the first time, with the wearer connected and someone's hand near the box.

1. **Wake the box and enable remote control** (see [Make the box discoverable](#make-the-box-discoverable)).
   It advertises as **`Kx250-4S`**.
2. **Check it's reachable** — `k250-status` reads the battery, the live channels and the current pattern.
3. **Read your limits before you drive anything.** Open `limits.json`, or build one with
   `limits-form.html`. Know three things: the power ceiling, the stop word, and the session budget.
4. **Lowest useful first run.** With the wearer able to speak, and the physical kill switch in the room:
   ```bash
   k250-scene speed_sweep --base 5 --secs 30
   ```
   5 % is the shipped default start. It is meant to be barely anything — the point is to prove the path
   works, not to be a scene.
5. **Prove the stop works, both ways.** Say the stop word out loud, run `k250-stop`, and show everyone
   the hardware kill switch. Do this *before* the first real scene.
6. **Then build** — up only on the wearer's spoken word, never in silence. See the hard stops at the top.

## For an AI agent (or anyone writing a skill from this)

Everything an agent needs is in the repo, as plain text. A skill built from this should be able to
connect, drive, and stop the box without guessing:

| to do this | read this |
|---|---|
| Understand the device, and connect to it | **Finding the box** above, and `FINDINGS.md` — the full log, including the dead ends |
| Talk to it correctly (frames, keys, the 0..10000 scale) | **The protocol** section, and `k250_codec.py` |
| Know what it must never do | `limits.json` → `safety.hard_stops`, plus **Safety & limits** |
| Drive it | `k250-scene <pattern> --base N --secs N`. On Windows, where the wrapper can't run, the engine clamps to the same ceiling itself |
| Stop it | `k250-stop` — the correct response to a stop word, and to "I feel nothing" |
| See what's connected | `k250-status` |
| See what patterns exist | `k250_play.py --list` — works on every platform, no box needed |

**Put the safety in the tool, not in the prompt.** The engine clamps to `limits.json` no matter what a
driver asks for, so an agent cannot exceed the wearer's agreed ceiling even if it tries. An agent does
not need to be trusted, because the ceiling is not in its hands — that is the point of shipping a limits
file rather than a paragraph of good intentions. The two rules any skill must carry are in
[If you're an AI reading this](#if-youre-an-ai-reading-this).

### Give this to your agent

Point it at the folder — or the clone — and hand it this:

```text
Read README.md in this repo. Follow the Install section for this OS, then the First steps
section. Read limits.json before driving anything. Use k250-status to check the box is
awake, k250-scene to run a pattern, and k250-stop to stop. The stop word is "red".

Never exceed the ceiling in limits.json. It is enforced in the code — do not work around it.
If I say I feel nothing, stop and check the electrodes; never add power. One BLE connection
at a time.
```

**The skill is in the repo, not just described here.**
[`agent-skill/SKILL.md`](agent-skill/SKILL.md) is that skill written out — the two hard rules, the limits
contract, the tool table and the protocol traps. Copy the folder into whatever your agent auto-loads, or
paste it into a system prompt.

## Roadmap / ideas

Things worth building next, in rough order of usefulness:

1. **A browser UI for the box** — one page, no build step: pick a pattern, set base/peak/seconds, hit
   go, with a big STOP button. The engine already speaks plain arguments, so it's a thin wrapper over
   `k250-scene`. Pair it with `limits-form.html` (already done) and a stranger can drive the box safely
   without reading a line of Python.
2. **A pattern composer, and the surface for it.** This project rests on the finding that power and
   frequency are *two* axes, and that the interesting feelings come from how they move relative to each
   other. A composer — "power: flat / climb / drop, frequency: sweep / hold / step, over N seconds" —
   would generate far more patterns than anyone wants to hand-write, and the vocabulary already exists
   in `k250_play.py`. The engine is the hard part; the UI over it is small.
3. **A session log.** Timestamped record of what was run and at what numbers — partly for
   reproducibility, mostly because "what did we do last time" is the hardest question to answer
   afterwards.
4. **A pre-flight checklist** the driver must answer before the first write: pads on where, loops loose,
   no mains, stop word understood, who's in the room. Cheap, and it's the step people skip.

**Shipped since v1.0:** multi-channel verified on two channels · per-channel limits · the three
enforced controls (power / frequency / slew) · the enforced session budget · the limits page · the
pattern-change fix and its regression test.

## Status / honest gaps

- `SB` / `CS` semantics unknown.
- Firmware `v1.08`'s DFU container is encrypted; no plaintext recovered.

## Licence

**Apache-2.0** — see [LICENSE](LICENSE). Copyright 2026 vybesforge.

Why that one: this is a safety tool for a device that can hurt people, and it is deliberately written
to be read, copied and adapted — a `limits.json` you can rewrite for your own body, an operator skill
you can drop into your own agent. Apache-2.0 keeps that open (it is permissive, and it can be used in
closed projects) while adding an explicit patent grant and a requirement that modified files be marked
as changed. If you fork it and change how it clamps power, say so — in the file header, and in a note
to whoever is wearing the pads.

Nothing here is a medical device, and the licence says what every licence says: no warranty. The safety
that matters is in the code (`limits.json` is enforced, not requested) and in the hard stops above.

If this saved you a fortnight of reverse-engineering — the protocol, the pattern vocabulary, the fact
that "I feel nothing" means *less* power — there's a tip cup at
**[ko-fi.com/vybesforge](https://ko-fi.com/vybesforge)**. Nothing here is paywalled, nothing will be,
and the ceiling clamping works identically whether or not anyone ever tips.

## Versions

`main` is always the current release, and every earlier release is kept as a **git tag** — `v1.0`,
`v2.0`, `v3.0`, `v3.1`, `v3.2`, `v3.2.1` … — so an old version is never lost and never in your way:

```bash
git fetch --tags          # get the tags
git tag -l                # list the releases
git checkout v1.0         # go back to a specific one, if you ever need to
git checkout main         # and back to current
```

What changed, and when: **[CHANGELOG.md](CHANGELOG.md)**.

That is all version maintenance is here: tag a release, write down what changed, keep `main`
releasable. Nothing gets deleted, and nothing gets hidden inside the repo.
