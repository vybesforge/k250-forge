# Personas

One JSON per persona. Copy `TEMPLATE.json` to `<slug>.json` and edit.
The slug is the identity: it namespaces the persona's images in `../assets/`
and its persisted state.

## Generation modes (`gen.mode`)

| mode | engine | identity comes from | use for |
|---|---|---|---|
| `t2i` | `pg_lora_t2i` (with or without `lora`) | prompt_seed + LoRA trigger words | personas defined by a character LoRA or pure prompt |
| `i2i` | `pg_img2img` | a reference plate in `assets/` (`gen.reference`) | photoreal personas anchored on one face; denoise ~0.4–0.6 |
| `edit` | `pg_qedit` (Qwen-Image-Edit) | a reference plate, instruction-edited | biggest pose/scene changes while identity holds; needs Qwen-Image-Edit models installed in ComfyUI (GGUF variants run on older GPUs) |

Notes:
- `checkpoint: null` → the config default is used.
- LoRA filenames must match the ComfyUI install EXACTLY, extension included.
- i2i at high denoise drifts; for real pose variety use `edit`.
- Every render lands in `assets/` as `plates_<slug>_<timestamp>.png` and the
  newest plate is what the portrait frame serves — each generation overwrites
  the last in place.

## Expressions (optional)

Drop `assets/expr_<slug>_<mood>.png` files (moods: `calm`, `stern`, `pleased`,
`hungry`, `soft`, `cruel`) and the page crossfades between them as the chat
mood shifts. Missing moods 404 silently and the current look stays. Generate
them from your reference plate with a low denoise and expression-only prompts.

## What the server appends automatically

The personality text is only the character. The server appends the shared
mechanics: reply style, `[[stim ...]]` grammar (with live ceilings), optional
channel-2 fields, `[[image ...]]` grammar, and the stop-word rule. Do not put
tag instructions in the personality.
