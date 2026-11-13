#!/usr/bin/env python3
"""HR-driven K250 — the dial follows the wearer's heart, not a script.

Reads a standard BLE heart-rate service (0x180D / 0x2A37) — e.g. a Garmin watch.

Two ways to use it:

  python hr_drive.py                 # DRIVER: creeps/eases raw PW frames into the
                                     # held-link controller's FIFO (~/.cache/k250/ctl.fifo)
  python hr_drive.py --trace FILE    # FEED: append {"hr":N} samples to FILE so an
                                     # engine pattern (hr_follow / mirror) can ride them
  python hr_drive.py --trace F --feed-only
                                     # FEED ONLY: stream HR to F and do NOT touch the
                                     # box — use when a pattern is driving (never both)

The FD feed exists because exactly one process may hold the watch link, and exactly
one may drive the box: so the pattern reads a trace file this driver writes, rather
than opening a second watch connection mid-scene.

The rule he asked for: "force it higher". So it creeps the level up while his heart is
coasting, and only stops when he is actually working. It also EASES on a spike — this is
a person wearing electrodes, not a game.

Safety:
  - hard per-run ceiling MAX_LEVEL (68) — well under his file ceiling of 85
  - floor MIN_LEVEL (20) so a stale read can't strand him at nothing
  - if HR goes stale (>5 s) it holds LOW instead of escalating
  - any exception / SIGTERM writes PW 0 on the way out
"""
import asyncio, json, os, signal, sys, time

# The heart-rate strap's BLE address is a per-wearer device, so it is NEVER hardcoded.
# Set GARMIN_HR_ADDR (or the address resolves from ~/.config/k250/hr-watch.addr) and it
# is consumed once at startup; absent one, the driver refuses rather than guess at a
# MAC that could belong to someone else's body.
HR_CHR  = "00002a37-0000-1000-8000-00805f9b34fb"   # standard BLE Heart Rate Measurement
FIFO    = os.path.expanduser("~/.cache/k250/ctl.fifo")
LOG     = os.path.expanduser("~/.cache/k250/hr-drive.log")


def resolve_addr():
    env = os.environ.get("GARMIN_HR_ADDR", "").strip()
    if env:
        return env
    conf = os.path.expanduser("~/.config/k250/hr-watch.addr")
    try:
        with open(conf) as fh:
            addr = fh.read().strip()
        if addr:
            return addr
    except FileNotFoundError:
        pass
    sys.exit("error: no heart-rate watch address. Set GARMIN_HR_ADDR or put one MAC "
             "in ~/.config/k250/hr-watch.addr (one line). Refusing to guess a MAC.")

MIN_LEVEL   = int(os.environ.get("HR_MIN_LEVEL", 20))
MAX_LEVEL   = int(os.environ.get("HR_MAX_LEVEL", 68))
FLOOR_BPM   = int(os.environ.get("HR_PUSH_BPM", 105))   # below this, the wearer is coasting -> push
SPIKE_BPM   = int(os.environ.get("HR_EASE_BPM", 145))   # above this, EASE
# Wearer calibration (measured live): high effort reads as pain well above 110 bpm, so
# 90-110 bpm is NOT a spike and must not trigger easing. Default ease threshold 145.
STEP_UP     = 2
STEP_DOWN   = 6
TICK        = 1.0

level = 25.0
last_hr = None
last_hr_at = 0.0
fifo = None
TRACE = None          # path to append {"hr":N} samples to (from --trace)
FEED_ONLY = False     # when True: stream HR to TRACE and never write to the box


def write_trace(hr):
    """Append one {"hr":N} JSONL sample. Never raises into the loop."""
    if not TRACE:
        return
    try:
        with open(TRACE, "a") as fh:
            fh.write(json.dumps({"hr": int(hr), "t": round(time.time(), 3)}) + "\n")
    except Exception as e:
        log("trace write failed: %s" % e)


def log(msg):
    line = "%s %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


def send_pw(value):
    global fifo
    if FEED_ONLY:
        return                     # a pattern is driving; this process only feeds HR
    n = int(round(max(0, min(MAX_LEVEL, value))))
    try:
        if fifo is None:
            fifo = open(FIFO, "w", buffering=1)
        fifo.write('{"PW":"%d"}\n' % n)
        fifo.flush()
    except Exception as e:
        log("FIFO write failed: %s" % e)
        fifo = None


def parse_hr(data: bytearray) -> int:
    return int.from_bytes(data[1:3], "little") if data[0] & 0x01 else data[1]


async def read_hr():
    """Subscribe forever, keeping last_hr fresh."""
    global last_hr, last_hr_at
    from bleak import BleakClient
    while True:
        try:
            async with BleakClient(resolve_addr(), timeout=25) as c:
                log("watch connected — HR streaming")
                def cb(_, data):
                    global last_hr, last_hr_at
                    last_hr = parse_hr(data)
                    last_hr_at = time.time()
                    write_trace(last_hr)
                await c.start_notify(HR_CHR, cb)
                while c.is_connected:
                    await asyncio.sleep(2)
            log("watch disconnected — reconnecting")
        except Exception as e:
            log("watch error: %s — retrying in 5s" % e)
        await asyncio.sleep(5)


def shutdown(*_):
    if FEED_ONLY:
        log("SHUTDOWN — feed-only, leaving the box to the pattern")
        sys.exit(0)
    log("SHUTDOWN — writing PW 0")
    try:
        if fifo is None:
            f = open(FIFO, "w", buffering=1)
        else:
            f = fifo
        f.write('{"PW":"0"}\n')
        f.flush()
    except Exception as e:
        log("zero on exit FAILED: %s" % e)
    sys.exit(0)


async def main():
    global level, TRACE, FEED_ONLY
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--trace" and i + 1 < len(args):
            TRACE = os.path.expanduser(args[i + 1])
        elif a.startswith("--trace="):
            TRACE = os.path.expanduser(a.split("=", 1)[1])
        elif a == "--feed-only":
            FEED_ONLY = True
    if FEED_ONLY and not TRACE:
        sys.exit("error: --feed-only needs --trace FILE (there is nothing else to do)")
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    if FEED_ONLY:
        log("start (feed-only): streaming HR to %s, NOT driving the box" % TRACE)
    else:
        log("start: floor %d, spike %d, level cap %d%s"
            % (FLOOR_BPM, SPIKE_BPM, MAX_LEVEL, ("  trace->" + TRACE) if TRACE else ""))
        send_pw(level)
    asyncio.create_task(read_hr())
    while True:
        await asyncio.sleep(TICK)
        now = time.time()
        if last_hr is None or (now - last_hr_at) > 5:
            if not FEED_ONLY and level > 30:
                level = 30
                log("HR stale — holding low at %d" % level)
                send_pw(level)
            continue
        if FEED_ONLY:
            continue               # the trace is the whole output; nothing to drive
        hr = last_hr
        why = None
        if hr > SPIKE_BPM:
            level -= STEP_DOWN
            why = "EASING"
        elif hr < FLOOR_BPM:
            level += STEP_UP
            why = "pushing"
        level = max(MIN_LEVEL, min(MAX_LEVEL, level))
        if why:
            # log the value actually SENT, not the pre-clamp intent — a safety
            # trail that reports 70 when 68 went out is a lie in the only record.
            log("HR %d — %s to %d" % (hr, why, level))
        send_pw(level)


if __name__ == "__main__":
    asyncio.run(main())
