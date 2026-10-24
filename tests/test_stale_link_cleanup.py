"""The launcher clears a stale BlueZ link to the box after every run.

A run that ends abnormally (killed, crashed, or a stop that could not reach the
box) can leave BlueZ holding a phantom connection. The box stops advertising
while connected, so that stale link keeps it silent until cleared. This test
guards the automatic cleanup: it must find a connected Kx250 by name (the box's
address is a random static LE address that changes on power-cycle) and
disconnect it, and must do nothing when no Kx250 is connected.

Run: venv/bin/python tests/test_stale_link_cleanup.py
"""
import os
import sys
import unittest.mock as mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import k250_launcher as L


def fake_run(calls, output):
    """Return a subprocess.run mock that records calls and returns `output`."""
    m = mock.Mock()
    m.run.side_effect = lambda *a, **k: mock.Mock(stdout=output)
    return m


def main():
    checks = []

    # 1. A connected Kx250 is found by name and disconnected.
    calls = []
    def run_side(*a, **k):
        calls.append(a[0])
        if a[0][0] == "bluetoothctl" and a[0][1] == "devices":
            return mock.Mock(stdout="Device EB:29:F1:14:42:6B Kx250-4S\n")
        return mock.Mock(stdout="")
    with mock.patch.object(L.subprocess, "run", side_effect=run_side):
        L._clear_stale_link()
    disconnects = [c for c in calls if c[0] == "bluetoothctl" and c[1] == "disconnect"]
    checks.append(("a connected Kx250 is disconnected by address",
                   disconnects == [["bluetoothctl", "disconnect", "EB:29:F1:14:42:6B"]],
                   disconnects))

    # 2. No Kx250 connected -> no disconnect is attempted.
    calls2 = []
    def run_side2(*a, **k):
        calls2.append(a[0])
        return mock.Mock(stdout="")
    with mock.patch.object(L.subprocess, "run", side_effect=run_side2):
        L._clear_stale_link()
    disconnects2 = [c for c in calls2 if c[0] == "bluetoothctl" and c[1] == "disconnect"]
    checks.append(("no Kx250 connected -> no disconnect attempted",
                   disconnects2 == [], disconnects2))

    # 3. A non-Kx250 connected device is left alone.
    calls3 = []
    def run_side3(*a, **k):
        calls3.append(a[0])
        if a[0][0] == "bluetoothctl" and a[0][1] == "devices":
            return mock.Mock(stdout="Device AA:BB:CC:11:22:33 SomeOtherDevice\n")
        return mock.Mock(stdout="")
    with mock.patch.object(L.subprocess, "run", side_effect=run_side3):
        L._clear_stale_link()
    disconnects3 = [c for c in calls3 if c[0] == "bluetoothctl" and c[1] == "disconnect"]
    checks.append(("a non-Kx250 device is left alone",
                   disconnects3 == [], disconnects3))

    width = max(len(n) for n, _, _ in checks)
    bad = 0
    for name, ok, got in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}  got {got!r}")
        bad += not ok
    print(f"\nSTALE LINK CLEANUP: {'PASS' if not bad else f'{bad} FAILED'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
