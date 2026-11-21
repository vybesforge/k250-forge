---
name: vfk250
description: "Use when driving a Kink K250-4S e-stim box over BLE, single or dual channel."
version: 2.0.0
author: vybesforge
license: Apache-2.0
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [k250, e-stim, ble, hardware]
---

# vfk250 — K250-4S control (lean, V2)

## When to Use

Load this skill whenever a Kink K250-4S e-stim box must be driven, stopped, or
status-checked over BLE — scenes, manual blocks, dual-channel work, the vfk250
companion web app, or diagnosing link/ownership/channel state.

Self-contained driver for the Kink K250-4S 4-channel e-stim box over Bluetooth LE.
Engine layer from the k250-forge project (Apache-2.0, see NOTICE). V2 differences:
one driver code path for one or two channels (`k250_play2.py`), ceilings come from
the caller (the wearer's word or the companion web page) instead of a limits file,
and plug detection uses the authoritative `CA` array. The companion GUI lives in
this repo under `web/` and works outside Hermes.

## Setup (once per machine)

```bash
# Linux / macOS
python3 -m venv ~/.vfk250 && ~/.vfk250/bin/pip install bleak
export VK250_PY=~/.vfk250/bin/python     # the driver needs bleak; run_block honours this
```

```powershell
# Windows (PowerShell) -- same venv, different bin dir
py -m venv $HOME\.vfk250
& "$HOME\.vfk250\Scripts\python.exe" -m pip install bleak
$env:VK250_PY = "$HOME\.vfk250\Scripts\python.exe"
```

- **Windows is a supported host.** BLE runs on bleak's WinRT backend (verified against
  a live `Kx250-4S`: discovery, GATT connect, notifications and state reads all work).
  `link_status.py` and `k250_stop.py` reach for PowerShell where Linux uses `/proc` and
  `bluetoothctl`, and the launcher detaches playback with a Windows process group.
  Point `VK250_PY` at the venv's `Scripts\python.exe` — there is no `bin/python`.
- **macOS is a supported host** — verified against hardware, like Windows. It has no
  `/proc` (`link_status.py` falls back to `ps -eo`) and no `bluetoothctl` (it reads the
  `Connected:` group of `system_profiler SPBluetoothDataType`). If it ever reports
  `UNKNOWN`, that probe is the first thing to check.

- Box: powered on, LCD on **Options → Remote App Control** (that is when it advertises).
- Pads: channel 1 only by default, loop complete, seated. Channel 2 only per the dual rules below.
- Never hardcode a MAC: the BLE address is random per power-cycle; discovery is by the name `Kx250-4S`.

## The contract (non-negotiable)

1. Get a CEILING (percent) from the wearer **in this session**. None stated → ask once and do not drive. Pass it as `--cap`. Numbers from old sessions are not permission.
2. Channel 1 is the only channel unless the wearer says channel 2 is wired **and** placed. Current must never cross the heart or chest. No mains, no chest path, ever.
2b. **The box's hardware Maximum Power Level (MP) is the authoritative cap** — have the wearer set it to their true maximum BEFORE any AI-driven scene. Software ceilings and driver clamps are defense in depth, not a guarantee: an AI can make mistakes. (The web stack is tested down to small local models — testing is not a guarantee.)
3. The stop word (default `red`) is absolute: run stop ONCE and verify a fresh zero echo (`STATE: {"PW": 0}` in the log). Unreachable does NOT mean off — have the wearer power the box off physically (hold any knob ~2 s).
4. One BLE client at a time. `link_status.py` reports ownership; BUSY/BLOCKED means an owner exists, never that output is zero. Never start a second driver or reset the adapter while output is uncertain.
5. A failed launch ends the attempt: report once, stop. No retries, no escalation, never raise power to diagnose silence.
6. The launcher returns immediately while playback runs detached. Never wrap it in `timeout` or pipe it to `head`; that kills long scenes early.

## Drive

```bash
python3 scripts/run_block.py --cap CAP --level LVL --secs SECS --pattern tide
```

- Prints one JSON record `{pid, log, state: STARTING_NOT_VERIFIED, ...}` plus a LAUNCHED line. The launcher exiting is NOT the end of playback.
- Read the log once: expect `PLAY <pattern> base=...%`, `PW` echoes while it runs, and at the end fresh `STATE: {"PW": 0}` lines with `all zeroed (ended)`. Only that fresh zero supports a "stopped/zero" claim.
- **66 patterns** ship in the engine. Authoritative name list: `scripts/k250_codec.py` → `PATTERN_NAMES` (stdlib-only; the launcher pre-validates against it, so an unknown name is REFUSED before launch). All names — no lookup needed:
```
  arc, ascend, bounce, buzzline, climb, compress, cooldown, crawl, creep, 
  crescendo, dice, dread, drift, dual, edge, edging_continuous, 
  edging_pulse, edging_wave, engulf, fangs, flat, flick, friction, groove, 
  heartbeat, heavy, high_sweep, hr_follow, jelly, knead, lie, lift, 
  light_pulse, light_stutter, map, metronome, milker, mirror, pain_edge, 
  pain_shocks, pain_slow_ramp, pain_stutter, power_sweep, power_sweep_rich, 
  pulse, rain, ration, ripple, signature, sound, speed_step, speed_sweep, 
  stutter, sweep_drop, sweep_hold_zero, swell, switchback, tease, 
  teasing_continuous, teasing_pulse, teasing_stutter, thrust, tickles, 
  tide, trap, verge
```
- Optional envelope: `--base B --peak P` override the flat `--level` (both ≤ cap; the JSON record echoes level, base and peak).
- Bounds enforced in launcher AND driver: `0 < level <= cap <= 100`, `1 <= secs <= 3600`.

## Dual channel (V2)

Same launcher, add the ch2 fields — `k250_play2.py` is the single code path for one or two channels:

```bash
python3 scripts/run_block.py --cap 50 --level 30 --secs 300 --pattern swell \
                             --cap2 25 --level2 12 --pattern2 tide
```

- Each channel gets its OWN waveform frame (pattern/base/peak) and its OWN ceiling; ch2 never inherits ch1's numbers. AC-select + PW/MA writes are atomic under one lock, so a value can never land on the wrong channel.
- Preconditions (the driver verifies and REFUSES both channels rather than degrading silently):
  - ch2 must read **Active** in `CA` (pads plugged).
  - Each driven channel's pattern slot must be set to **Manual by hand on the box**: v2 firmware refuses pattern (PA) writes over BLE; a blank slot rejects power (echoes `PW: 0`); a patterned slot runs the box's own generator on top of yours.
- Start low (≈10%) on a freshly wired channel and verify per-channel echoes in the log before raising anything.

## Stop

```bash
"$VK250_PY" scripts/k250_stop.py
```

- Kills any running `k250_play*` driver, then zeroes every plugged channel and prints a `STOPPED:` line naming the channels it wrote.
- Exit 1 / no zero echo = UNCONFIRMED → physical power-off (hold a knob ~2 s). Never SIGKILL a driver yourself: the box holds its last power with no dead-man switch.
- **Link drop mid-run**: `k250_play2.py` catches the dead link during shutdown, makes ONE fresh reconnect attempt to zero every driven channel, and exits **4 = ZEROING UNCONFIRMED** if that also fails (the log then carries the physical power-off instruction). Treat exit 4 as an unconfirmed stop, never as success.

## Status / ownership (read-only; never touches BLE)

```bash
python3 scripts/link_status.py
```

IDLE → safe to launch (or make one direct probe). BUSY/CONFLICT → report the existing owner and do nothing. A stale cache is not live evidence; a link is not proof of output.

## Firmware & protocol traps

- v2.x: PW is percent×1, MA (frequency axis) apexes ~100, PA writes refused (pattern set by hand). v1: PW is percent×100. **Unknown firmware → the driver refuses** (fail safe). Details: `references/protocol.md`.
- Plug status is **CA**, which must be requested explicitly (`{"CA": ""}`) — it is not in read-all. **PA slots lie**: verified live `CA=['Active','Active',...]` while `PA=['Rhythm',"UNPLUG'D",...]`.
- **On v2 firmware the `PA` pattern NAME is unreadable too.** Every slot reports `Rhythm` no matter which pattern is actually selected on the box — verified 2026-11-20 on fw `2.00.09` (box set to another pattern by hand, `PA[0]` still `Rhythm`). So `PA` can confirm neither plug state nor the chosen pattern. Never treat a `Rhythm` reading as "the box is on Rhythm", and never use it to verify the wearer's selection — ask them.
- `MP` is the box's own master cap (L-05..L-100) and limits every channel regardless of software. Ask the wearer what it is set to; don't assume.
- BlueZ wedge after a crashed scan (`org.bluez.Error.InProgress`): full `bluetoothctl power off && bluetoothctl power on` cycle; the HCI number can re-enumerate.
- **Windows**: there is no BlueZ to wedge. Ownership comes from the CIM process list, and "is the box connected" from `Get-PnpDevice -PresentOnly -Class Bluetooth` — a Bluetooth PnP node exists only while the peripheral is CONNECTED, so that listing is the live-link signal (the host's own controllers/enumerators are present too and never match `Kx250`). A non-paired Web Bluetooth link is invisible to it. `k250_play2.py` installs its SIGINT/SIGTERM handlers via `signal.signal` (asyncio's `add_signal_handler` is unsupported on Windows), and `k250_stop.py` terminates pattern processes — `TerminateProcess` runs no exit handler, which is why the `PW=0` writes that follow it are the ones that matter.

## Companion web app

`web/` — FastAPI bridge + single-page UI for use outside Hermes: chat personas that drive the box via `[[stim]]` tags, per-channel ceiling sliders (the page is the ceiling authority), a queue that never stacks commands blindly, and optional portrait generation. See `web/README.md`.
