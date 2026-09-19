#!/usr/bin/env python3
"""K250 launcher bridge — lets the limits-form.html page run patterns with one click.

A tiny HTTP server bound to 127.0.0.1 ONLY (nothing on the network can reach it).
The page POSTs {pattern, level, secs} to /run; this validates against the limits
file, then shells out to the tested k250_play.py engine. The engine enforces the
power ceiling, slew, session budget and stop word — this bridge is a launcher,
never a bypass.

Endpoints:
  GET  /            -> the limits page itself (same-origin with this server)
  GET  /limits      -> the limits fields this page owns, as the file has them
  POST /limits      -> merge the page's numbers into limits.json (backs up first)
  GET  /patterns    -> the pattern list (name -> first docstring line)
  POST /run         -> body {pattern, level, secs}  (level 1-100, secs 10-300)
  POST /stop        -> run k250_stop.py (instant zero + kill)
  GET  /status      -> {running, pattern, pid, exit_code, live_channels,
                        zeroing, last_zero, log_tail}

Run:  python3 k250_launcher.py          (Linux, macOS)
      py -3 k250_launcher.py           (Windows, from an activated venv)

The page it serves is the same file, so http://127.0.0.1:6969/ works on every
platform we support; opening limits-form.html directly still works too.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


def _venv_python():
    """The interpreter that has bleak: the venv, on Linux/macOS or on Windows.

    Windows installs the venv as venv\\Scripts\\python.exe, so a hardcoded
    venv/bin/python is a path that does not exist there — the same class of
    unportable assumption as an absolute author-home path, just quieter. Falls
    back to whatever is running this, which is correct on the documented Windows
    route (`py -3 k250_launcher.py` inside an activated venv)."""
    for cand in (os.path.join(HERE, "venv", "bin", "python"),
                 os.path.join(HERE, "venv", "Scripts", "python.exe"),
                 os.path.join(HERE, ".venv", "bin", "python"),
                 os.path.join(HERE, ".venv", "Scripts", "python.exe")):
        if os.path.isfile(cand):
            return cand
    return sys.executable


PY = _venv_python()
PLAY = os.path.join(HERE, "k250_play.py")
STOP = os.path.join(HERE, "k250_stop.py")
LIMITS = os.environ.get("K250_LIMITS", os.path.join(HERE, "limits.json"))
PAGE = os.path.join(HERE, "limits-form.html")
RUNLOG = os.path.join(HERE, "launcher-run.log")

# the running pattern process (one at a time — the box takes one BLE connection)
_lock = threading.Lock()
_running = {"proc": None, "pattern": None, "started": 0.0}
# the post-run zeroing thread + its result, so the page can show whether the box
# is definitely back at zero afterwards
_zero_thread = None
_last_zero = {"ok": None, "message": None, "pattern": None, "at": None}


def _zero_after(proc, pattern):
    """After a run ends: SIGTERM anything left, then write PW=0 to every live channel.

    The engine already zeroes at the end of a pattern, so this is the second
    lock, not the only one. It exists because the dangerous case is exactly the
    run that ends abnormally — killed, crashed, or a final write that never
    landed — and because a stop that cannot reach the box must say so: exit 1
    from the stop tool means the box may still be energised."""
    try:
        proc.wait()
    except Exception:
        pass
    try:
        r = subprocess.run([PY, STOP], cwd=HERE, timeout=90,
                           capture_output=True, text=True)
        lines = [l.strip() for l in ((r.stdout or "") + (r.stderr or "")).splitlines() if l.strip()]
        # Prefer the LOUD line. The stop tool's useful line is its first ("STOP
        # FAILED: box not reachable — IT MAY STILL BE ENERGISED!"); its last line
        # is a parenthetical about the Options screen, which is true but reads
        # like a footnote on the one message that must not read like a footnote.
        msg = next((l for l in lines if l.upper().startswith("STOP FAILED")),
                   lines[0] if lines else f"exit {r.returncode}")
        _last_zero.update(ok=r.returncode == 0, message=msg,
                          pattern=pattern, at=time.time())
    except Exception as e:
        _last_zero.update(ok=False, message=f"stop tool failed: {e}",
                          pattern=pattern, at=time.time())


def _load_limits():
    try:
        with open(LIMITS) as f:
            return json.load(f)
    except Exception:
        return {}


def _power_ceiling():
    d = _load_limits()
    return d.get("power", {}).get("max_percent", 50)


_PATTERNS_CACHE = None


def _pattern_list():
    """name -> first line of docstring, from the engine's PATTERNS.

    Spawning the engine to answer this costs ~100 ms (it imports bleak, ~76 ms of
    that) and the answer cannot change while this process runs, so it is computed
    once and cached. Returns the cache even if a later call would fail."""
    global _PATTERNS_CACHE
    if _PATTERNS_CACHE is not None:
        return _PATTERNS_CACHE
    try:
        out = subprocess.run(
            [PY, "-c",
             "import sys;sys.path.insert(0,'%s');import k250_play as K;"
             "import json;print(json.dumps({n:(f.__doc__ or '').strip().split(chr(10))[0] for n,f in K.PATTERNS.items()}))" % HERE],
            capture_output=True, text=True, timeout=30)
        _PATTERNS_CACHE = json.loads(out.stdout) if out.returncode == 0 else {}
    except Exception:
        _PATTERNS_CACHE = {}
    return _PATTERNS_CACHE


def _tail(n=5):
    """Last lines of the current run's own output.

    The engine used to be launched with stdout/stderr to DEVNULL, so a run that
    refused to do anything (no live channels -> the box echoes PW:0 for every
    write) looked identical to a healthy one: the page said 'running', nothing
    was felt, and there was no evidence either way. The engine's own output is
    the witness — capture it and show it."""
    try:
        with open(RUNLOG, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except Exception:
        return []


def _live_channels():
    """What the engine last reported as actually plugged in.

    `live channels: []` means the box has no usable load: it accepts and echoes
    power writes while nothing can be felt, which is exactly how a run 'looks
    healthy and does nothing'. The engine prints this early and it scrolls out
    of any tail, so scan the whole run log for it."""
    try:
        with open(RUNLOG, errors="replace") as f:
            hits = re.findall(r"live channels:\s*\[([^\]]*)\]", f.read())
    except Exception:
        return None
    if not hits:
        return None
    return [int(x) for x in re.findall(r"\d+", hits[-1])]


def _limits_view():
    """The fields this page owns, as they currently stand in the contract file.

    The page must show the file, not its own defaults: a slider that disagrees
    with limits.json is the exact confusion the Apply button exists to remove."""
    d = _load_limits()
    pc = (d.get("channels", {}) or {}).get("per_channel", {}) or {}
    return {
        "power_ceiling": (d.get("power", {}) or {}).get("max_percent"),
        "frequency_max": (d.get("frequency", {}) or {}).get("max"),
        "slew": (d.get("slew", {}) or {}).get("max_percent_per_second"),
        "session_minutes": round(((d.get("session", {}) or {}).get("max_duration_s") or 1800) / 60),
        "stop_word": d.get("stop_word"),
        "battery_below": (d.get("battery", {}) or {}).get("report_below_percent"),
        "ack": (d.get("safety", {}) or {}).get("hard_stops_acknowledged"),
        "per_channel": {str(c): (pc.get(str(c)) or {}) for c in (1, 2, 3, 4)},
        "path": LIMITS,
    }


def _apply_limits(p):
    """Write the page's numbers into limits.json — merging, never replacing.

    The page generates a whole JSON document, but writing that document over the
    file would DELETE everything the page does not know about (pattern_notes,
    the tuned notes, the disclaimer wording) — the same class of accident as
    copying the repo's default limits over a live one. So this merges only the
    keys the page owns, after validating them, and keeps a timestamped backup."""
    d = _load_limits()
    if not d:
        return False, f"cannot read {LIMITS}"

    def i(key, lo, hi):
        try:
            v = int(p[key])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{key}: missing or not a number")
        if not (lo <= v <= hi):
            raise ValueError(f"{key}: {v} is outside {lo}-{hi}")
        return v

    try:
        ceiling = i("power_ceiling", 0, 100)
        freq = i("frequency_max", 0, 10000)
        slew = i("slew", 0, 1000)
        minutes = i("session_minutes", 1, 240)
        batt = None
        if "battery_below" in p:
            batt = int(p["battery_below"])
            if not (0 <= batt <= 100):
                raise ValueError(f"battery_below: {batt} is outside 0-100")
        ack = bool(p["ack"]) if "ack" in p else None
        stop = str(p.get("stop_word", "")).strip()
        if not (1 <= len(stop) <= 24):
            raise ValueError("stop_word: must be 1-24 characters")
        pc = p.get("per_channel") or {}
        chan = {}
        for c in ("1", "2", "3", "4"):
            e = pc.get(c) or {}

            def num(key, lo, hi):
                v = e.get(key)
                if v is None:
                    return None          # null = inherit the global limit, as the file documents
                v = int(v)
                if not (lo <= v <= hi):
                    raise ValueError(f"per_channel.{c}.{key}: {v} is outside {lo}-{hi}")
                return v

            chan[c] = {}
            for k, gl in (("power", ceiling), ("frequency", freq), ("slew", slew)):
                v = num(k, 0, 100 if k == "power" else (10000 if k == "frequency" else 1000))
                # equal to the global limit -> write null, i.e. "inherit", which is
                # how the file is designed to read. Spelling out four identical
                # channels is churn in the contract for no benefit.
                chan[c][k] = v if (v is None or v != gl) else None
    except (ValueError, TypeError) as e:
        return False, f"refused: {e}"

    changes = []

    def put(where, old, new):
        if old != new:
            changes.append(f"{where}: {old} -> {new}")

    old_p = dict(d.get("power", {}) or {})
    put("power.max_percent", old_p.get("max_percent"), ceiling)
    # power.default_percent is NOT the page's to write. It is the wearer's tuned
    # starting figure (38 in the live file); the page's level is the ceiling, and
    # overwriting the start with the ceiling is exactly the kind of silent loss
    # this file has already suffered once.
    d.setdefault("power", {})["max_percent"] = ceiling

    put("frequency.max", (d.get("frequency", {}) or {}).get("max"), freq)
    d.setdefault("frequency", {})["max"] = freq

    put("slew.max_percent_per_second",
        (d.get("slew", {}) or {}).get("max_percent_per_second"), slew)
    d.setdefault("slew", {})["max_percent_per_second"] = slew

    sess = d.setdefault("session", {})
    old_min = round((sess.get("max_duration_s") or 0) / 60)
    put("session.max_duration_s", f"{old_min} min", f"{minutes} min")
    sess["max_duration_s"] = minutes * 60

    put("stop_word", d.get("stop_word"), stop)
    d["stop_word"] = stop

    if batt is not None:
        put("battery.report_below_percent",
            (d.get("battery", {}) or {}).get("report_below_percent"), batt)
        d.setdefault("battery", {})["report_below_percent"] = batt

    if ack is not None:
        put("safety.hard_stops_acknowledged",
            (d.get("safety", {}) or {}).get("hard_stops_acknowledged"), ack)
        d.setdefault("safety", {})["hard_stops_acknowledged"] = ack

    live = d.setdefault("channels", {}).setdefault("per_channel", {})
    for c, vals in chan.items():
        old = live.get(c) or {}
        for k in ("power", "frequency", "slew"):
            put(f"channels.per_channel.{c}.{k}", old.get(k), vals[k])
        live[c] = vals

    if not changes:
        return True, "already matches the file — nothing to write"

    stamp = time.strftime("%Y%m%d-%H%M%S")
    # next to the file being backed up, not next to this script: K250_LIMITS can
    # point somewhere else entirely, and a backup that lands in the wrong directory
    # is a backup nobody finds when they need it.
    backup = os.path.join(os.path.dirname(os.path.abspath(LIMITS)),
                          f"limits.json.bak-{stamp}")
    try:
        with open(LIMITS) as f:
            raw = f.read()
        with open(backup, "w") as f:
            f.write(raw)
        tmp = LIMITS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, LIMITS)
    except Exception as e:
        return False, f"write failed: {e}"
    pruned = _prune_backups()
    return True, (f"{len(changes)} change(s) written; backup {os.path.basename(backup)}"
                  + (f"; pruned {pruned} old backup(s)" if pruned else ""))


def _prune_backups(keep=10):
    """Keep the newest `keep` backups next to the limits file.

    One is written per Apply, so the directory grows forever — 32 of them, 165 KB,
    and every one looks like every other to a human scanning a folder. Ten is plenty
    to undo a mistake; the file itself is the contract, these are just the undo."""
    d = os.path.dirname(os.path.abspath(LIMITS))
    try:
        baks = sorted((os.path.join(d, f) for f in os.listdir(d)
                       if f.startswith("limits.json.bak-")), key=os.path.getmtime)
    except OSError:
        return 0
    n = 0
    for p in baks[:-keep] if len(baks) > keep else []:
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def _session_state():
    """The session ledger, read IN-PROCESS through the module that enforces it.

    This used to spawn `k250_session.py show --json` on every call, and the page
    polls this endpoint every two seconds: one python process (38 ms) per poll,
    thirty a minute, forever, for a number that is two file reads away. Importing
    the module is not a reimplementation — it is the same code the engine uses, so
    the timer still cannot disagree with reality."""
    try:
        import importlib
        import sys as _sys
        if HERE not in _sys.path:
            _sys.path.insert(0, HERE)
        sess = importlib.import_module("k250_session")
        d = _load_limits()
        max_s = float(((d.get("session") or {}).get("max_duration_s")) or 1800)
        st = sess.load(HERE)
        used = float(st.get("used_s", 0.0) or 0.0)
        return {"used_s": round(used, 1), "max_s": max_s,
                "left_s": round(max(0.0, max_s - used), 1),
                "spent": (max_s - used) <= 0,
                "session_start": st.get("session_start"),
                "enforced": True}
    except Exception as e:
        return {"used_s": None, "max_s": None, "left_s": None, "spent": None,
                "enforced": True, "error": str(e)}


def _session_reset():
    """Deliberate fresh session. There is no time-based reset any more, so this is
    the only way the budget refills — which is why it takes two clicks on the page."""
    try:
        r = subprocess.run([PY, os.path.join(HERE, "k250_session.py"), "reset", "--dir", HERE],
                           capture_output=True, text=True, timeout=20)
        ok = r.returncode == 0
        return ok, (r.stderr or r.stdout or "").strip() or "reset", _session_state()
    except Exception as e:
        return False, f"reset failed: {e}", _session_state()


def _run_pattern(pattern, level, secs):
    """Validate + launch. Returns (ok, message)."""
    global _zero_thread
    ceiling = _power_ceiling()
    if level == 0:
        return False, ("drive level is 0 — set it in section 3 (Manual). Nothing is "
                       "sent at 0, on purpose: the stock position is safe.")
    if not (1 <= level <= 100):
        return False, f"level must be 1-100 (got {level})"
    if not (10 <= secs <= 300):
        return False, f"secs must be 10-300 (got {secs})"
    if pattern not in _pattern_list():
        return False, f"unknown pattern '{pattern}'"
    # the engine clamps to the ceiling anyway; we just refuse to ask above it
    # The Manual drive level is decoupled from the limits: if it is above the
    # file's ceiling, that is the wearer's spoken override and the engine is told
    # by name, so the run log carries an OVERRIDE line instead of a silent raise.
    over = level > ceiling
    eff = level
    cmd = [PY, PLAY, pattern, "--base", str(eff), "--peak", str(eff),
           "--secs", str(secs), "--limits", LIMITS]
    if over:
        # be explicit on both counts: name the ceiling and say it is overridden
        cmd += ["--hardcap", str(eff), "--override-ceiling"]
    with _lock:
        if _running["proc"] is not None and _running["proc"].poll() is None:
            return False, "a pattern is already running — stop it first"
        # the post-run zeroing holds the box's single BLE connection: wait for it
        # rather than fight it (a fresh run that can't reach the box is silence)
        if _zero_thread is not None and _zero_thread.is_alive():
            _zero_thread.join(timeout=30)
            if _zero_thread.is_alive():
                return False, "still zeroing the box after the last run — try again in a moment"
        lf = open(RUNLOG, "wb")
        try:
            env = dict(os.environ)
            if over:
                # The ONLY place the wearer's level-override marker is ever set: a
                # run the person in the electrodes started from the page. Tools and
                # agent-driven runs never get this marker, and the engine refuses
                # the flag without it.
                env["K250_WEARER_OVERRIDE"] = "1"
            proc = subprocess.Popen(cmd, cwd=HERE, stdout=lf, stderr=subprocess.STDOUT,
                                    env=env)
        except Exception as e:
            lf.close()
            return False, f"could not launch the engine: {e}"
        _running["proc"] = proc
        _running["pattern"] = pattern
        _running["started"] = time.time()
        _running["logfile"] = lf
        _last_zero.update(ok=None, message=None, pattern=pattern, at=None)
        _zero_thread = threading.Thread(target=_zero_after, args=(proc, pattern), daemon=True)
        _zero_thread.start()
    msg = f"started {pattern} at {eff}% for {secs}s"
    if over:
        msg += (f" — OVERRIDING the {ceiling}% ceiling in limits.json "
                f"(Manual drive level wins)")
    else:
        msg += f" (ceiling {ceiling}%)"
    return True, msg


def _stop():
    with _lock:
        proc = _running["proc"]
        lf = _running.get("logfile")
        _running["proc"] = None
        _running["pattern"] = None
        _running["logfile"] = None
    # kill the pattern process first, then run the stop tool (belt and braces)
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    if lf is not None:
        try:
            lf.close()
        except Exception:
            pass
    try:
        subprocess.run([PY, STOP], cwd=HERE, timeout=30,
                       capture_output=True, text=True)
        return True, "stopped"
    except Exception as e:
        return False, f"stop tool failed: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        """Let a LOCAL FILE page talk to the bridge, and nothing else.

        The page is opened as file:///... so its Origin header is the literal
        string "null" (that is what browsers send for file:// pages). Only that
        origin is echoed back: a website's origin gets no
        Access-Control-Allow-Origin header, so the browser blocks the request.
        The page was previously always reporting "launcher not running" because
        every cross-origin fetch (and the JSON POST's preflight) was refused for
        want of these headers."""
        origin = self.headers.get("Origin")
        allow = None
        if origin is None:
            allow = "*"
        elif (origin == "null"                      # file:///... page
              or origin.startswith("http://127.0.0.1:")
              or origin.startswith("http://localhost:")
              or origin.startswith("http://[::1]")):  # a local app's preview pane
            allow = origin
        if allow:
            self.send_header("Access-Control-Allow-Origin", allow)

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        # preflight for the JSON POSTs (/run, /stop)
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html", "/limits-form.html"):
            # Serve the page itself from here. Opened over http://127.0.0.1:6969/
            # the page is SAME-ORIGIN with the bridge, so no CORS at all — the
            # file:// route still works (see _cors) but this is the easy one.
            try:
                with open(PAGE, "rb") as f:
                    body = f.read()
            except Exception as e:
                self._send(500, {"ok": False, "error": f"cannot read {PAGE}: {e}"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/limits":
            self._send(200, _limits_view())
        elif self.path == "/patterns":
            self._send(200, _pattern_list())
        elif self.path == "/status":
            with _lock:
                proc = _running["proc"]
                running = proc is not None and proc.poll() is None
                out = {"running": running,
                       "pattern": _running["pattern"] if running else None,
                       "pid": proc.pid if running else None,
                       "exit_code": None if (proc is None or running) else proc.returncode,
                       "live_channels": _live_channels(),
                       "zeroing": bool(_zero_thread is not None and _zero_thread.is_alive()),
                       "last_zero": dict(_last_zero),
                       "session": _session_state(),
                       "elapsed_s": (round(time.time() - _running["started"], 1)
                                     if running and _running.get("started") else None),
                       "log_tail": _tail()}
                # The box accepts and echoes power writes with nothing plugged
                # in, so an empty live-channel list means the run is going
                # nowhere — say it rather than let it look healthy.
                if out["live_channels"] == []:
                    out["warning"] = ("the box reports NO live channels — it is "
                                      "echoing the writes but nothing can be felt. "
                                      "Check the pads and the lead.")
                self._send(200, out)
        else:
            self._send(404, {"ok": False, "error": "not found",
                             "endpoints": ["/", "/limits", "/patterns", "/run", "/stop", "/status"]})

    def do_POST(self):
        if self.path == "/session/reset":
            ok, msg, st = _session_reset()
            self._send(200 if ok else 500, {"ok": ok, "message": msg, "session": st})
        elif self.path == "/limits":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, {"ok": False, "message": "bad JSON body"})
                return
            ok, msg = _apply_limits(body)
            self._send(200 if ok else 400, {"ok": ok, "message": msg,
                                            "limits": _limits_view()})
        elif self.path == "/run":
            try:
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                self._send(400, {"ok": False, "error": "bad JSON body"})
                return
            pattern = str(body.get("pattern", "")).strip()
            level = int(body.get("level", 0))
            secs = int(body.get("secs", 0))
            ok, msg = _run_pattern(pattern, level, secs)
            self._send(200 if ok else 400, {"ok": ok, "message": msg})
        elif self.path == "/stop":
            ok, msg = _stop()
            self._send(200 if ok else 500, {"ok": ok, "message": msg})
        else:
            self._send(404, {"ok": False, "error": "not found"})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=6969)
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"k250 launcher on http://127.0.0.1:{a.port}  (127.0.0.1 only — not network-reachable)")
    print(f"limits: {LIMITS}  power ceiling: {_power_ceiling()}%")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        _stop()
        print("\nstopped")


if __name__ == "__main__":
    main()
