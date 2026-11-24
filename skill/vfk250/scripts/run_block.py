#!/usr/bin/env python3
"""vfk250 run_block — one bounded K250 pattern block, launched detached.

V2 contract:
  * The CEILING comes from the caller (--cap / --cap2): the wearer's word this
    session, or the companion web page. There is no limits file on this path.
  * One ownership check, one launch, one JSON record. No retries, no recovery.
  * Playback continues independently of this launcher; read the log for PLAY
    and the final fresh PW-zero echoes. Never wrap this in `timeout` or pipe it
    to `head`.

Usage:
  run_block.py --cap 50 --level 30 --secs 300 --pattern swell
  run_block.py --cap 50 --level 30 --secs 300 --pattern swell --base 10 --peak 25
  run_block.py --cap 50 --level 30 --secs 300 --pattern swell \
               --cap2 25 --level2 12 --pattern2 tide
Env:
  VK250_PY  python interpreter with bleak installed (default: this one)
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from link_status import snapshot


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cap', required=True, type=float,
                   help='channel-1 ceiling in percent — from the wearer/page, this session')
    p.add_argument('--level', required=True, type=float,
                   help='channel-1 flat level (sets base == peak)')
    p.add_argument('--base', type=float, default=None,
                   help='channel-1 power floor %% (default: --level)')
    p.add_argument('--peak', type=float, default=None,
                   help='channel-1 power peak %% (default: --level)')
    p.add_argument('--secs', required=True, type=int)
    p.add_argument('--pattern', required=True, help="driver validates against its pattern table")
    p.add_argument('--cap2', type=float, default=None, help='channel-2 ceiling (dual mode)')
    p.add_argument('--level2', type=float, default=None)
    p.add_argument('--pattern2', default=None)
    p.add_argument('--frequency', type=float, default=2000.0)
    p.add_argument('--slew', type=float, default=0.0)
    a = p.parse_args(argv)

    dual = a.level2 is not None or a.pattern2 is not None or a.cap2 is not None
    # ch1 envelope. --level is the flat default; --base/--peak override it so a block
    # can ramp between two powers (the driver has always taken them -- the launcher
    # collapsed both onto --level).
    ch1_base = a.level if a.base is None else a.base
    ch1_peak = a.level if a.peak is None else a.peak
    errs = []
    if not all(math.isfinite(v) for v in (a.cap, a.level, ch1_base, ch1_peak)):
        errs.append('non-finite cap/level/base/peak')
    if not (0 < a.level <= a.cap <= 100):
        errs.append('require 0 < level <= cap <= 100')
    if not (0 <= ch1_base <= 100 and 0 <= ch1_peak <= 100):
        errs.append('require 0 <= base/peak <= 100')
    if max(ch1_base, ch1_peak) > a.cap:
        errs.append('ch1 base/peak exceeds cap')
    if not (1 <= a.secs <= 3600):
        errs.append('secs must be 1..3600')
    if dual:
        if not (a.level2 is not None and a.pattern2 is not None and a.cap2 is not None):
            errs.append('dual mode needs --level2, --pattern2 and --cap2 together')
        elif not (0 <= a.level2 <= a.cap2 <= 100):
            errs.append('require 0 <= level2 <= cap2 <= 100')
    # Fail fast on a pattern the driver will refuse. `--pattern` used to be left to
    # "the driver validates", but the launcher detached the child, printed LAUNCHED and
    # exited 0 for a run that never happened -- a failed attempt reported as a success,
    # which the V2 contract forbids. Same table the driver uses.
    # The name table lives in k250_codec (stdlib-only), so this pre-check works
    # even where the launcher's python has no bleak — k250_play asserts its live
    # PATTERNS table matches the codec set at driver import (drift fails there).
    from k250_codec import PATTERN_NAMES as _PATTERNS
    for _nm in (a.pattern, a.pattern2 if dual else None):
        if _nm and _nm not in _PATTERNS:
            errs.append(f'unknown pattern {_nm!r}')
    if errs:
        print('REFUSED: ' + '; '.join(errs), flush=True)
        return 2

    state = snapshot()
    if not state['assessment'].startswith('IDLE:'):
        print('BLOCKED: ' + state['assessment'] + ' Report once; do not retry or recover.',
              flush=True)
        return 3

    py = os.environ.get('VK250_PY', sys.executable)
    cmd = [py, str(SCRIPTS / 'k250_play2.py'),
           a.pattern, '--base', str(ch1_base), '--peak', str(ch1_peak),
           '--secs', str(a.secs), '--hardcap', str(a.cap),
           '--frequency', str(a.frequency), '--slew', str(a.slew)]
    if dual:
        cmd += ['--pattern2', a.pattern2,
                '--base2', str(a.level2), '--peak2', str(a.level2),
                '--hardcap2', str(a.cap2)]

    runtime = Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'vfk250'
    runtime.mkdir(parents=True, exist_ok=True)
    logfile = runtime / f'block-{time.time_ns()}.log'
    # Private process session + file stdout: a timeout/kill on THIS launcher
    # cannot take playback down with it. start_new_session is POSIX-only and is
    # silently ignored on Windows, so Windows gets its own isolation: a new
    # process GROUP (so a Ctrl+C in the launcher's console never reaches the
    # detached driver) with no console window of its own.
    detach: dict = {}
    if os.name == 'nt':
        detach['creationflags'] = (subprocess.CREATE_NEW_PROCESS_GROUP
                                   | getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    else:
        detach['start_new_session'] = True
    with logfile.open('wb') as output:
        child = subprocess.Popen(cmd, cwd=str(SCRIPTS), stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT,
                                 close_fds=True, **detach)
    record = {'pid': child.pid, 'log': str(logfile), 'secs': a.secs,
              'level': a.level, 'base': ch1_base, 'peak': ch1_peak,
              'cap': a.cap, 'pattern': a.pattern,
              'level2': a.level2 if dual else None,
              'pattern2': a.pattern2 if dual else None,
              'cap2': a.cap2 if dual else None,
              'started_at': time.time(), 'state': 'STARTING_NOT_VERIFIED'}
    (runtime / 'block-last.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
    print('LAUNCHED independently. Read the log to verify PLAY and the eventual fresh zero '
          'echoes. Do not relaunch because this launcher exited. Stop with k250_stop.py.',
          file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
