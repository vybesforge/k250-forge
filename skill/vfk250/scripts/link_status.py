#!/usr/bin/env python3
"""Read-only K250 ownership snapshot. No scan, FIFO writes, BLE connection or recovery.

Cross-platform. It answers the same two questions on every host -- which driver
processes own the box, and whether a box is currently connected -- using whatever
the platform offers, and never opening a BLE link itself:

  * Linux  : /proc for processes, `bluetoothctl devices Connected` for the link.
  * Windows: CIM (`Get-CimInstance Win32_Process`) and the Bluetooth PnP tree.
  * macOS  : `ps -eo` (there is no /proc) and `system_profiler SPBluetoothDataType`.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

DRIVERS = {"k250_ctl.py", "k250_play.py", "k250_play2.py", "k250_stim_play.py", "hr_drive.py"}

IS_WIN = os.name == "nt"


# --------------------------------------------------------------------------
# process discovery
# --------------------------------------------------------------------------

def processes(proc=Path('/proc')):
    """Driver processes on POSIX, read from /proc. See _collect() for Windows."""
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


# PowerShell one-liners. Each is a single argv entry handed straight to
# CreateProcess, so there is no shell to quote against; and the format string is
# single-quoted so no inner double quotes have to survive that trip either.
_PS_UTF8 = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
_PS_PROCS = (
    "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
    "Where-Object { $_.Name -match 'python' -and "
    "$_.CommandLine -match 'k250_ctl\\.py|k250_play\\.py|k250_play2\\.py|k250_stim_play\\.py|hr_drive\\.py' } | "
    "ForEach-Object { '{0}~{1}' -f $_.ProcessId, $_.CommandLine }"
)
# A Bluetooth peripheral's device node exists only while it is CONNECTED, so
# `-PresentOnly` is the closest Windows equivalent of `bluetoothctl devices
# Connected`. The host's own controllers/enumerators/services are present too —
# they never match the box name, so the caller's filter drops them.
_PS_CONNECTED = (
    "Get-PnpDevice -PresentOnly -Class Bluetooth -ErrorAction SilentlyContinue | "
    "Select-Object -ExpandProperty FriendlyName"
)


def _run_ps(command, timeout=12):
    """Run one PowerShell one-liner. Returns (stdout, ok). ok=False means the probe
    itself could not run — callers must report that, never read it as 'nothing
    found' (an unreadable process list is not an idle adapter)."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            _PS_UTF8 + command],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if r.returncode != 0:
        return None, False
    return r.stdout, True


def parse_windows_procs(text):
    """'pid~commandline' lines -> driver records shaped like the POSIX ones."""
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        pid_s, sep, args = line.partition('~')
        if not sep or not pid_s.strip().isdigit():
            continue
        if 'python' not in args.lower():
            continue
        script = next((Path(m).name for m in re.findall(r'[A-Za-z0-9_.\-]+\.py', args)
                       if Path(m).name in DRIVERS), None)
        if script:
            found.append({'pid': int(pid_s), 'script': script, 'argv': args.split()})
    return sorted(found, key=lambda x: x['pid'])


def _processes_ps():
    """Driver processes via `ps -eo`, for hosts with no /proc (macOS)."""
    r = subprocess.run(["ps", "-eo", "pid=,comm=,args="],
                       capture_output=True, text=True, timeout=10)
    if r.returncode != 0:
        raise OSError(f'ps exited {r.returncode}')
    found = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        # Scan ALL tokens: comm paths may contain spaces (a venv under
        # "My Documents"), so never rely on a fixed pid/comm/args column split.
        if not any('python' in tok.lower() for tok in parts[1:]):
            continue
        script = next((Path(tok).name for tok in parts[1:] if Path(tok).name in DRIVERS), None)
        if script:
            found.append({'pid': int(parts[0]), 'script': script, 'argv': parts[1:]})
    return sorted(found, key=lambda x: x['pid'])


def _collect(proc=Path('/proc')):
    """(driver records, listing_ok). listing_ok=False is a hard 'we do not know'."""
    try:
        if IS_WIN:
            out, ok = _run_ps(_PS_PROCS, timeout=20)
            return (parse_windows_procs(out) if ok and out else []), ok
        if sys.platform == 'darwin':
            return _processes_ps(), True
        return processes(proc), True
    except (OSError, subprocess.TimeoutExpired):
        return [], False


def _connected_macos(timeout=15):
    """Connected Bluetooth peripherals on macOS, which has no bluetoothctl.
    `system_profiler SPBluetoothDataType` groups devices under a 'Connected:'
    heading; only those are returned, so a paired-but-idle box is not read as a
    live link. Verified against hardware."""
    try:
        r = subprocess.run(["system_profiler", "SPBluetoothDataType"],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    out, inside, base = [], False, 0
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        if s == 'Connected:':
            inside, base = True, indent
            continue
        if inside:
            if indent <= base:
                inside = False              # left the Connected block
            elif s.endswith(':'):
                out.append(s[:-1].strip())  # a device name
    return out


def connected_devices():
    """Names of currently connected Bluetooth peripherals, or None if the probe
    could not run. Linux: `bluetoothctl devices Connected`. Windows: present
    Bluetooth PnP nodes (a node exists only while the peripheral is connected).
    macOS: devices under `system_profiler SPBluetoothDataType`'s 'Connected:'
    heading."""
    if IS_WIN:
        out, ok = _run_ps(_PS_CONNECTED, timeout=10)
        if not ok:
            return None
        return [s.strip() for s in out.splitlines() if s.strip()]
    if sys.platform == 'darwin':
        return _connected_macos()
    try:
        r = subprocess.run(['bluetoothctl', 'devices', 'Connected'],
                           capture_output=True, text=True, timeout=5)
        return (r.stdout.splitlines() if r.returncode == 0
                and 'No default controller' not in r.stdout else None)
    except (OSError, subprocess.TimeoutExpired):
        return None


def snapshot(runtime=None, proc=Path('/proc'), connected=None, now=None):
    runtime = runtime or Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'k250'
    now = time.time() if now is None else now
    drivers, procs_ok = _collect(proc)
    controllers = [p for p in drivers if p['script'] == 'k250_ctl.py']
    result = {'drivers': drivers, 'controller_count': len(controllers),
              'platform': sys.platform, 'process_listing_ok': procs_ok,
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
        connected = connected_devices()
    result['connected_devices'] = connected
    result['bluez_connected'] = connected          # kept for the web app's existing readers
    box_connected = bool(connected and any('kx250' in s.lower() for s in connected))
    if len(controllers) > 1:
        action = 'CONFLICT: multiple controllers. Do not scan, connect, reset or start another driver.'
    elif drivers:
        action = 'BUSY: existing driver owns/may own the link. Do not start a second BLE client.'
        if controllers and not valid:
            action += ' Controller cache is stale/unverified; not proof of a working link.'
    elif not procs_ok:
        action = ('UNKNOWN: the driver-process listing failed on this host, so whether a '
                  'driver owns the box cannot be told. Do not treat this as an idle adapter.')
    elif box_connected:
        action = 'CONNECTED_WITHOUT_KNOWN_DRIVER: do not assume asleep or disconnect blindly; identify owner first.'
    elif connected is None:
        action = ('UNKNOWN: the link query failed (bluetoothctl on Linux, the Bluetooth PnP '
                  'tree on Windows, system_profiler on macOS). Do not treat this as an '
                  'idle adapter.')
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
