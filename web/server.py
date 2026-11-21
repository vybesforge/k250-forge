"""vfk250 web — companion app for the vfk250 K250 driver skill.

One HTML page, one persona, the dial. Serves the UI and proxies:
  - chat  -> local LLM (probes candidates, caches winner)
  - tts   -> Chatterbox :3200
  - stt   -> Whisper :3201
  - k250  -> run_block.py / k250_stop.py helpers (spawn, never hold)
  - image -> ComfyUI router :8188 (pg_lora_t2i style via helper script)

Run: python3 server.py  (uses system python with fastapi+uvicorn+httpx+yaml)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

ROOT = Path(__file__).resolve().parent
CFG_PATH = Path(os.environ.get("VFK250_CFG", ROOT / "config.yaml"))
IMG_DIR = Path(os.environ.get("VFK250_ASSETS", str(ROOT / "assets"))).expanduser()
if not IMG_DIR.is_absolute():
    IMG_DIR = (ROOT / IMG_DIR).resolve()

def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_cfg() -> dict[str, Any]:
    """config.yaml is the shipped template; config.local.yaml holds machine-local
    overrides (written by the page's settings dialog). Local wins."""
    with open(CFG_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    local = CFG_PATH.with_name("config.local.yaml")
    if local.exists():
        try:
            cfg = _deep_merge(cfg, yaml.safe_load(local.read_text()) or {})
        except Exception:
            pass
    return cfg


CFG: dict[str, Any] = _load_cfg()

app = FastAPI(title="mm-web")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---------------------------------------------------------------- llm routing
_llm_cache: dict[str, Any] = {"winner": None, "ts": 0.0}


async def resolve_llm(client: httpx.AsyncClient) -> tuple[str, str]:
    """Probe candidates, return (base_url, model) of first that answers."""
    if _llm_cache["winner"] and time.time() - _llm_cache["ts"] < 20:
        return _llm_cache["winner"]
    for cand in CFG["llm"].get("candidates", []):
        url = cand["base_url"].rstrip("/") + "/models"
        try:
            r = await client.get(url, timeout=2.5)
            if r.status_code == 200:
                _llm_cache.update(winner=(cand["base_url"].rstrip("/"), cand["model"]), ts=time.time())
                return cand["base_url"].rstrip("/"), cand["model"]
        except Exception:
            continue
    # fall back to first candidate even if probe failed (it may still work)
    c = CFG["llm"]["candidates"][0]
    return c["base_url"].rstrip("/"), c["model"]


# ---------------------------------------------------------------- k250 state
K250 = {
    "ceiling": 10,           # conservative first-run defaults — raise deliberately
    "ceiling2": 5,           # channel-2 ceiling — the page is the authority
    "level": 0,
    "level2": 0,
    "pattern": None,
    "pattern2": None,
    "running": False,
    "pid": None,
    "log": None,
    "started_at": None,
    "secs": None,
    "ends_at": None,
    "queue": [],             # stim commands waiting for the box to free up
}
QUEUE_MAX = 3
COMFY_CFG = CFG.get("comfyui", {})

# ceilings survive restarts — the page is the authority, and its word persists
CEILING_STATE = ROOT / ".ceilings"


def _load_ceilings() -> None:
    try:
        d = json.loads(CEILING_STATE.read_text())
        K250["ceiling"] = max(10.0, min(100.0, float(d.get("ceiling", K250["ceiling"]))))
        K250["ceiling2"] = max(0.0, min(100.0, float(d.get("ceiling2", K250["ceiling2"]))))
    except Exception:
        pass


def _save_ceilings() -> None:
    try:
        CEILING_STATE.write_text(json.dumps(
            {"ceiling": K250["ceiling"], "ceiling2": K250["ceiling2"]}))
    except Exception:
        pass


_load_ceilings()


def _block_active() -> bool:
    """In-memory check: is a launched block still within its window?"""
    if not K250["running"]:
        return False
    if K250.get("ends_at") and time.time() >= K250["ends_at"]:
        K250.update(running=False, level=0)
        return False
    return True


async def _launch_block(level: float, pattern: str, secs: int,
                        level2: float | None = None, pattern2: str | None = None) -> dict[str, Any]:
    """Spawn run_block.py once and record state. Caller must ensure the box is free.
    With level2/pattern2 the dual-channel driver (k250_play2.py) is used and the
    the caller/page ceilings are the authority (no limits file on this path)."""
    cfg = _k250_cfg()
    ceiling = K250["ceiling"]
    level = min(level, ceiling)
    argv = [_driver_python(cfg), str(_script_path(cfg["run_block"])),
            "--cap", str(ceiling), "--level", str(level),
            "--secs", str(secs), "--pattern", pattern]
    dual = level2 is not None and pattern2 is not None
    if level2 is not None and pattern2 is not None:
        cap2 = K250["ceiling2"]
        level2 = max(0.0, min(float(level2), cap2))
        argv += ["--level2", str(level2), "--pattern2", pattern2, "--cap2", str(cap2)]
    p = await asyncio.wait_for(
        asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        ),
        timeout=60,
    )
    out, _ = await p.communicate()
    text = out.decode().strip()
    if text.startswith("BLOCKED"):
        return {"blocked": True, "raw": text[:200]}
    # run_block.py prints JSON then a human-readable line — extract the JSON object
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    result = json.loads(text[brace_start:brace_end]) if brace_start >= 0 else {"raw": text[-300:]}
    if result.get("pid"):
        now = time.time()
        K250.update(running=True, pid=result.get("pid"), log=result.get("log"),
                    started_at=now, ends_at=now + secs, secs=secs,
                    level=level, pattern=pattern,
                    level2=(level2 if dual else 0), pattern2=(pattern2 if dual else None))
    return result


async def _queue_worker() -> None:
    """Fire queued stim blocks once the box is genuinely free."""
    while True:
        await asyncio.sleep(2)
        if not K250["queue"] or _block_active():
            continue
        snap = await k250_status_snapshot()
        if snap.get("drivers"):   # someone else owns the link — wait, never compete
            continue
        job = K250["queue"].pop(0)
        try:
            await _launch_block(job["level"], job["pattern"], job["secs"],
                                job.get("level2"), job.get("pattern2"))
        except Exception:
            pass


def _check_pattern_drift() -> None:
    """Log-only startup check: every pattern on the web whitelist must exist in the
    driver's dependency-free name table (k250_codec.PATTERN_NAMES). A whitelisted
    pattern the driver does not know = a mid-scene REFUSED waiting to happen."""
    try:
        rb = _script_path(CFG.get("k250", {}).get("run_block", ""))
        if str(rb.parent) not in sys.path:
            sys.path.insert(0, str(rb.parent))
        from k250_codec import PATTERN_NAMES
        allowed = set(CFG.get("k250", {}).get("patterns", []))
        unknown = sorted(allowed - set(PATTERN_NAMES))
        if unknown:
            print(f"[warn] stim whitelist patterns the driver does NOT know: {unknown}", flush=True)
        else:
            print(f"[ok] stim whitelist ({len(allowed)} patterns) is a subset of the "
                  f"driver's {len(PATTERN_NAMES)}", flush=True)
    except Exception as e:
        print(f"[warn] pattern drift check skipped: {e}", flush=True)


@app.on_event("startup")
async def _start_queue_worker() -> None:
    asyncio.create_task(_queue_worker())
    _check_pattern_drift()
    if COMFY_CFG.get("prewarm_on_start"):
        asyncio.create_task(_prewarm_qedit())


def _k250_cfg() -> dict[str, str]:
    return CFG["k250"]


def _script_path(p: str) -> Path:
    """Resolve a configured helper/script path; relative paths anchor at web/."""
    pp = Path(p).expanduser()
    return pp if pp.is_absolute() else (ROOT / pp).resolve()


def _driver_python(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else _k250_cfg()
    return cfg.get("python") or os.environ.get("VK250_PY") or sys.executable


async def k250_status_snapshot() -> dict[str, Any]:
    """Read link_status.py (ownership only, never competes for the link)."""
    try:
        p = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, str(_script_path(_k250_cfg()["link_status"])),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=15,
        )
        out, _ = await p.communicate()
        data = json.loads(out.decode().strip())
        return {"assessment": data.get("assessment"), "drivers": data.get("drivers", [])}
    except Exception as e:
        return {"assessment": f"ERROR: {e}", "drivers": []}


@app.post("/api/k250/drive")
async def k250_drive(req: Request):
    """Start a block (optionally dual-channel), or queue it if the box is busy."""
    body = await req.json()
    allowed = set(CFG["k250"].get("patterns", []))
    level = float(body.get("level", 10))
    secs = max(5, min(600, int(body.get("secs", 300))))
    pattern = body.get("pattern", "swell")
    if pattern not in allowed:
        return JSONResponse({"ok": False, "error": f"unknown pattern {pattern!r}"}, status_code=200)
    level = min(level, K250["ceiling"])
    level2 = body.get("level2")
    pattern2 = body.get("pattern2")
    if pattern2 is not None and pattern2 not in allowed:
        return JSONResponse({"ok": False, "error": f"unknown pattern2 {pattern2!r}"}, status_code=200)
    if level2 is not None:
        level2 = max(0.0, min(float(level2), K250["ceiling2"]))
    try:
        busy = _block_active()
        if not busy:
            snap = await k250_status_snapshot()
            busy = bool(snap.get("drivers"))
        if busy:
            if len(K250["queue"]) >= QUEUE_MAX:
                return {"ok": False, "queued": False, "error": "queue full — wait for the current block"}
            K250["queue"].append({"level": level, "pattern": pattern, "secs": secs,
                                  "level2": level2, "pattern2": pattern2,
                                  "queued_at": time.time()})
            return {"ok": True, "queued": True, "position": len(K250["queue"]),
                    "level": level, "pattern": pattern, "secs": secs,
                    "level2": level2, "pattern2": pattern2,
                    "state": "QUEUED_BEHIND_RUNNING_BLOCK"}
        result = await _launch_block(level, pattern, secs, level2, pattern2)
        if result.get("blocked"):
            return JSONResponse({"ok": False, "queued": False,
                                 "error": "link busy: " + result.get("raw", "")}, status_code=200)
        if not result.get("pid"):
            # launcher refused (validation, REFUSED gate, crash) — never pretend it started
            return JSONResponse({"ok": False, "queued": False,
                                 "error": "launcher refused",
                                 "raw": str(result.get("raw", result))[:300]}, status_code=200)
        return {"ok": True, "queued": False, **result}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.get("/api/k250/patterns")
async def k250_patterns():
    """The pattern whitelist (manual-drive dropdown + tag validation)."""
    return {"ok": True, "patterns": CFG["k250"].get("patterns", [])}


@app.post("/api/k250/ceilings")
async def k250_ceilings(req: Request):
    """The page is the ceiling authority (operator decision — no limits file)."""
    body = await req.json()
    if body.get("ceiling") is not None:
        K250["ceiling"] = max(10.0, min(100.0, float(body["ceiling"])))
    if body.get("ceiling2") is not None:
        K250["ceiling2"] = max(0.0, min(100.0, float(body["ceiling2"])))
    _save_ceilings()
    return {"ok": True, "ceiling": K250["ceiling"], "ceiling2": K250["ceiling2"]}


@app.post("/api/k250/stop")
async def k250_stop():
    """Stop the box and drop everything queued. Red means red."""
    cfg = _k250_cfg()
    dropped = len(K250["queue"])
    K250["queue"].clear()
    try:
        p = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                _driver_python(cfg), str(_script_path(cfg["stop"])),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            ),
            timeout=30,
        )
        out, _ = await p.communicate()
        text = out.decode().strip()
        confirmed = "PW\": 0" in text or "PW': 0" in text or 'PW": 0' in text
        K250.update(running=False, level=0, level2=0, pattern2=None, pid=None, ends_at=None)
        return {"ok": True, "confirmed_zero": confirmed, "dropped_queued": dropped, "raw": text[-400:]}
    except Exception as e:
        K250.update(running=False, level=0, level2=0, pattern2=None, pid=None, ends_at=None)
        return JSONResponse({"ok": False, "error": str(e), "dropped_queued": dropped}, status_code=200)


def _proc_start_epoch(pid: int) -> float | None:
    """When did that driver process start? (/proc starttime + boot time)"""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        starttime_ticks = int(fields[19])          # field 22 overall, after comm
        with open("/proc/stat") as f:
            for line in f:
                if line.startswith("btime"):
                    btime = int(line.split()[1])
                    break
            else:
                return None
        hz = os.sysconf("SC_CLK_TCK")
        return btime + starttime_ticks / hz
    except Exception:
        return None


def _external_driver_info(snap: dict[str, Any]) -> dict[str, Any] | None:
    """Describe a driver we didn't launch (e.g. started by a previous server)."""
    drivers = snap.get("drivers") or []
    if not drivers:
        return None
    d = drivers[0]
    argv = d.get("argv") or []
    info: dict[str, Any] = {"source": "external", "pid": d.get("pid")}
    try:
        # pattern is the first positional arg after the script path
        script_i = next(i for i, a in enumerate(argv)
                        if a.endswith(("k250_play.py", "k250_play2.py")))
        info["pattern"] = argv[script_i + 1]
        if "--base" in argv:
            info["level"] = float(argv[argv.index("--base") + 1])
        if "--base2" in argv:
            info["level2"] = float(argv[argv.index("--base2") + 1])
        if "--pattern2" in argv:
            info["pattern2"] = argv[argv.index("--pattern2") + 1]
        if "--secs" in argv:
            secs = float(argv[argv.index("--secs") + 1])
            info["secs"] = int(secs)
            start = _proc_start_epoch(d.get("pid"))
            if start:
                info["remaining"] = max(0, int(start + secs - time.time()))
    except Exception:
        pass
    return info


@app.get("/api/k250/status")
@app.post("/api/k250/status")
async def k250_status():
    snap = await k250_status_snapshot()
    active = _block_active()
    if active:
        remaining = max(0, int(K250["ends_at"] - time.time())) if K250.get("ends_at") else 0
        out = {**K250, "remaining": remaining, "queue_len": len(K250["queue"]), "link": snap}
    else:
        ext = _external_driver_info(snap)
        out = {**K250, "queue_len": len(K250["queue"]), "link": snap}
        if ext:   # a driver owns the box that this server didn't launch — report it honestly
            out.update(running=True, level=ext.get("level", 0), pattern=ext.get("pattern"),
                       level2=ext.get("level2", 0), pattern2=ext.get("pattern2"),
                       remaining=ext.get("remaining", 0), secs=ext.get("secs"),
                       source="external", pid=ext.get("pid"))
        else:
            out.update(running=False, level=0, level2=0, pattern2=None, remaining=0, source="bridge")
    return out


# ---------------------------------------------------------------- profiles
PROFILES_DIR = Path(os.environ.get("VFK250_PROFILES", ROOT / "profiles"))


def _load_profiles() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            out[data.get("slug", f.stem)] = data
        except Exception:
            continue
    return out


PROFILES = _load_profiles()
PROFILE_STATE = ROOT / ".active_profile"


def _load_active_slug():
    try:
        s = PROFILE_STATE.read_text().strip()
        if s in PROFILES:
            return s
    except Exception:
        pass
    return "demo" if "demo" in PROFILES else next(iter(PROFILES), None)


ACTIVE = {"slug": _load_active_slug()}


def _active_profile() -> dict[str, Any]:
    return PROFILES.get(ACTIVE["slug"]) or {}


def _profile_meta(p: dict[str, Any] | None = None) -> dict[str, Any]:
    p = p if p is not None else _active_profile()
    return {"slug": p.get("slug"), "name": p.get("name"),
            "accent_color": p.get("accent_color"), "voice": p.get("voice")}


def _shared_mechanics() -> str:
    ceil = int(K250["ceiling"])
    ceil2 = int(K250["ceiling2"])
    patterns = ", ".join(CFG["k250"].get("patterns", ["tide", "swell"]))
    return (
        "\n\nYou drive this session: you set the pace and you keep the lead while the wearer plays. "
        "Follow these rules exactly.\n"
        "1. Never mention being an AI, a model or a program.\n"
        "2. Replies are one to four short sentences. No lists, no markdown, no emoji, "
        "no asterisks, no stage directions.\n"
        "3. To move the current, put AT MOST ONE tag in your reply, on its own line, "
        "in exactly this shape:\n"
        "[[stim level=40 pattern=swell secs=300]]\n"
        f"level = whole number 1-{ceil}; pattern = one of: {patterns}; secs = 30-600.\n"
        "Only if channel 2 is wired this session, you may add two more fields before the "
        f"closing brackets: level2 (whole number 0-{ceil2}) and pattern2, e.g.\n"
        "[[stim level=40 pattern=swell secs=300 level2=12 pattern2=tide]]\n"
        "If you have not been told channel 2 is wired, drive channel 1 alone.\n"
        "4. To show yourself in the portrait frame, AT MOST ONE tag:\n"
        "[[image one-line visual description of the shot]]\n"
        "5. The tags are machine-read. Never explain them, never repeat them, never "
        "invent other bracket tags.\n"
        "6. The stop word is red and it is absolute. If the wearer says red, confirm "
        "zero and be gentle.\n"
        "Example — the wearer writes 'surprise me', you answer:\n"
        "You have been too quiet. Let me fix that.\n"
        "[[stim level=35 pattern=tease secs=300]]"
    )


def system_prompt() -> str:
    p = _active_profile()
    personality = p.get("personality") or (
        "You are the persona of this page, in control of a session while the wearer "
        "wears your e-stim pads. You are not an assistant and you never mention being an AI.")
    return personality + _shared_mechanics()


@app.get("/api/profiles")
async def list_profiles():
    return {"ok": True, "active": ACTIVE["slug"],
            "active_meta": _profile_meta(),
            "profiles": [{"slug": s, "name": p.get("name", s)} for s, p in PROFILES.items()]}


@app.get("/api/profile")
async def get_profile():
    return {"ok": True, "profile": _profile_meta(),
            "greeting": _active_profile().get("greeting", "...")}


@app.post("/api/profile")
async def set_profile(req: Request):
    body = await req.json()
    slug = body.get("slug")
    if slug not in PROFILES:
        return JSONResponse({"ok": False, "error": f"unknown profile: {slug}"}, status_code=200)
    ACTIVE["slug"] = slug
    try:
        PROFILE_STATE.write_text(slug)     # survive restarts
    except Exception:
        pass
    p = PROFILES[slug]
    pending = False
    if not _portrait_file(p):
        pp = p.get("portrait_prompt") or "close-up portrait, looking at the viewer"
        asyncio.create_task(_generate_plate(pp, profile=p))
        pending = True
    return {"ok": True, "profile": _profile_meta(p),
            "greeting": p.get("greeting", "..."), "portrait_pending": pending}


def _portrait_file(p: dict[str, Any]) -> Path | None:
    """Newest plate for this profile, else its default portrait, else None."""
    slug = p.get("slug", "mm")

    def newest(pat: str) -> Path | None:
        c = sorted((q for q in IMG_DIR.glob(pat) if q.is_file()),
                   key=lambda q: q.stat().st_mtime, reverse=True)
        return c[0] if c else None

    f = newest(f"plates_{slug}_*.png")
    if f is None and p.get("portrait"):
        cand = IMG_DIR / p["portrait"]
        f = cand if cand.exists() else None
    return f


# ---------------------------------------------------------------- chat
def _parse_stim(text: str) -> tuple[dict[str, Any] | None, str]:
    """Tolerant [[stim ...]] extraction with server-side validation.
    Accepts case drift, floats, loose spacing; clamps to ceilings; drops the tag
    (and the command) if the pattern is not on the whitelist. Returns (stim, clean_text)."""
    clean = re.sub(r"\s*\[\[\s*stim[^\]]*\]\]", "", text, flags=re.IGNORECASE).strip()
    m = re.search(
        r"\[\[\s*stim\s+level\s*=\s*(\d+(?:\.\d+)?)\s+pattern\s*=\s*([A-Za-z_]+)\s+"
        r"secs\s*=\s*(\d+(?:\.\d+)?)"
        r"(?:\s+level2\s*=\s*(\d+(?:\.\d+)?)\s+pattern2\s*=\s*([A-Za-z_]+))?\s*\]\]",
        text, re.IGNORECASE)
    if not m:
        return None, clean
    allowed = set(CFG["k250"].get("patterns", []))
    pattern = m.group(2).lower()
    if pattern not in allowed:
        return None, clean          # invented pattern — drop it rather than launch garbage
    stim: dict[str, Any] = {
        "level": min(float(m.group(1)), K250["ceiling"]),
        "pattern": pattern,
        "secs": max(30, min(600, int(float(m.group(3))))),
    }
    if m.group(4) is not None and m.group(5) is not None and m.group(5).lower() in allowed:
        stim["level2"] = min(float(m.group(4)), K250["ceiling2"])
        stim["pattern2"] = m.group(5).lower()
    return stim, clean


@app.post("/api/chat")
async def chat(req: Request):
    body = await req.json()
    messages = body.get("messages", [])
    history = body.get("history", [])
    sysmsg = system_prompt()
    if VISION["summary"] and time.time() - VISION["ts"] < 120:
        sysmsg += f"\n\n[Your webcam is ON. What you see right now: {VISION['summary']}]"
    full = [{"role": "system", "content": sysmsg}] + history[-20:] + messages
    try:
        async with httpx.AsyncClient() as client:
            base, model = await resolve_llm(client)
            r = await client.post(
                base + "/chat/completions",
                json={
                    "model": model,
                    "messages": full,
                    "temperature": CFG["llm"].get("temperature", 0.9),
                    "max_tokens": CFG["llm"].get("max_tokens", 320),
                },
                timeout=120,
            )
            data = r.json()
            text = data["choices"][0]["message"]["content"]
            # tolerant [[stim]] extraction + validation (small models drift on spacing/types)
            stim, text = _parse_stim(text)
            # extract image tag if present — generate in the background, frame swaps on its own
            image_state = None
            im = re.search(r"\[\[image\s+([^\]]+)\]\]", text)
            if im:
                image_prompt = im.group(1).strip()
                text = re.sub(r"\s*\[\[image[^\]]*\]\]", "", text).strip()
                image_state = "generating"
                asyncio.create_task(_generate_plate(image_prompt))
            return {"ok": True, "text": text, "stim": stim, "image": image_state,
                    "llm": f"{base} {model}"}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ---------------------------------------------------------------- tts / stt
@app.post("/api/tts")
async def tts(req: Request):
    body = await req.json()
    text = body.get("text", "")
    payload: dict[str, Any] = {"text": text}
    voice = _active_profile().get("voice") or CFG["tts"].get("voice")
    if voice:
        payload["voice"] = voice       # backend may ignore; profile voice rides along
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(CFG["tts"]["url"], json=payload, timeout=60)
            ct = r.headers.get("content-type", "")
            if "audio" in ct or "wav" in ct or "mp3" in ct:
                return Response(content=r.content, media_type=ct or "audio/wav")
            # maybe it returns base64 json
            data = r.json()
            import base64
            audio_b64 = data.get("audio") or data.get("wav") or ""
            raw = base64.b64decode(audio_b64) if audio_b64 else r.content
            return Response(content=raw, media_type="audio/wav")
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.post("/api/stt")
async def stt(file: UploadFile = File(...)):
    audio = await file.read()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                CFG["stt"]["url"],
                files={"file": ("audio.webm", audio, "audio/webm")},
                data={"model": CFG["stt"].get("model", "base"), "language": "en"},
                timeout=120,
            )
            data = r.json()
            return {"ok": True, "text": data.get("text", "")}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


# ---------------------------------------------------------------- image
@app.get("/api/portrait")
async def portrait():
    """Serve the active character's newest plate — every new gen overwrites the last."""
    try:
        f = _portrait_file(_active_profile())
        if f:
            return FileResponse(f, media_type="image/png",
                                headers={"Cache-Control": "no-store", "X-Plate": f.name})
    except Exception:
        pass
    return JSONResponse({"ok": False, "error": "no portrait"}, status_code=404)


@app.get("/api/expression/{mood}")
async def expression(mood: str):
    """Serve a rendered expression state for the ACTIVE character only.
    Naming: expr_<slug>_<mood>.png inside the assets dir. A persona with no
    rendered expression 404s and the page keeps their current look."""
    mood = re.sub(r"[^a-z_]", "", mood.lower())
    slug = (_active_profile().get("slug") or "mm")
    cands = [IMG_DIR / f"expr_{slug}_{mood}.png"]
    for cand in cands:
        if cand.exists():
            return FileResponse(cand, media_type="image/png",
                                headers={"Cache-Control": "no-store"})
    return JSONResponse({"ok": False, "error": "no expression"}, status_code=404)


# ---------------------------------------------------------------- vision (webcam -> VLM)
VISION: dict[str, Any] = {"summary": None, "ts": 0.0}
_vlm_cache: dict[str, Any] = {"winner": None, "ts": 0.0}


async def resolve_vlm(client: httpx.AsyncClient):
    """Probe VLM candidates (ports rotate with loaded weights — never hardcode)."""
    if _vlm_cache["winner"] and time.time() - _vlm_cache["ts"] < 60:
        return _vlm_cache["winner"]
    for cand in CFG.get("vision", {}).get("candidates", []):
        url = cand["base_url"].rstrip("/") + "/models"
        try:
            r = await client.get(url, timeout=2.5)
            if r.status_code == 200:
                winner = (cand["base_url"].rstrip("/"), cand.get("model", "vlm"))
                _vlm_cache.update(winner=winner, ts=time.time())
                return winner
        except Exception:
            continue
    return None


@app.post("/api/vision")
async def vision(file: UploadFile = File(...)):
    """One webcam frame -> VLM description, kept as her rolling 'what I see'."""
    import base64
    img = await file.read()
    b64 = base64.b64encode(img).decode()
    try:
        async with httpx.AsyncClient() as client:
            w = await resolve_vlm(client)
            if not w:
                return {"ok": False, "error": "no VLM available"}
            base, model = w
            r = await client.post(
                base + "/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text":
                            "You are the eyes of a dominatrix watching her submissive through "
                            "a webcam. In one or two short sentences describe what you see: "
                            "posture, expression, state of arousal or distress, anything she "
                            "would comment on. No advice, no preamble."},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]}],
                    "max_tokens": 120,
                    "temperature": 0.4,
                },
                timeout=90,
            )
            d = r.json()
            summary = d["choices"][0]["message"]["content"].strip()
            VISION.update(summary=summary[:400], ts=time.time())
            return {"ok": True, "summary": summary}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/vision/clear")
async def vision_clear():
    """Webcam OFF means she genuinely cannot see him. Period."""
    VISION.update(summary=None, ts=0.0)
    return {"ok": True}


@app.post("/api/image/prewarm")
async def image_prewarm():
    """Warm the Qwen-Edit model cache with a 1-step miniature job so the first
    real [[image]] tag doesn't pay the multi-minute cold-load tax. Fire-and-forget."""
    asyncio.create_task(_prewarm_qedit())
    return {"ok": True, "state": "warming"}


async def _prewarm_qedit():
    prof = _active_profile()
    g = prof.get("gen", {})
    slug = prof.get("slug", "demo")
    ref = IMG_DIR / g.get("reference", f"ref_{slug}.png")
    if not ref.exists():
        cands = sorted((q for q in IMG_DIR.glob(f"plates_{slug}_*.png") if q.is_file()),
                       key=lambda q: q.stat().st_mtime, reverse=True)
        if not cands:
            cands = sorted((q for q in IMG_DIR.glob("mmweb_*.png") if q.is_file()),
                           key=lambda q: q.stat().st_mtime, reverse=True)
        if not cands:
            cands = sorted((q for q in IMG_DIR.glob("mm_*.png") if q.is_file()),
                           key=lambda q: q.stat().st_mtime, reverse=True)
        if not cands:
            return                      # nothing to warm against
        ref = cands[0]
    cmd = [str(_script_path(COMFY_CFG.get("qedit", "helpers/pg_qedit"))),
           "--image", str(ref), "--prompt", "same woman, same pose",
           "--steps", "1", "--w", "256", "--h", "256", "--prefix", "prewarm"]
    env = {**os.environ, "COMFY_HOST": COMFY_CFG.get("url", "http://127.0.0.1:8188")}
    try:
        p = await asyncio.create_subprocess_exec(*cmd, env=env,
                                                 stdout=asyncio.subprocess.DEVNULL,
                                                 stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.communicate(), timeout=1200)
    except Exception:
        pass


@app.get("/api/comfy/status")
async def comfy_status():
    url = COMFY_CFG.get("url", "http://127.0.0.1:8188")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url + "/queue", timeout=4)
            d = r.json()
            return {"ok": True, "url": url,
                    "running": len(d.get("queue_running", [])),
                    "pending": len(d.get("queue_pending", []))}
    except Exception as e:
        return {"ok": False, "url": url, "error": str(e)}


@app.post("/api/image/gen")
async def image_gen(req: Request):
    """Generate a new plate of the ACTIVE character through the ComfyUI router.
    Saved into IMG_DIR — /api/portrait picks up the newest, so the frame swaps."""
    body = await req.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"ok": False, "error": "no prompt"}, status_code=200)
    return await _generate_plate(prompt, denoise=body.get("denoise"))


async def _generate_plate(prompt: str, profile: dict[str, Any] | None = None,
                          denoise: float | None = None) -> dict[str, Any]:
    """Per-profile generation: LoRA/t2i via pg_lora_t2i, reference-anchored via
    pg_img2img, instruction edits via pg_qedit. Output: assets/plates_<slug>_<ts>.png."""
    prof = profile or _active_profile()
    slug = prof.get("slug", "mm")
    g = prof.get("gen", {})
    ckpt = g.get("checkpoint", COMFY_CFG.get("checkpoint", "RealVisXL_V5.0_fp16.safetensors"))
    seed_prompt = g.get("prompt_seed", COMFY_CFG.get("prompt_seed", ""))
    full_prompt = f"{seed_prompt}, {prompt}" if seed_prompt else prompt
    w = str(g.get("width", COMFY_CFG.get("width", 832)))
    h = str(g.get("height", COMFY_CFG.get("height", 1216)))
    steps = str(g.get("steps", COMFY_CFG.get("steps", 34)))
    cfgv = str(g.get("cfg", COMFY_CFG.get("cfg", 6.5)))
    env = {**os.environ, "COMFY_HOST": COMFY_CFG.get("url", "http://127.0.0.1:8188")}
    prefix = f"plates_{slug}"
    ts = int(time.time())
    lora = g.get("lora")
    if lora:
        cmd = [str(_script_path(COMFY_CFG.get("lora_t2i", "helpers/pg_lora_t2i"))),
               "--ckpt", ckpt, "--lora", lora, str(g.get("lora_strength", 0.85)),
               "--prompt", full_prompt, "--w", w, "--h", h,
               "--steps", steps, "--cfg", cfgv, "--prefix", prefix]
        if g.get("neg"):
            cmd += ["--neg", g["neg"]]
    elif g.get("mode") == "t2i":
        # pure text-to-image: identity lives in prompt_seed / LoRA trigger words
        cmd = [str(_script_path(COMFY_CFG.get("t2i", "helpers/pg_lora_t2i"))),
               "--ckpt", ckpt, "--prompt", full_prompt, "--w", w, "--h", h,
               "--steps", steps, "--cfg", cfgv, "--prefix", prefix]
        if g.get("neg"):
            cmd += ["--neg", g["neg"]]
    else:
        ref = IMG_DIR / g.get("reference", f"ref_{slug}.png")
        if not ref.exists():
            cands = sorted((q for q in IMG_DIR.glob(f"plates_{slug}_*.png") if q.is_file()),
                           key=lambda q: q.stat().st_mtime, reverse=True)
            if not cands:
                return {"ok": False, "error": "no reference plate"}
            ref = cands[0]
        if g.get("mode") == "edit":
            # Qwen-Image-Edit 2509: instruction-style edits that keep identity but
            # genuinely change pose/scene. Natural language, no seed prepend.
            cmd = [str(_script_path(COMFY_CFG.get("qedit", "helpers/pg_qedit"))),
                   "--image", str(ref), "--prompt", prompt, "--prefix", prefix]
        else:
            cmd = [str(_script_path(COMFY_CFG.get("img2img", "helpers/pg_img2img"))),
                   "--image", str(ref), "--ckpt", ckpt, "--prompt", full_prompt,
                   "--denoise", str(denoise if denoise is not None
                                    else g.get("denoise", COMFY_CFG.get("denoise", 0.55))),
                   "--w", w, "--h", h, "--steps", steps, "--cfg", cfgv, "--prefix", prefix]
            if g.get("neg"):
                cmd += ["--neg", g["neg"]]
    try:
        p = await asyncio.create_subprocess_exec(
            *cmd, env=env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=900)
        text = out.decode().strip()
        if p.returncode != 0:
            return {"ok": False, "error": text[-500:]}
        src = Path(text.splitlines()[-1].strip())
        if not src.exists():
            return {"ok": False, "error": f"helper path missing: {src}", "raw": text[-300:]}
        dest = IMG_DIR / f"plates_{slug}_{ts}.png"
        dest.write_bytes(src.read_bytes())
        return {"ok": True, "file": str(dest), "slug": slug}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "generation timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------- settings
def _parse_candidate_lines(lines) -> list[dict[str, str]]:
    out = []
    for ln in lines or []:
        ln = str(ln).strip()
        if not ln or ln.startswith("#"):
            continue
        parts = ln.split(None, 1)
        out.append({"base_url": parts[0], "model": parts[1].strip() if len(parts) > 1 else ""})
    return out


def _candidate_lines(section: str) -> list[str]:
    return [f"{c.get('base_url', '')} {c.get('model', '')}".strip()
            for c in CFG.get(section, {}).get("candidates", [])]


@app.get("/api/settings")
async def get_settings():
    return {"ok": True,
            "llm_candidates": _candidate_lines("llm"),
            "vision_candidates": _candidate_lines("vision"),
            "tts_url": CFG.get("tts", {}).get("url", ""),
            "stt_url": CFG.get("stt", {}).get("url", ""),
            "comfy_url": CFG.get("comfyui", {}).get("url", "")}


@app.post("/api/settings")
async def set_settings(req: Request):
    """Persist overrides to config.local.yaml, hot-apply, reset probe caches."""
    global CFG, COMFY_CFG
    body = await req.json()
    over: dict[str, Any] = {}
    if body.get("llm_candidates") is not None:
        over["llm"] = {"candidates": _parse_candidate_lines(body["llm_candidates"])}
    if body.get("vision_candidates") is not None:
        over["vision"] = {"candidates": _parse_candidate_lines(body["vision_candidates"])}
    if body.get("tts_url") is not None:
        over["tts"] = {"url": str(body["tts_url"]).strip()}
    if body.get("stt_url") is not None:
        over["stt"] = {"url": str(body["stt_url"]).strip()}
    if body.get("comfy_url") is not None:
        over["comfyui"] = {"url": str(body["comfy_url"]).strip()}
    try:
        local = CFG_PATH.with_name("config.local.yaml")
        existing = {}
        if local.exists():
            existing = yaml.safe_load(local.read_text()) or {}
        local.write_text(yaml.safe_dump(_deep_merge(existing, over), sort_keys=False))
        CFG = _load_cfg()
        COMFY_CFG = CFG.get("comfyui", {})
        _llm_cache.update(winner=None, ts=0.0)
        _vlm_cache.update(winner=None, ts=0.0)
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.get("/api/health")
async def health():
    out: dict[str, Any] = {"ok": True, "ts": time.time()}
    try:
        async with httpx.AsyncClient() as client:
            base, model = await resolve_llm(client)
            out["llm"] = f"{base} ({model})"
    except Exception as e:
        out["llm"] = f"ERROR {e}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(CFG["tts"]["url"].rsplit("/", 1)[0] + "/health", timeout=3)
            out["tts"] = "up" if r.status_code == 200 else f"http {r.status_code}"
    except Exception:
        out["tts"] = "down"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://127.0.0.1:3201/v1/models", timeout=3)
            out["stt"] = "up" if r.status_code == 200 else f"http {r.status_code}"
    except Exception:
        out["stt"] = "down (lazy)"
    out["k250"] = await k250_status_snapshot()
    return out


# ---------------------------------------------------------------- static
@app.get("/")
async def index():
    return FileResponse(ROOT / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("VFK250_PORT", CFG.get("port", 8091))), log_level="warning")
