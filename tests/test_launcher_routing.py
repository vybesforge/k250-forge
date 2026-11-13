#!/usr/bin/env python3
"""test_launcher_routing.py — AI runs route through a held controller, Manual keeps
the spawn path (override marker gate), dead controller falls back to spawn, and
`stop` routes through the controller when the run did.

The point this guards: routing /run through the held-link controller is what keeps
the BLE connection alive (rest at zero, no disconnect between runs). But the
Manual-override safety gate (K250_WEARER_OVERRIDE) is only enforced on the SPAWN
path — a Manual run must NEVER be handed to the controller, or its override marker
is silently dropped. And when no controller is alive, spawn must still work.

Run: venv/bin/python tests/test_launcher_routing.py
"""
import importlib.util
import json
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Load the launcher module without importing bleak (read-only paths must not pay
# for it, and the import asserts nothing about the radio at module scope).
spec = importlib.util.spec_from_file_location("k250_launcher", os.path.join(ROOT, "k250_launcher.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

PASS = 0


def check(name, cond, detail=""):
    global PASS
    tag = "PASS" if cond else "FAIL"
    print(f"  {tag:4} {name}" + (f"   [{detail}]" if detail and not cond else ""))
    PASS += 1 if cond else 0
    if not cond:
        print("ROUTING TEST: FAILURE — aborting")
        sys.exit(1)


# --- 1. AI-mode run routes through an alive controller -------------------
calls = {"sent": []}


def _ctl_alive_true():
    return True


def _ctl_send_capture(line):
    calls["sent"].append(line)
    return True


L._ctl_alive = _ctl_alive_true
L._ctl_send = _ctl_send_capture
L._running = {"proc": None, "pattern": None, "started": 0.0, "via_ctl": False}
L._launch = lambda *a, **k: (True, "should not spawn")

ok, msg = L._run_pattern("tide", 20, 30, manual=False)   # AI mode, controller up
check("AI run sends 'run {...}' to the held controller", ok and calls["sent"]
      and calls["sent"][0].startswith("run ") and '"kind":"pattern"' in calls["sent"][0].replace(" ", ""),
      str(calls["sent"]))
check("AI run is marked via_ctl", L._running.get("via_ctl") is True)

# --- 2. Manual-override run NEVER routes through the controller ----------
calls["sent"] = []
L._running = {"proc": None, "pattern": None, "started": 0.0, "via_ctl": False}
ok, msg = L._run_pattern("tide", 20, 30, manual=True)    # Manual mode
# _launch returns (ok, err); _run_pattern turns an err into a message via _launch
# being stubbed above to (True, "should not spawn") — but that stub returns True
# meaning the run "started". The assertion we care about: nothing was sent over
# the FIFO.
check("Manual run sends NOTHING to the controller", not calls["sent"], str(calls["sent"]))

# --- 3. dead controller falls back to spawn -------------------------------
calls["sent"] = []
L._ctl_alive = lambda: False
L._running = {"proc": None, "pattern": None, "started": 0.0, "via_ctl": False}
spawned = []
L._launch = lambda cmd, label, manual, zero=True: spawned.append((label, manual)) or (True, "spawned")
ok, msg = L._run_pattern("tide", 20, 30, manual=False)
check("no controller -> spawn path, nothing on FIFO", ok and not calls["sent"] and spawned, str(spawned))
check("spawned run is NOT via_ctl", L._running.get("via_ctl") in (False, None))

# --- 4. /stop with a LIVE controller ALWAYS routes via ctl, even when idle ----
# This is the regression that bit us live: after a ctl-routed run ends, via_ctl is
# already False; a plain /stop between patterns must STILL go over the held link.
# The old gate (`via_ctl and _ctl_alive`) fell back to the spawn path, which cannot
# reach a box the controller holds -- it retried and cried "IT MAY STILL BE ENERGISED".
calls["sent"] = []
spawned = []
L._ctl_alive = _ctl_alive_true
L._ctl_send = _ctl_send_capture
L._run_stop_tool = lambda *a, **k: (_ for _ in ()).throw(AssertionError("spawn stop tool called on live controller"))
L._running = {"proc": None, "pattern": None, "started": 0.0, "via_ctl": False}  # idle
ok, msg = L._stop()
check("idle /stop with live controller routes via ctl (sends 'stop')",
      ok and calls["sent"] == ["stop"], f"ok={ok} sent={calls['sent']} msg={msg}")
check("idle /stop did NOT spawn the stop tool", not spawned, str(spawned))

# --- 5. /stop with NO controller falls back to the stop tool ------------------
calls["sent"] = []
ran_stop = []
L._ctl_alive = lambda: False
L._ctl_send = _ctl_send_capture
L._run_stop_tool = lambda timeout=90: (ran_stop.append(1) or (0, "stopped", True))
L._running = {"proc": None, "pattern": None, "started": 0.0, "via_ctl": False}
ok, msg = L._stop()
check("dead controller /stop falls back to the stop tool", ok and ran_stop == [1], f"ok={ok} ran={ran_stop} msg={msg}")
check("dead controller /stop sent nothing on the FIFO", not calls["sent"], str(calls["sent"]))

print(f"\nROUTING TEST: PASS — {PASS} checks")
