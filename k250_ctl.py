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
from k250_codec import ADDR, CHR, READ_ALL, runtime_dir  # noqa: E402
from k250_ble import K250, find                  # noqa: E402

# Runtime state lives OUTSIDE the checkout: a FIFO in the tree breaks any copy of it
# (shutil.copytree refuses a named pipe), and running the controller must never be able
# to break the test suite.
RUNTIME = runtime_dir()
FIFO = os.path.join(RUNTIME, "ctl.fifo")
PIDFILE = os.path.join(RUNTIME, "ctl.pid")
STATEFILE = os.path.join(RUNTIME, "ctl.state")
T0 = time.time()

# A dropped link is re-probed essentially forever (this is the held-link controller --
# its whole job is continuity), with a backoff that grows up to a cap so it never
# hammers a box that stopped advertising (asleep / off the remote-control screen).
# It only exits on `quit`. A box that never comes back just costs one find every
# backoff interval; giving up after a few seconds is what killed active sessions.
# Module level so the reconnect budget is testable.
RECONNECT_BACKOFF = 8.0
# Cap the re-probe gap LOW: a box in this testing drops and comes back within
# seconds, so a backoff that grew to 60s left the controller asleep for nearly a
# minute while the box was already back in range (dead air the operator felt). A
# 10s cap means worst-case pickup ~10s after the box returns, while a box that is
# genuinely gone (asleep / off the remote screen / cable out) still costs only one
# find every 10s -- not a hammer.
RECONNECT_BACKOFF_CAP = 10.0

# Signal floor (dBm): weaker than this at CONNECT time and the controller flags it.
# BLE is clean up to roughly -75 dBm; past ~-85 writes start dropping and a real drop
# is imminent. The box sits around -50 up close, so a weak connect is a loud warning.
WEAK_SIGNAL_DBM = -75

# Mid-session signal ladder (C2). The box keeps advertising while connected, so a
# sidecar sampler (k250_signal.py) reads its RSSI passively and writes signal.state;
# the controller applies this ladder each serve-loop tick.
#   warn    -- at/below this: log + surface it, keep playing (fragile but working).
#   cutoff  -- at/below this: CANCEL the run and zero the box WHILE the link is still
#              alive. A drop past ~-85 means we cannot zero on exit (no dead-man
#              timer), so cutting at -82 catches it coming, not after.
SIGNAL_WARN_DBM = -77
SIGNAL_CUTOFF_DBM = -82
SIGNALFILE = os.path.join(RUNTIME, "signal.state")

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


def _flag_signal(where, present, rssi):
    """Flag a weak connection to the operator at CONNECT time (initial or reconnect).

    RSSI is only meaningful when the box is present; a '?' (unknown) is logged but
    not flagged, and a present-but-unknown signal is not treated as weak. Weaker than
    WEAK_SIGNAL_DBM is a loud warning: writes may start dropping even while the link
    still echoes, and a real drop is imminent at that strength. This is the early
    tell the box's own state cannot give (BC/battery rides in the box's JSON and
    says nothing about the RF link).
    """
    if not present:
        return
    if rssi is None:
        log(f"!! signal (connect/{where}): unknown RSSI -- not flagged")
        return
    if rssi <= WEAK_SIGNAL_DBM:
        log(f"!! !! WEAK SIGNAL -- {where} at {rssi} dBm (floor {WEAK_SIGNAL_DBM}). "
            f"Connection is in range but fragile; a drop is imminent.")
    else:
        log(f"   signal (connect/{where}): {rssi} dBm -- good")


def signal_ladder(rssi):
    """Classify a mid-session RSSI: 'cutoff' (zero the box now), 'warn' (fragile,
    keep playing), or 'ok'. Pure so it is unit-testable. None (unknown) -> 'ok'
    (never cut on a missing reading -- the sampler may just be mid-scan)."""
    if rssi is None:
        return "ok"
    if rssi <= SIGNAL_CUTOFF_DBM:
        return "cutoff"
    if rssi <= SIGNAL_WARN_DBM:
        return "warn"
    return "ok"


async def probe_box_via_subprocess() -> bool:
    """Find the box from a FRESH interpreter (the path proven reliable).

    After a hard mid-run drop the controller's OWN in-process `find()` has been seen
    to miss a box that is perfectly visible (live, 2026-10-22: fresh-process finds
    returned in 0.1-2.9s every time while the long-running controller kept logging
    "box still off the air"). BlueZ can wedge per-process discovery state behind a
    dropped connection. Shelling the probe out side-steps that entirely -- each probe
    is a brand-new BlueZ discovery.

    Matches by NAME/UUID, never by ADDR: the box's address is a random static LE
    address that changes on power-cycle, and the codec's ADDR is a placeholder that
    never matches a real device. `find()` with no address matches on name ("Kx250")
    or service UUID, which is the durable identity.

    Returns (present, rssi): True + the box's signal strength (dBm, int) when it is on
    the air, (False, None) when it is not or the probe failed -- caller treats that as
    "not yet", never as a hard answer. The RSSI is what lets us flag a weak CONNECT.
    """
    import subprocess
    import textwrap
    script = textwrap.dedent(
        f"""\
        import asyncio, sys
        sys.path.insert(0, {HERE!r})
        from k250_ble import find
        async def _m():
            d = await find(timeout=8)
            if d is None:
                print("NOBOX")
            else:
                rssi = getattr(d, "rssi", None)
                print("BOX", rssi if rssi is not None else "?")
        asyncio.run(_m())
        """
    )
    try:
        r = subprocess.run([sys.executable, "-c", "exec(" + repr(script) + ")"],
                           capture_output=True, timeout=15, cwd=HERE)
        out = r.stdout.decode(errors="replace").strip()
        if "BOX" in out:
            parts = out.split()
            try:
                return True, int(parts[1])
            except (IndexError, ValueError):
                return True, None
        return False, None
    except Exception as e:
        log("   (probe subprocess failed:", type(e).__name__, e, ")")
        return False, None


async def hold_link(queue, quit_now):
    """Connect once, serve the FIFO until `quit` or the link dies.

    Returns the reason:
      "quit" -- the operator asked, cleanly (box already zeroed on the way out).
      "lost" -- the link died; the box may hold its last power and must be
                 verified/zeroed on the next connect.

    The zero-on-every-exit and stop-handling the code above relies on live here:
    a drop mid-run leaves the box energised (no dead-man timer), so the caller
    reconnects and zeroes rather than silently walking away.
    """
    async with BleakClient(ADDR, timeout=30) as cl:
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
                if not cl.is_connected:
                    log("!! LINK LOST -- refusing the command, the box is not reachable")
                    return "lost"
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
                        log("!! STOP requested -- aborting and zeroing")
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
            if not cl.is_connected:
                # The link dying must never take the process down silently: the
                # bridge falls back to spawning without a word, the page just gets
                # slow again, and nobody learns why. Say it, and get out to the
                # reconnect loop.
                log("!! LINK LOST -- the box disconnected")
                if RUN["pl"] is not None:
                    log("!! a run was in progress; the box holds its last power -- "
                        "it must be zeroed on reconnect")
                return "lost"
            if time.time() - last_ping > 20:
                try:
                    await k.send(READ_ALL)
                except Exception as e:
                    log("!! keepalive failed:", type(e).__name__, e)
                    return "lost"
                last_ping = time.time()

            # ---- mid-session signal ladder (C2) ----
            # Read the sidecar sampler's RSSI and apply warn/cutoff. The cutoff is the
            # safety win: it cancels the run and zeroes the box WHILE the link is still
            # alive, because past ~-85 dBm a drop means we cannot zero on exit (no
            # dead-man timer). Catch it coming, not after.
            try:
                with open(SIGNALFILE) as _sf:
                    _sig = json.load(_sf)
                _rssi = _sig.get("rssi")
                _ladder = signal_ladder(_rssi)
                if _ladder == "cutoff":
                    log(f"!! !! SIGNAL CUTOFF -- {_rssi} dBm (floor {SIGNAL_CUTOFF_DBM}). "
                        "Cancelling the run and zeroing while the link is still alive.")
                    _task = RUN["task"]
                    if RUN["pl"] is not None:
                        RUN["pl"].stop = True
                    if _task is not None and not _task.done():
                        _task.cancel()
                    try:
                        await k.send({"PW": "0"})
                        log("   PW=0 (signal cutoff)")
                    except Exception as _e:
                        log("   could not zero on signal cutoff:", type(_e).__name__, _e)
                elif _ladder == "warn":
                    log(f"!! weak signal -- {_rssi} dBm (warn floor {SIGNAL_WARN_DBM}); "
                        "still playing but fragile")
            except FileNotFoundError:
                pass
            except Exception as _e:
                log("   (signal read failed:", type(_e).__name__, _e, ")")

        if RUN["pl"] is not None:
            RUN["pl"].stop = True
            await asyncio.sleep(0.2)
        log("TX {'PW':'0'} (safe down)")
        try:
            await k.send({"PW": "0"})
            await asyncio.sleep(0.8)
            await k.send(READ_ALL)
            await asyncio.sleep(0.6)
        except Exception as e:
            log("!! could not zero on the way out:", type(e).__name__, e)
            log("!! the box may still be energised -- use the physical kill switch")
    return "quit"


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

    # Spawn the signal sampler (sidecar process) so we can watch RSSI mid-session.
    # It only scans passively -- never connects -- so it cannot wedge the held link.
    sampler = None
    try:
        import subprocess
        sampler = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "k250_signal.py")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("signal sampler started (pid", sampler.pid, ")")
    except Exception as e:
        log("   (could not start signal sampler:", type(e).__name__, e, ")")

    try:
        # The controller is a held-link server: it must not die just because the box
        # was off the air when it launched (asleep / left the remote-control screen /
        # cable out). Startup discovery shares the same patient re-probe loop as a
        # mid-session drop -- find until it has a box or the operator sends `quit`.
        box_present, rssi = await probe_box_via_subprocess()
        _flag_signal("connect", box_present, rssi)
        backoff = RECONNECT_BACKOFF
        while not quit_now.is_set():
            # Wait for the box before attempting a connection. The probe runs in a
            # FRESH interpreter because -- verified live -- the controller's own long-
            # lived in-process find() misses an advertising box after a hard drop,
            # while a fresh process finds it every time. Never attempt hold_link until
            # the probe confirms the box is actually on the air.
            if not box_present:
                # After a drop the box is EXPECTED to come back, so probe fast and
                # continuously (no long sleep up front) -- a box that re-advertises is
                # grabbed the moment it does. Only grow the backoff between failed
                # probes, so a box that has genuinely been gone a while stops hammering.
                box_present, rssi = await probe_box_via_subprocess()
                if not box_present:
                    try:
                        import subprocess
                        subprocess.run(["bluetoothctl", "disconnect", ADDR],
                                       capture_output=True, timeout=10)
                    except Exception as e:
                        log("   (could not clear the stale link:", type(e).__name__, e, ")")
                    log(f"!! box still off the air -- next probe in {backoff:.0f}s ...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, RECONNECT_BACKOFF_CAP)
                    continue
                else:
                    backoff = RECONNECT_BACKOFF   # reset: fresh link
                    _flag_signal("reconnect", True, rssi)

            try:
                reason = await hold_link(queue, quit_now)
            except Exception as e:
                # An unexpected exception in the serve loop must NEVER silently kill the
                # controller -- a dead controller leaves the box energised (no dead-man
                # timer) and there is no one left to zero it. Log it, try to zero the box
                # straight from here (via a fresh k250-stop, the reliable path), and go
                # back to re-probing.
                log("!! SERVE-LOOP ERROR:", type(e).__name__, e)
                try:
                    import subprocess
                    stop_script = ("import asyncio;"
                                   "from k250_stop import main as s;"
                                   "raise SystemExit(asyncio.run(s()))")
                    subprocess.run([sys.executable, "-c", stop_script],
                                   capture_output=True, timeout=15, cwd=HERE)
                    log("   attempted PW=0 via fresh k250-stop after serve-loop error")
                except Exception as e2:
                    log("   could not zero after serve-loop error:",
                        type(e2).__name__, e2)
                box_present = False
                continue
            if reason == "quit" or quit_now.is_set():
                break
            # LINK LOST. The box may be energised; do NOT auto-resume an interrupted run --
            # the operator re-launches. Drop dev so the top of the loop re-probes for it.
            log("!! LINK LOST -- dropped the box handle; re-probing (not auto-resuming)")
            box_present = False
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
        if sampler is not None:
            try:
                sampler.terminate()
                log("signal sampler stopped")
            except Exception as e:
                log("   (could not stop signal sampler:", type(e).__name__, e, ")")
    log("link closed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
