#!/usr/bin/env python3
"""K250-4S live controller — ONE persistent BLE link, driven from a FIFO.

Why this exists: a fresh BLE connection costs ~5.7 s on this box (measured), while every
frame after that costs ~0.05 s. A spawned process per click therefore pays ~7 s to start
something that runs in milliseconds. Hold the link open and a run starts essentially at once.

Start (background):
  <repo>/venv/bin/python k250_ctl.py > /tmp/k250_ctl.log 2>&1 &
Drive it (the FIFO lives in the runtime dir, not the checkout — see runtime_dir()):
  printf '%s\n' 'run {"kind":"pattern","key":"tide","level":5,"secs":30}' > <repo>/ctl.fifo
  printf '%s\n' 'run {"kind":"stim","key":"edge/edge_1_continuous","level":20,"secs":60}' > <repo>/ctl.fifo
  printf '%s\n' stop         > <repo>/ctl.fifo     # abort the run AND zero the box
  printf '%s\n' read         > <repo>/ctl.fifo     # force a read-all
  printf '%s\n' read         > <repo>/ctl.fifo
  printf '%s\n' '{"PW":"10"}' > <repo>/ctl.fifo    # raw frame
  printf '%s\n' quit         > <repo>/ctl.fifo     # zero output + disconnect
  printf '%s\n' read         > <repo>/ctl.fifo

Two files in the same runtime dir, so other tools can find/call it:
  ctl.pid    — the live controller's pid. Absent/stale = no controller, use the spawn path.
  ctl.state  — the box's last reported state, so /status can still answer while the link
               is held (nothing else can connect while we hold it).

SAFETY: the box HOLDS power — it has no dead-man timer. So: every run zeroes on the way
out (finished, aborted, or failed), `stop` zeroes, `quit` zeroes, and a run has a hard
deadline. A held link means nothing else can reach the box, so anyone who needs to stop it
must come through here (or the physical kill switch).
"""
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from bleak import BleakClient                    # noqa: E402
from k250_codec import CHR, READ_ALL, runtime_dir  # noqa: E402
from k250_ble import K250, find                  # noqa: E402

# Runtime state lives OUTSIDE the checkout: a FIFO in the tree breaks any copy of it
# (shutil.copytree refuses a named pipe), and running the controller must never be able
# to break the test suite.
RUNTIME = runtime_dir()
FIFO = os.path.join(RUNTIME, "ctl.fifo")
PIDFILE = os.path.join(RUNTIME, "ctl.pid")
STATEFILE = os.path.join(RUNTIME, "ctl.state")
T0 = time.time()

# what is going on right now, so `stop` can reach it
RUN = {"task": None, "pl": None, "key": None}
FW = {"fv": None}


def log(*a):
    print(f"[{time.time()-T0:6.1f}]", *a, flush=True)


def _write_state(box):
    """Publish the box's state AND what we are doing, for /status.

    Nothing else can connect while we hold the link, so this file is the only way the
    page can still tell what the box is doing."""
    try:
        tmp = STATEFILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"box": box, "running": RUN["pl"] is not None,
                       "key": RUN.get("key"), "pid": os.getpid(),
                       "at": time.time()}, f)
        os.replace(tmp, STATEFILE)
    except Exception as e:
        log("could not write ctl.state:", e)


class Job:
    """The little namespace apply_limits() expects."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def build_player(k, spec, secs):
    """A Player carrying the same axes, caps and override rules as the spawned engine."""
    from k250_play import (Player, apply_limits, find_limits, load_limits,
                           ma_box_max_for, pa_writable_for, pw_scale_for)

    manual = bool(spec.get("manual"))
    lim_path = find_limits(spec.get("limits"))
    lim = load_limits(lim_path)
    # Manual mode hands in an EMPTY contract, so the ceiling falls out of the loop; the
    # wearer's Manual level is the override. Never implicit, always logged.
    ov = bool(spec.get("override_ceiling", manual))
    a = Job(hardcap=None, max_rate=None, ma_top=None, channel_caps="",
            base=spec.get("base"), peak=spec.get("peak"), override_ceiling=ov)
    a.hardcap, a.max_rate, a.ma_top, a.channel_caps = apply_limits(a, lim, override=ov)
    if isinstance(a.channel_caps, str) and a.channel_caps:
        try:
            a.channel_caps = json.loads(a.channel_caps)
        except Exception:
            log("ignoring bad channel_caps")
            a.channel_caps = ""

    pl = Player(k, a.hardcap)
    pl.ma_top = a.ma_top
    pl.ma_out_max = ma_box_max_for(FW["fv"])
    pl.pw_scale = pw_scale_for(FW["fv"])
    pl.pa_writable = pa_writable_for(FW["fv"])
    pl.max_rate = a.max_rate
    pl.override_power = ov
    if a.channel_caps:
        pl.channel_caps = a.channel_caps
    log(f"limits  : {lim_path or '(none)'}  ceiling {a.hardcap:g}%  freq {a.ma_top:g}"
        + ("  !! MANUAL OVERRIDE" if ov else ""))
    return pl


async def run_job(k, spec):
    """Play a pattern or a stim on the HELD link.

    The WHOLE body is inside the try/finally on purpose. The box holds power, and a
    half-built job must never leave RUN state set -- that would refuse every later run
    while claiming one was in progress. Any failure here clears the state and zeroes.
    """
    from k250_play import PATTERNS
    kind = spec.get("kind", "pattern")
    key = spec.get("key")
    secs = float(spec.get("secs") or 60)
    level = spec.get("level")
    if level is None:
        level = spec.get("base")
    try:
        level = float(level or 0)
    except Exception:
        level = 0.0
    if level <= 0:
        log("REFUSED: level 0 — nothing is sent at 0, on purpose")
        return

    pl = None
    RUN["key"] = f"{kind}:{key}"
    try:
        pl = build_player(k, spec, secs)
        await pl.prepare_channels()
        pl.deadline = time.time() + secs
        RUN["pl"] = pl
        log(f"PLAY {kind} {key} level={level:g}% secs={secs:g}")

        if kind == "stim":
            import stim_translate as T
            _cat, entry = T.find_stim(T.load_catalog(), key)
            if entry is None:
                log(f"REFUSED: unknown stim {key!r}")
                return
            tl, _bv = T.stim_timeline(entry)
            tl = [x for x in tl if x[0] <= secs]
            ef = T.flat(entry.get("effectFrequence"), 0) or 1.0
            ticks = T.to_k250(tl, level, ma_top=pl.ma_top, effect=ef)
            if not ticks:
                log("REFUSED: empty stim — nothing to play")
                return
            while not pl.stop and not pl.expired():
                cyc = time.time()
                for (t, pw, ma) in ticks:
                    if pl.stop or pl.expired():
                        break
                    dt = t - (time.time() - cyc)
                    if dt > 0:
                        await asyncio.sleep(dt)
                    await pl.ma(ma)
                    await pl.w(pw)
        else:
            fn = PATTERNS.get(key)
            if fn is None:
                log(f"REFUSED: unknown pattern {key!r}")
                return
            base = float(spec.get("base", level) or level)
            peak = float(spec.get("peak", level) or level)
            await fn(pl, base, peak, secs)
    except Exception as e:
        log("RUN FAILED:", type(e).__name__, e)
    finally:
        RUN["pl"] = None
        RUN["key"] = None
        # The box HOLDS power and has no dead-man timer. Zero on EVERY exit path.
        try:
            if pl is not None:
                await pl.w(0)
                await asyncio.sleep(0.15)
            await k.send({"PW": "0"})
            await asyncio.sleep(0.4)
            log("PW=0 (ended)")
        except Exception as e:
            # A cancelled job cannot always await its way through the zero -- the
            # controller zeroes directly on `stop`, so this is usually benign.
            log("note: this job could not zero on exit:", e,
                "(a stop should already have zeroed it — verify)")


async def main():
    if not hasattr(os, "mkfifo"):
        log("k250_ctl.py needs a FIFO, which Windows does not have.")
        log("  Use the pattern engine directly instead: "
            "python k250_play.py <pattern> --base N --secs N")
        log("  (it reads limits.json itself, so the ceiling still applies).")
        return 1
    if not os.path.exists(FIFO):
        os.mkfifo(FIFO)
    fd = os.open(FIFO, os.O_RDONLY | os.O_NONBLOCK)

    dev = await find()
    if dev is None:
        log("K250 not found")
        return 1
    log("found", dev.address, dev.name)

    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))

    quit_now = asyncio.Event()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_fifo():
        try:
            data = os.read(fd, 4096)
        except BlockingIOError:
            return
        for line in data.decode(errors="replace").splitlines():
            if line.strip():
                queue.put_nowait(line.strip())

    loop.add_reader(fd, on_fifo)

    try:
        async with BleakClient(dev, timeout=30) as cl:
            k = K250(cl)
            await k.start()
            log("notifications on; link HELD OPEN")
            await asyncio.sleep(0.4)
            await k.send(READ_ALL)
            await asyncio.sleep(0.6)
            FW["fv"] = (k.last or {}).get("FV")
            if k.last:
                _write_state(k.last)
            log("firmware:", FW["fv"])

            last_ping = time.time()
            while not quit_now.is_set():
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    line = None
                if line:
                    if line in ("quit", "q"):
                        break
                    if line == "stop" or line == "!":
                        # The ONLY way to stop a run while we hold the link.
                        #
                        # Setting `pl.stop` alone is NOT enough: it is a request that a
                        # pattern has to notice, and not every pattern checks it in every
                        # loop -- a stop that silently did not take left the box running.
                        # So: flag it, CANCEL the job, and zero from here regardless. A
                        # stop must never depend on the pattern's cooperation.
                        task = RUN["task"]
                        if RUN["pl"] is not None:
                            log("!! STOP requested — aborting and zeroing")
                        else:
                            log("stop: nothing running; zeroing anyway")
                        if RUN["pl"] is not None:
                            RUN["pl"].stop = True
                        if task is not None and not task.done():
                            task.cancel()
                        await k.send({"PW": "0"})
                        log("PW=0 (stop)")
                    elif line.startswith("run "):
                        try:
                            spec = json.loads(line[4:])
                        except Exception as e:
                            log("BAD RUN SPEC:", e)
                            continue
                        if RUN["task"] is not None and not RUN["task"].done():
                            log("REFUSED: a run is already going (send `stop` first)")
                        else:
                            RUN["task"] = asyncio.create_task(run_job(k, spec))
                    elif line == "read":
                        log("TX read-all")
                        await k.send(READ_ALL)
                    elif line.startswith("w0 "):
                        obj = json.loads(line[3:])
                        log("TX(noresp)", obj)
                        await cl.write_gatt_char(
                            CHR, json.dumps(obj, separators=(",", ":")).encode(),
                            response=False)
                    elif line.startswith("stream "):
                        _, payload, secs, ms = line.split()
                        obj = json.loads(payload)
                        t_end = time.time() + float(secs)
                        n = 0
                        while time.time() < t_end:
                            await k.send(obj)
                            n += 1
                            await asyncio.sleep(float(ms) / 1000.0)
                        log(f"streamed {obj} x{n} over {secs}s")
                        await k.send(READ_ALL)
                    else:
                        try:
                            obj = json.loads(line)
                        except Exception as e:
                            log("BAD JSON:", e)
                            continue
                        log("TX", obj)
                        await k.send(obj)
                        await asyncio.sleep(0.4)
                        log("TX read-all")
                        await k.send(READ_ALL)
                    await asyncio.sleep(0.3)
                if k.last:
                    _write_state(k.last)
                if time.time() - last_ping > 20:
                    await k.send(READ_ALL)
                    last_ping = time.time()

            if RUN["pl"] is not None:
                RUN["pl"].stop = True
                await asyncio.sleep(0.2)
            log("TX {'PW':'0'} (safe down)")
            await k.send({"PW": "0"})
            await asyncio.sleep(0.8)
            await k.send(READ_ALL)
            await asyncio.sleep(0.6)
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
    log("link closed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
