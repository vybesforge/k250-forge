# K250-4S protocol reference (measured against real hardware)

BLE device name: `Kx250-4S`. Random/static LE address — it changes on every
power-cycle, so never record or hardcode a MAC; discover by name.

Radio SoC: Nordic nRF52840 (Raytac MDBT50Q module).

## Host → device writes (compact JSON over the write characteristic)

| key | meaning | value |
|---|---|---|
| `PW` | Power, applies to the SELECTED channel (`AC`) | `"0".."10000"` on v1 (percent×100); percent×1 on v2 |
| `MA` | Multi-Adjust = FREQUENCY axis (0 = fast buzz, max = slow thump) | `"0".."10000"` on v1; apex ~100 on v2 |
| `PA` | Pattern slots, array of 4 | e.g. `["Waves","UNPLUG'D","UNPLUG'D","UNPLUG'D"]` — v2 REFUSES writes |
| `AC` | Selected/active channel index | `"0".."3"` — refused for unplugged channels |
| `MP` | Master power cap (box UI `L-05`..`L-100`) | `"5".."100"` — caps every channel regardless of software |
| `CA` | Per-channel status array | `["Active","Unplugged",...]` — request explicitly with `{"CA": ""}` |
| `GP` | Pattern list (dropdown source) | `["Climb","Combo","Intense","Manual","Orgasm","Rhythm","Stroke","Torment","Waves"]` |
| `FV` | Firmware version (read-only) | e.g. `"2.00.09--v2.00.09"` |
| `BC` | Battery % | integer |
| `ER` | Error code | `"0"` normal |

Device → host: notifications echo each accepted write as a one-key JSON object
(`STATE: {"PW": 45}` etc.). A read-all returns the combined state object.

## Per-firmware axes (getting these wrong fails SILENTLY)

| fw | PW units | MA apex | PA writable |
|---|---|---|---|
| v1.x | percent × 100 | 10000 | yes |
| v2.x | percent × 1 | ~100 | **no** (pattern is set by hand on the box) |
| unknown | driver REFUSES to run | — | — |

Unknown firmware must never inherit v1's ×100 scale: on v2 hardware that pins
the box to its top output. The engine fails safe (smaller units) and the driver
refuses outright.

## Channel semantics

- `PW`/`MA` writes apply to the channel currently selected by `AC`. Multi-channel
  driving = alternate `AC` then write, serialized so select+write is atomic.
- The box HOLDS `PW` and `MA` per channel once written (no refresh needed; a
  ~2 s keepalive re-send is harmless insurance). A PATTERN CHANGE (`PA`, v1 only)
  zeroes that channel's power and frequency.
- A channel with a BLANK pattern slot rejects power writes (echoes `{"PW": 0}`).
- Plug detection: **only `CA` is authoritative.** `PA` slots can read `UNPLUG'D`
  for a channel whose `CA` reads `Active` (verified live). `CA` is not part of
  read-all — request it with `{"CA": ""}` and read it from the reply.
- On v2 the `PA` pattern NAME is unreadable as well: every slot reports `Rhythm`
  whatever is selected on the box (verified 2026-11-20, fw `2.00.09`). `PA` can
  therefore confirm neither plug state nor the chosen pattern — ask the wearer.
- `AC=n` is refused while channel n is unplugged.

## Connection lifecycle notes

- The box advertises only while awake with the LCD on (Options → Remote App
  Control). A sleeping box is invisible: not connected ≠ off.
- After connecting, subscribe to notifications, send read-all, and WAIT for the
  `FV` field before trusting any axis (early partial replies can be empty).
  Re-ask every ~3 s for up to ~12 s before declaring a read timeout.
- One BLE client at a time. Two concurrent drivers corrupt each other's channel
  selection. Ownership must be checked before connecting (see link_status).
- Host support: Linux (BlueZ), Windows (WinRT through bleak) and macOS — all three
  verified against hardware. `link_status.py` answers the same two questions on
  each: running driver processes (/proc, the CIM process list, or `ps -eo`) and
  whether the box is connected (`bluetoothctl devices Connected`,
  `Get-PnpDevice -PresentOnly -Class Bluetooth`, or the `Connected:` group of
  `system_profiler SPBluetoothDataType`). On Windows a Bluetooth PnP node exists
  only while the peripheral is CONNECTED, so that listing is the live-link signal;
  a non-paired Web Bluetooth link is invisible to it.
