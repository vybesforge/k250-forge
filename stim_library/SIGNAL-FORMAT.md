# Imported stim signals — formats & catalogue

Reverse-engineered from the Next.js client bundles + the live estim dictionary.
Everything here is **observation of the public client**, for replicating stims we own.

## Two signal families

The dictionary mixes two device classes. An entry drives one or both:

1. **Audio / serial e-stim** (`estimTower`, some `miscs`) — not BLE. Params:
   `frequence` (carrier Hz), `volume` (dB, negative), `shape` (`sine|pulse|square`),
   `width` (duty), `rampDuration`. Played as audio (e.g. `AAaudio/100a.mp3`) into a
   TENS/serial box (device `2BSerial`, `DIY`). The "signal" is an **audio waveform**.
2. **Coyote V3 (DG-Lab) over Web Bluetooth** — everything with a `sequence`.
   The "signal" is a **10 Hz stream of 20-byte B0 frames** (below).

## Coyote V3 GATT (Civet profile)

| role | UUID |
|---|---|
| service (data) | `0000180c-0000-1000-8000-00805f9b34fb` |
| service (battery) | `0000180a-0000-1000-8000-00805f9b34fb` |
| char write | `0000150a-0000-1000-8000-00805f9b34fb` |
| char notify | `0000150b-0000-1000-8000-00805f9b34fb` |
| char battery | `00001500-0000-1000-8000-00805f9b34fb` |

Name prefix filter (device advertises as the Coyote V3); writes are
`writeValueWithoutResponse`, one every **100 ms** (`setInterval(...,100)`).

## The B0 frame (20 bytes)

Built each tick in `setBT()`:

```
base   = "B0" + "0F" + hex(strA) + hex(strB)      # 4 bytes, header
A      = hex(freqA[4])  + hex(intA[4])             # 8 bytes
B      = hex(freqB[4])  + hex(intB[4])             # 8 bytes
packet = base + A + B                              # 20 bytes = 40 hex chars
```

- `hex(v)` = `v.toString(16)`, zero-padded to 2 chars, **uppercase** (`getHexValue`).
- `"0F"` byte = `binaryToHex("1111")` = constant `0x0F`.
- `strA` / `strB` = channel strength limits (`settings.strA/strB`), the base power ceiling.
- `freqX` / `intX` are **4-tuples**: each BLE write carries a **4-step waveform** per
  channel (4 frequency slots + 4 intensity slots = 4×(freq,int) sub-frames, ~25 ms apart).
  The tuple stream is rebuilt every tick and the index rolls — a moving waveform window.

So one packet = 4 sub-frames of A and 4 of B. Sub-frame cadence 25 ms; packet cadence 100 ms.

## Sequence model (how a named script becomes frames)

`getNamedEstim(name, library, settings)` resolves a name against the dictionary:
- split on `#` → `[path, speedCat, pulseType, speed, delay]`
- split path on `|` → walk the JSON tree; leaf matched case-insensitively against `.name`
- `random#a#b` picks an int in [a,b]

Each stim entry:
```json
{ "channels":["A","B"], "name":"edge_2_pulse_unsynced", "typeEstim":"pleasure",
  "frequence":[1000,1000], "volume":[-21.5,-23.5], "shape":["pulse","square"],
  "effectFrequence":[1,1], "effectDepth":1, "pattern":"sequence",
  "sequence":[ { "baseTime":2, "data":{ "type":"absolute",
      "volume":[ {toVal, rampTime, atTime, cubicBezier, cubicBezierData?}, ... ],
      "frequency":[ ... ] } }, ... ] }
```

Pipeline (`buildSequenceForCoyote` → `getTuples`/`getFrequencyTuples`):
1. `n = baseTime / effectFrequence * 1000/100 * 4` → number of sub-values.
2. Keyframes `{toVal, atTime}` are linearly interpolated across `n` steps
   (step = `0.025 * effectFrequence`); `toVal` may itself be a **pre-baked array**
   (then sampled directly), and `cubicBezier` curves ease the ramp.
3. Values grouped **every 4** into a tuple → tuple array per channel/param.
4. `setBT` writes `base + hex(freqA)+hex(intA)+hex(freqB)+hex(intB)` every 100 ms.

Lower `MA`/frequency = tighter pulse; intensity rides the `volume` keyframes.

## Files here
- `dictionnary_estims.json` — live dictionary (127 stims, 22 categories). Source:
  captured once into `dictionnary_estims.json` (shipped in this folder)
- `estim-presets.json`, `estimAI-presets.json` — `/assets/*.json` presets.
- `stim-catalog.csv` — flattened table: category, name, freq, volume, shape, seq steps, uid.
- `stim_signal.py` — encoder/decoder for the B0 frame; builds packets from curves.

## Replication notes (→ our stack)
- The Coyote signal is (frequency, intensity) keyframes → maps naturally onto our K250
  `MA` (frequency) + `PW` (power) model. A translator feeds the same keyframe timeline
  through the K250 engine.
- **Wearer limits win.** Anything ported must clamp to `limits.json`, never exceed it.
- Not yet captured: the AI-generated stims (`/api/estimAI/getIntelligentStim`) and the
  `Session?script=<name>` scenario script (server-side, behind login) — those are dynamic.
