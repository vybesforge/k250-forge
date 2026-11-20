# vfk250 web — companion app

A single portable folder: one HTML page, one FastAPI bridge, no build step.
Copy it to any machine, point `config.yaml` at that machine's services, run:

```bash
python3 -m venv .venv && .venv/bin/pip install fastapi uvicorn httpx pyyaml python-multipart
VK250_PY=~/.vfk250/bin/python .venv/bin/python server.py
# open http://localhost:6868
```

`VK250_PY` must point at a python with **bleak** installed (the K250 driver
runs as a detached subprocess with that interpreter).

## What it does

- **Safety gate first** — the page opens on an e-stim safety warning and stays
  dark until the wearer ticks "I understand and accept". The gate hides itself
  and re-arms on every page load. Nothing drives until then.
- **Chat** with a local LLM (any OpenAI-compatible endpoint; candidates are
  probed in order and the winner cached). The active persona can drive the box
  straight from conversation with machine-read tags:
  - `[[stim level=40 pattern=swell secs=300]]` — and optionally
    `level2=12 pattern2=tide` for the second channel.
  - `[[image one-line shot description]]` — renders a new portrait of the
    persona (ComfyUI optional).
  Tags are parsed tolerantly and **validated server-side**: unknown patterns
  are dropped, levels clamp to the page ceilings, seconds clamp to 30–600.
- **Per-channel ceiling sliders** — the page is the ceiling authority
  (persisted in `.ceilings`, loaded on boot, never clobbered by client
  defaults). First-run defaults are deliberately conservative (CH1 10%,
  CH2 5%); raise them deliberately.
- **Manual drive panel** — pick "User" under *Who drives* and you get direct
  controls: pattern dropdown, level slider (starts at 5%), duration, Start.
  Pick the persona and chat takes the dial instead.
- **Settings dialog** — click the header's status indicators (⚙) to edit every
  service URL (LLM/vision candidates, TTS, STT, ComfyUI) from the page.
  Overrides persist to `config.local.yaml` (gitignored) and hot-apply without
  a restart, so `config.yaml` stays a clean template.
- **Stim queue** — a command sent while a block is running queues behind it
  (max 3) and fires only when the link is genuinely free. STOP drops the queue:
  after a stop, nothing fires.
- **External-driver awareness** — blocks launched before a server restart are
  picked up from the process table (pattern/level/remaining reconstructed), so
  the panel always tells the truth.
- **Personas** (`profiles/*.json`) — each has its own personality, accent
  color, greeting, voice, and image-generation recipe (t2i / i2i-off-a-reference
  / Qwen-Image-Edit instruction edits / LoRA characters). Switching personas
  recolors the page, renames everything, resets the chat to their greeting,
  and auto-renders their first portrait if they don't have one.
- **Portrait frame** — fixed frame, each new generation overwrites the last in
  place; expression states crossfade under it as the persona's mood shifts.
- **Webcam → VLM** (optional) — when the toggle is ON, frames go to a local
  vision model every 3 s and the description rides along in the persona's
  context. When OFF, nothing is captured and the stored summary is wiped
  server-side. Off means off.
- **Voice** — mic (STT) in, TTS out (🔇/🔊 button), both optional services.

## Layout

```
web/
  server.py        FastAPI bridge (chat, stim, queue, profiles, image gen, tts/stt/vision)
  index.html       the whole UI (no build step, no npm)
  config.yaml      EDIT THIS: service URLs, ComfyUI settings, script paths, port
  profiles/        one JSON per persona (+ TEMPLATE.json)
  assets/          per-persona image storage (see assets/README.md) — ships empty
  helpers/         optional ComfyUI generation helpers (stdlib-only, any python3)
```

## K250 wiring

`config.yaml → k250` points at the skill scripts (`../skill/vfk250/scripts/`
by default). The safety contract in the root README applies unchanged: the
page's sliders are the ceilings, `red` stops immediately and drops the queue,
and a busy link is never competed with.

## Optional services

Everything degrades gracefully: no ComfyUI → no portraits (chat/stim still
work); no TTS/STT → voice buttons inert; no VLM → webcam frames are described
by nothing and ignored. Health dots in the header show what's live.

## Notes

- No authentication — run it on a trusted LAN or tunnel it yourself.
- Persona portraits are **not** shipped. Bring your own reference plates or
  let a persona render its first portrait from its `portrait_prompt`
  (needs ComfyUI + a checkpoint). See `profiles/README.md`.
