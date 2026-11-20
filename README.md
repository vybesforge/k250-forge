# vfk250

**Drive the Kink K250-4S e-stim box from an AI agent or a browser** — single or
dual channel, with hard safety rails.

Two halves, one repo:

| | what | who it's for |
|---|---|---|
| [`skill/vfk250/`](skill/vfk250/) | A [Hermes Agent](https://hermes-agent.nousresearch.com/docs) skill: lean V2 driver manual + self-contained scripts | any Hermes profile — drop it in the skills directory and the agent can drive the box |
| [`web/`](web/) | Companion web app: FastAPI bridge + single-page UI (chat personas, per-channel ceilings, stim queue, portrait generation) | anyone — runs standalone, no Hermes required |

The engine layer (BLE client, codec, ~66-pattern waveform engine, stop) comes
from [k250-forge](https://github.com/vybesforge/k250-forge) (Apache-2.0, see
[NOTICE](NOTICE)). V2 adds:

- **One driver, one or two channels** — `k250_play2.py` gives every channel its
  own waveform frame and ceiling; AC-select + power writes are atomic under one
  lock, so a value can never land on the wrong channel.
- **Caller ceilings** — the cap comes from the wearer's word this session (or
  the web page's sliders), enforced in both the launcher and the driver. No
  limits file to drift.
- **Honest channel detection** — plug status is read from the box's `CA` array
  (the `PA` pattern slots lie about it; see the protocol reference).
- **Fail-closed everywhere** — unknown firmware, unplugged requested channel,
  busy link, bad arguments: refuse, report once, never retry, never degrade
  silently.

## The safety contract (both halves)

1. A ceiling is obtained from the wearer **per session**; nothing drives without one.
2. Channel 1 only unless the wearer says channel 2 is wired and placed. **Current never crosses the heart or chest.**
3. The stop word (default `red`) is absolute: stop once, verify a fresh `PW=*** echo; if the box is unreachable it is NOT off — power it off physically (hold any knob ~2 s).
4. One BLE client at a time; a busy link is reported, never competed with.
5. A failed attempt is reported once. No retries, no escalation, never raise power to diagnose silence.

## Quick start

```bash
# driver environment (bleak)
python3 -m venv ~/.vfk250 && ~/.vfk250/bin/pip install bleak
export VK250_PY=~/.vfk250/bin/python

# read-only ownership/link check (never touches BLE unless IDLE)
python3 skill/vfk250/scripts/link_status.py

# one bounded block: 30% for 5 minutes, ceiling 50
python3 skill/vfk250/scripts/run_block.py --cap 50 --level 30 --secs 300 --pattern tide

# stop (kills drivers, zeroes every plugged channel, prints confirmation)
"$VK250_PY" skill/vfk250/scripts/k250_stop.py
```

Full operating manual: [`skill/vfk250/SKILL.md`](skill/vfk250/SKILL.md).
Protocol details and firmware traps: [`skill/vfk250/references/protocol.md`](skill/vfk250/references/protocol.md).
Web app setup: [`web/README.md`](web/README.md).

## Hardware

[Kink K250-4S](https://kinkstore.com) 4-channel e-stim power box, firmware
v1.x/v2.x, BLE name `Kx250-4S`. Linux + BlueZ. Nothing here is medical advice;
the wearer owns every limit.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
