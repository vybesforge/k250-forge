# Kink K250-4S — BLE takeover: findings & assets

Saved 2026-09-14. Device physically attached to the bench machine; a webcam points at its LCD.

## 1. The device
- **Kink K250-4S E-Stim Power Box** (Kink Store / Red System Ventures LLC; kinkstore.com).
  4 independent bipolar e-stim channels, 4 knobs, touchscreen, USB-C charging, "upgradable firmware".
- BLE advertises as **`Kx250-4S`** — random/static LE address (it changes on power-cycle, so it is
  not something to record), RSSI ≈ −50 at this box. Device is powered on and advertising when the LCD is on.
- **Radio SoC: Nordic nRF52840** — carried module is a Raytac MDBT50Q; FCC ID **`SH6MDBT50`**
  (grantee SH6 = Raytac; MDBT50Q series is nRF52840, BLE 5.4). Good news: nRF52840 = 1 MB flash,
  SWD debug, well-documented toolchain.
- Device firmware string: **`v1.08--18c4987-250114-01hGMT`** (v1.08, build 18c4987, 2025-01-14).
- User manual (v2.0, 2024) Google Doc — saved as `manual.txt`:
  `https://docs.google.com/document/d/1PNQ_AMPMlGZ1YDRcaUjfWJxGBN_61kI7MN2dPukY5c8/export?format=txt`
- Store page: https://www.kinkstore.com/products/estim-power-box-by-kink  (out of stock; $429)
- Companion app: **Konnector** (konnector.com, Web Bluetooth app; Android `us.konnector.app`;
  iOS id6449022393). APK mirror in manual: `https://s3.us-west-2.amazonaws.com/konnector.live/apk/app15.apk`
  → saved as `apk/konnector-app15.apk` (280 MB).
- E-stim community Discord (from manual): https://discord.gg/pcsPCwZqFK ("meet the inventor").

## 2. USB
- **USB-C port does NOT enumerate** on Linux: no new device in `lsusb`/dmesg/journal for the whole
  session; no `/dev/ttyACM*`. Cable in use may be charge-only, or the port is charge-only while
  bright. Manual: "use the included USB Type-C charging cable when charging **and when updating the
  firmware**" — but the documented update path is **BLE** (Konnector in Chrome/Edge). Do not count on
  USB data; all work done over BLE.

## 3. BLE GATT
- Advertised + primary service: **`086e0000-7935-0d3a-ca91-bfb0c8c34043`**
- Single characteristic: **`086e0001-7935-0d3a-ca91-bfb0c8c34043`**
  properties: read, write, write-without-response, notify (CCCD `00002902`).
- Reading the characteristic returns a constant 4 bytes: `5d4e7cb7` (purpose TBD — maybe a
  handshake/version token).
- Standard GATT `0x1801` (Service Changed, indicate) also present.
- Web app pairs with `filters: [{ services: ["086e0000-7935-0d3a-ca91-bfb0c8c34043"] }]`.

## 4. Control protocol (runtime) — JSON text on the same characteristic
Host → device (write, compact JSON; app debounces 300 ms trailing):
| key | meaning | value |
|---|---|---|
| `PW` | Power (per selected channel) | **`"0".."10000"`** (×100 scale) |
| `MA` | Multi-Adjust = FREQUENCY (0 = high freq/buzzy, max = slow thump) | **`"0".."10000"`** (×100 scale) |
| `PA` | Pattern per channel | JSON array of 4 pattern names, e.g. `["Waves","UNPLUG'D","UNPLUG'D","UNPLUG'D"]` |
| `AC` | Selected/active channel index | `"0".."3"` |
| `MP` | Maximum power level (safety cap) | `"5".."100"` → UI "L-05".."L-100" |
| `CA` | per-channel status array | e.g. `["Active","Unplugged",...]` |
| `GP` | pattern list (dropdown source) | `["Climb","Combo","Intense","Manual","Orgasm","Rhythm","Stroke","Torment","Waves"]` (confirmed live) |
| `FV` | firmware version (read-only) | `"v1.08--18c4987-250114-01hGMT"` |
| `ER` | error code | `"0"` |
| `SB`,`BC`,`CS` | observed in the app's read-all template; semantics TBD |

Read-all query (what the app sends to sync) = all keys empty:
```json
{"AC":"","PW":"","MA":"","GP":"","PA":"","CA":"","MP":"","SB":"","BC":"","CS":"","FV":"","ER":"0"}
```
Device → host (notify): pretty-printed JSON, sometimes with unquoted numbers, sometimes split over
several notifications (app reassembles with a 60 ms debounce, strips `\n`/`\r`). Live sample:
```json
{"AC":0,"MA":0,"PA":["Waves","UNPLUG'D","UNPLUG'D","UNPLUG'D"],"MP":100,"BC":12,"FV":"v1.08--18c4987-250114-01hGMT"}
```
(`BC:12` is probably battery / box-config; ch1 has pads attached, ch2-4 unplugged → "UNPLUG'D".)

**VERIFIED LIVE (2026-09-14, from Python over BLE):** read-all works (`k250_ble.py read`) and writes
apply — `set MP=90` → device echoed `{"MP": 90}`, read-back confirmed `MP: 90`; restored to 100.
`set AC=1` (select ch2) was **refused while ch2-4 are unplugged** — matches the app disabling
unplugged channel tabs; channel selection needs a plugged channel. `BC` = battery % (12 → 40 while
charging on USB). ⇒ **we are in control of the box over BLE.**

App flow on connect: `startNotifications()` → send read-all → parse updates.
App write flow: debounce 300 ms; writes flagged "sync" are followed 100 ms later by a read-all.

## 5. Firmware update (DFU) protocol — same characteristic, custom framing
- Firmware pointer (public S3):
  `https://s3.us-west-2.amazonaws.com/konnector.live/box-firmware/config.json`
  → `{"full_path":"https://s3.us-west-2.amazonaws.com/konnector.live/box-firmware/estimbox_fw_v1.08_dfu.bin",
      "force_update":false,"version":"1.08"}`
  (older `konnector.s3.us-west-1.amazonaws.com/box-firmware/firmware.bin` → 403 now)
- **Saved image**: `firmware/estimbox_fw_v1.08_dfu.bin` — 1,860,932 B,
  sha256 `13cba3dbfc92c0fffa7adca1dbf6ac9d15f9dd79f0cea72099d4ada63ba8567f`.
- **Container format** (20-byte header + payload):
  * `0..3`  : `b4 46 68 04` magic
  * `4..11` : ASCII `eStim250`
  * `12..13`: `08 01` (version: 1.08; cmd 242 sends these two bytes **swapped** → `01 08`)
  * `14..15`: `00 01`
  * `16..19`: u32 LE payload size = 1,860,912
  * `20..`  : payload — entropy **7.9999 bits/byte ⇒ encrypted** (not plaintext code).
- **Message framing** (host→device): body = `[u16LE cmd][u16LE seq][u16LE argLen][payload]`,
  then a zero-run-length codec (same one used for device→host notifications):
  * encode: output = `00` + runs (`count = runLen+1` then the run bytes) + `00`;
    a zero byte is represented by count 1; count 255 = 254 run bytes with no implied zero.
  * decode: read count c → copy c−1 bytes → if c<255 emit a 0 byte.
  * write in chunks of ≤240 B (data blocks: ≤247 B) with 100 ms between writes
    (one app build used 1 ms for data blocks).
- **Commands**: `240` = start (empty, arg 0) · `241` = total size (u32 BE, arg 4) ·
  `242` = version bytes (arg 2) · `32` = data block (payload ≤247; if block >231 use 235) ·
  `33` = finish (empty, wait 5 s).
- **ACK**: device echoes `seq`; the app decodes notifications and reads `u16LE @ offset 3`;
  it waits (10 ms polls) until acked ≥ current seq, retries the block at ≥500 ms, aborts at ≥1 s.
- Box side: Options → top-right "Download to Device" button → **Firmware Update Mode**; then pair
  from the site/app. Estimated duration ~6 min; **factory reset required after** (power off, hold
  both center knobs 4–10 s, 50 s routine; firmware version is not reset).
- Web routes: `/your-device/Kinky-Box` (control) · `/update-kinky-box` and
  `/your-device/Kinky-Box-Update` (DFU UI).

## 6. Recon assets (author's box)

These were captured on the machine the protocol was reverse-engineered from and are **not** in this
repo — they're listed so the record is complete and so anyone repeating the work knows what they're
looking for. Paths in this section are relative to that working directory, not this repo:
```
manual.txt, manual_mb.html, manualpage.html      # user manual (txt + google-doc html)
konnector/*.js  +  konnector/pretty/*.js         # all konnector.com web-app chunks (raw + beautified)
firmware/estimbox_fw_v1.08_dfu.bin, config.json  # current firmware image + release pointer
apk/konnector-app15.apk                          # Konnector Android app (280 MB) for later decompile
scan_connect.py   # bleak: scan + connect + full GATT dump + notify capture
k250_read.py      # bleak: connect + send read-all + parse JSON reply
k250_codec.py     # wire codec (zero-run-length) + constants; self-test: venv/bin/python k250_codec.py
k250_ble.py       # driver: read | monitor | set KEY=VAL | raw '<json>'   (usage in file header)
vesc_probe.py     # early probe (VESC hypothesis — negative, kept for the record)
extract.py, beautify.py, fetch_missing.py, fw_analyze.py   # tooling
```
`venv/` — Python venv (bleak, jsbeautifier) used by all scripts.

## 6b. Runbook (resume here)
1. **Wake the box**: press any knob ~1 s (side LED glows red). Keep it charging —
   the last state read said `BC:12` (battery low-ish) and it dropped off BLE shortly after.
2. `cd <working dir> && venv/bin/python k250_ble.py read`  → expect a `STATE: {...}` JSON line.
3. Benign write check: `venv/bin/python k250_ble.py set AC=1` then `set AC=0`
   (AC = selected channel tab only, no output). Avoid PW/MA until we deliberately test output.
4. Screen check: `ffmpeg -y -f v4l2 -input_format yuyv422 -video_size 1920x1080 -i /dev/video0
   -frames:v 25 -f image2 -update 1 /tmp/k250_last.png` → screen ≈ crop `470:650:700:320`.
5. BLE drops when the box sleeps/screen-off ⇒ always wake it first.

## 6c. Manual-mode pattern engine (2026-09-14/15 session)

Control map, confirmed live against the LCD (2026-09-14/15):

| control | key | scale | notes |
|---|---|---|---|
| Power Multiplier | `PW` | 0..10000 = 0..100% | the *only* continuous control in Manual mode |
| Multi Adjust | `MA` | 0..10000 | **frequency**: `0` = highest/buzziest, `5000` (~50%) ≈ **1 thump/sec**, max = slowest thump |
| Pattern | `PA` | 4-name array | per-channel pattern name; setting it leaves `PW`/`MA` alone |
| Max power | `MP` | 5..100 | system cap, shows as `L-05..L-100`; applies even when output keys look dead |

Tooling (all in the working directory — the repo root if you cloned it, all speak **percent** and multiply by 100 internally):
- `k250_ctl.py` — live controller holding the link open, driven via `ctl.fifo` (`{"PW":"3000"}`,
  `read`, `w0 <json>`, `stream <json> <secs> <ms>`, `quit`). Auto read-all after every write.
- `k250_play.py <pattern> --base --peak --secs --hardcap` — pattern engine.
- `k250_show.py --set tour|new --hardcap N` — plays a setlist in ONE connection with gaps.

Pattern notes from testing: **edge** (hovers under, then pushes over) got used most;
**climb** (sawtooth, snaps back) and **tide** both landed; **stutter** is intense and
hard to settle into, but it has its place. `trap` ran fine
(escalating 25→40% with unpredictable dead zones). New this session: **verge** (edge's push
arrives in thump mode), **groove** (MA pinned 1/s, power surging on the beat),
**switchback** (climb whose snap-back is three heavy beats, not relief).

**The Reverse Polarity Switch cannot be driven over BLE** — it's box-only (manual line 167).
Design pattern "beats" around it and have the operator flip by hand. `SB` is read-only;
`CS`/`BC` semantics still unknown (`BC` = battery %).

**What testing established (one box, one placement — observations, not transferable numbers):**
- **`MA` is a full second axis, not a garnish.** Power held *dead flat* on the LCD while `MA`
  walked 0→2500 changed the sensation completely. `MA=0` = buzz, `MA≈2500` a comfortable top for
  most work; past ~3500 it thins out. Slow beats (~1/s) need materially MORE power than `MA=0`
  to feel equal — compensate power upward as `MA` rises.
- **Speed sweep is the favourite motion:** power held FLAT at **38%**, `MA` sine-sweeping 0↔25.
  Sweet spot for sweep period is **8-20 s** (~13 s ideal); 4 s is too brisk.
- **The hard drop is the money moment:** `MA` going 25 → 0 in ONE step (not a glide). Both the
  staircase wrapping around and the deliberate `sweep_drop` hit it. Sitting at the top before
  the drop makes the landing better.
- **The working power band ≈ 35-50 %.** Above ~50 % reads as pain, which is sometimes wanted, so
  `pain_edge` deliberately steps 6-10 % above the window for 1.5-3 s and falls back.
- **Compositions win over single motions:** `arc` = sweep ×2 → climb/sit/hard drop → cooldown hum
  (`MA=0` at ~72% of base power) → sweep hotter. Holds up across repeats; runs to 50% fine.
- Worth keeping: **edge/verge**, **switchback**, **climb**, **tide**, **speed_sweep**; **stutter**
  (intense but good); **groove/metronome** felt flat (fixed beat, needs more power); **creep** is the
  bug case below.

## 7. Next steps
1. **Confirm write path** with a benign setting change and eyes on the LCD (webcam) —
   then it's "controlled over BLE".
2. Wrap `k250lib.py` into a bridge (the same pattern used for the Coyote box) so an AI can drive
   power/pattern/MA per channel; add safety caps (MP level) and a hard stop.
3. Firmware recovery attempts (later, if anyone takes it on): the image is encrypted on the wire; options are
   (a) SWD dump of the nRF52840 (open case, check APPROTECT), (b) reverse the bootloader crypto,
   (c) craft our own DFU image and see if the box enforces only the version bytes. Also mine the
   APK for the DFU implementation.
4. Keep the device in **Firmware Update Mode** only when doing DFU work; never leave it there.
