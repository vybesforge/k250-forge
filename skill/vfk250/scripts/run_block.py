#!/usr/bin/env python3
"""vfk250 run_block — one bounded K250 pattern block, launched detached.

V2 contract:
  * The CEILING comes from the caller (--cap / --cap2): the wearer's word this
    session, or the companion web page. There is no limits file on this path.
  * One ownership check, one launch, one JSON record. No retries, no recovery.
  * Playback continues independently of this launcher; read the log for PLAY
    and the final fresh PW=*** echoes. Never wrap this in `timeout` or pipe it
    to `head`.

Usage:
  run_block.py --cap 50 --level 30 --secs 300 --pattern swell
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
    p.add_argument('--level', required=True, type=float)
    p.add_argument('--secs', required=True, type=int)
    p.add_argument('--pattern', required=True, help="driver validates against its pattern table")
    p.add_argument('--cap2', type=float, default=None, help='channel-2 ceiling (dual mode)')
    p.add_argument('--level2', type=float, default=None)
    p.add_argument('--pattern2', default=None)
    p.add_argument('--frequency', type=float, default=2000.0)
    p.add_argument('--slew', type=float, default=0.0)
    a = p.parse_args(argv)

    dual = a.level2 is not None or a.pattern2 is not None or a.cap2 is not None
    errs = []
    if not all(math.isfinite(v) for v in (a.cap, a.level)):
        errs.append('non-finite cap/level')
    if not (0 < a.level <= a.cap <= 100):
        errs.append('require 0 < level <= cap <= 100')
    if not (1 <= a.secs <= 3600):
        errs.append('secs must be 1..3600')
    if dual:
        if not (a.level2 is not None and a.pattern2 is not None and a.cap2 is not None):
            errs.append('dual mode needs --level2, --pattern2 and --cap2 together')
        elif not (0 <= a.level2 <= a.cap2 <= 100):
            errs.append('require 0 <= level2 <= cap2 <= 100')
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
           a.pattern, '--base', str(a.level), '--peak', str(a.level),
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
    # cannot take playback down with it.
    with logfile.open('wb') as output:
        child = subprocess.Popen(cmd, cwd=str(SCRIPTS), stdin=subprocess.DEVNULL,
                                 stdout=output, stderr=subprocess.STDOUT,
                                 start_new_session=True, close_fds=True)
    record = {'pid': child.pid, 'log': str(logfile), 'secs': a.secs,
              'level': a.level, 'cap': a.cap, 'pattern': a.pattern,
              'level2': a.level2 if dual else None,
              'pattern2': a.pattern2 if dual else None,
              'cap2': a.cap2 if dual else None,
              'started_at': time.time(), 'state': 'STARTING_NOT_VERIFIED'}
    (runtime / 'block-last.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record), flush=True)
    print('LAUNCHED independently. Read the log to verify PLAY and the eventual fresh PW=*** '
          'echoes. Do not relaunch because this launcher exited. Stop with k250_stop.py.',
          flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
