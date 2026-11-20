#!/usr/bin/env python3
"""Read-only K250 ownership snapshot. No scan, FIFO writes, BLE connection or recovery."""
import json
import os
from pathlib import Path
import subprocess
import time

DRIVERS = {"k250_ctl.py", "k250_play.py", "k250_play2.py", "k250_stim_play.py", "hr_drive.py"}


def processes(proc=Path('/proc')):
    found = []
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        try:
            args = (p / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
            # Match actual Python argv, never a shell containing a quoted command.
            if not args or 'python' not in Path(args[0]).name.lower():
                continue
            script = next((Path(a).name for a in args[1:] if Path(a).name in DRIVERS), None)
            if script:
                found.append({'pid': int(p.name), 'script': script, 'argv': args})
        except (OSError, UnicodeError):
            continue
    return sorted(found, key=lambda x: x['pid'])


def snapshot(runtime=None, proc=Path('/proc'), connected=None, now=None):
    runtime = runtime or Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'k250'
    now = time.time() if now is None else now
    drivers = processes(proc)
    controllers = [p for p in drivers if p['script'] == 'k250_ctl.py']
    result = {'drivers': drivers, 'controller_count': len(controllers),
              'power': 'UNKNOWN: read-all does not report PW; a link is not proof of output'}
    try:
        pid = int((runtime / 'ctl.pid').read_text().strip())
    except (OSError, ValueError):
        pid = None
    result['pidfile'] = pid
    result['pidfile_matches_controller'] = any(p['pid'] == pid for p in controllers)
    try:
        state = json.loads((runtime / 'ctl.state').read_text())
        if not isinstance(state, dict):
            raise ValueError('invalid cache object')
        age = now - float(state['at'])
        owner = state.get('pid')
        valid = (result['pidfile_matches_controller'] and owner == pid and 0 <= age <= 30)
        result['cache'] = {'age_seconds': round(age, 1), 'owner_pid': owner,
                           'valid_owner_and_age': valid, 'state': state}
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
        result['cache'] = None
    if connected is None:
        try:
            r = subprocess.run(['bluetoothctl', 'devices', 'Connected'],
                               capture_output=True, text=True, timeout=5)
            connected = (r.stdout.splitlines() if r.returncode == 0
                         and 'No default controller' not in r.stdout else None)
        except (OSError, subprocess.TimeoutExpired):
            connected = None
    result['bluez_connected'] = connected
    box_connected = bool(connected and any('kx250' in s.lower() for s in connected))
    if len(controllers) > 1:
        action = 'CONFLICT: multiple controllers. Do not scan, connect, reset or start another driver.'
    elif drivers:
        action = 'BUSY: existing driver owns/may own the link. Do not start a second BLE client.'
        if controllers and not valid:
            action += ' Controller cache is stale/unverified; not proof of a working link.'
    elif box_connected:
        action = 'CONNECTED_WITHOUT_KNOWN_DRIVER: do not assume asleep or disconnect blindly; identify owner first.'
    elif connected is None:
        action = 'UNKNOWN: BlueZ query failed. Do not treat this as an idle adapter.'
    else:
        action = 'IDLE: no known driver or named connected box. A single k250-status probe is now appropriate.'
    result['assessment'] = action
    return result


def main():
    import argparse
    argparse.ArgumentParser(description=__doc__).parse_args()
    print(json.dumps(snapshot(), indent=2))


if __name__ == '__main__':
    main()
