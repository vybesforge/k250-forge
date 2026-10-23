"""Reconnect tests for k250_ctl.

The held-link controller's job is continuity: after a drop it re-probes for the box
essentially forever (backoff grows toward a cap) and only exits on `quit`. It must
never attempt to run with no box (a real mid-run drop left the old `find` handing
hold_link a None object which BleakClient(None) crashed on -- live regression,
2026-10-22), and it must never give up a few seconds after a drop the way a short
bounded budget did (that strung the operator out mid-session).

Since 2026-10-22 the re-probe uses probe_box_via_subprocess() -- a FRESH interpreter
per probe -- because the long-lived controller's own in-process find() was seen to
miss an advertising box after a hard drop while a fresh process finds it every time.

Run: venv/bin/python tests/test_ctl_reconnect.py
"""
import asyncio, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import k250_ctl as C


def _setup(reason_fn, box_present=lambda n: True):
    """Stub probe_box_via_subprocess + hold_link; point at a scratch runtime dir.
    `calls.present` counts probe calls that saw the box; `box_present(n)` decides the
    nth probe's answer, so a test can have the box vanish or return after a drop."""
    calls = {"hold": 0, "probe": 0, "present": 0}

    async def fake_probe():
        calls["probe"] += 1
        if box_present(calls["probe"]):
            calls["present"] += 1
            return True, -50, "EB:29:F1:14:42:6B"   # strong signal, real addr
        return False, None, None

    async def fake_hold_link(addr, queue, quit_now):
        calls["hold"] += 1
        if quit_now.is_set():
            return "quit"
        return reason_fn(calls["hold"])

    td = tempfile.TemporaryDirectory()
    C.RUNTIME, C.FIFO = td.name, f"{td.name}/ctl.fifo"
    C.PIDFILE, C.STATEFILE = f"{td.name}/ctl.pid", f"{td.name}/ctl.state"
    C.RECONNECT_BACKOFF, C.RECONNECT_BACKOFF_CAP = 0.0, 0.0  # keep the test fast
    orig = C.probe_box_via_subprocess, C.hold_link
    C.probe_box_via_subprocess, C.hold_link = fake_probe, fake_hold_link
    return C, td, orig, calls


def _run_until_end(reason_fn, box_present=lambda n: True):
    ctx, td, orig, calls = _setup(reason_fn, box_present)
    try:
        rc = asyncio.run(C.main())
    finally:
        C.probe_box_via_subprocess, C.hold_link = orig
        td.cleanup()
    return rc, calls


def _run_until_timeout(loops_to_allow, box_present=lambda n: False):
    ctx, td, orig, calls = _setup(lambda _n: "lost", box_present)

    async def runner():
        return await C.main()

    async def drive():
        task = asyncio.ensure_future(runner())
        for _ in range(loops_to_allow + 2):
            await asyncio.sleep(0.01)
        assert not task.done(), "controller exited early -- it should keep re-probing"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    try:
        asyncio.run(drive())
    finally:
        C.probe_box_via_subprocess, C.hold_link = orig
        td.cleanup()
    return calls


def test_reconnect_after_drop_retried_repeatedly():
    """A flaky box (drops then re-advertises) is connected repeatedly across a long
    session -- the per-session behaviour the bounded budget used to break."""
    rc, calls = _run_until_end(
        lambda n: "quit" if n >= 8 else "lost",
        lambda n: True)                       # box always present in-range
    assert calls["hold"] == 8, f"expected 1 + 7 reconnects, got {calls['hold']}"
    assert calls["present"] >= 8, f"probe must keep confirming the box, got {calls['present']}"
    assert rc == 0
    print("PASS  drop retried repeatedly across a long session (7 reconnects)")


def test_probe_waits_for_box_before_attempting_connection():
    """While the box is off the air the controller must keep probing and never call
    hold_link -- the None-box crash regression, now expressed as 'no probe result,
    no connection attempt'."""
    # connect once, drop, box goes off air for a few probes, then returns so we can
    # verify a SECOND connection happens only once the box is confirmed present.
    def box_present(n):
        return n == 1 or n >= 5          # startup, gone briefly, back at probe 5
    rc, calls = _run_until_end(
        lambda n: "quit" if n >= 2 else "lost", box_present)
    assert rc == 0
    assert calls["hold"] == 2, f"expected 2 connections (drop then resume), got {calls['hold']}"
    assert calls["probe"] >= 5, f"should probe while box is gone, got {calls['probe']} probes"
    assert calls["present"] >= 2, f"should confirm the box on both connects, got {calls['present']}"
    print(f"PASS  probe waits for box; second connection only once present ({calls['probe']} probes)")


def test_box_gone_keeps_probing_does_not_give_up():
    """Even when the box stays off the air, the controller keeps re-probing indefinitely
    instead of exiting -- it only stops on quit."""
    calls = _run_until_timeout(loops_to_allow=8, box_present=lambda n: False)
    assert calls["hold"] == 0, f"no connection may hold without a box, got {calls['hold']}"
    assert calls["probe"] >= 8, f"should keep re-probing, got {calls['probe']} probes"
    print(f"PASS  box-gone keeps re-probing ({calls['probe']} probes, no give-up, no crash)")


def test_clean_quit_exits_without_reconnect():
    rc, calls = _run_until_end(lambda _n: "quit", box_present=lambda n: True)
    assert calls["hold"] == 1, f"quit should end after the first held link, got {calls['hold']}"
    assert rc == 0
    print("PASS  clean quit exits with no reconnect")


def _flag_for_rssi(rssi):
    """Capture what _flag_signal logs for a given signal strength (present box)."""
    out = []
    orig = C.log
    C.log = lambda *a: out.append(" ".join(str(x) for x in a))
    try:
        C._flag_signal("connect", True, rssi)
    finally:
        C.log = orig
    return "\n".join(out)


def test_weak_signal_is_flagged_at_connect():
    """A connect-time RSSI below the floor (WEAK_SIGNAL_DBM) must produce a WEAK SIGNAL
    log line -- the early RF tell the box's own JSON cannot give."""
    captured = []
    orig_log = C.log
    C.log = lambda *a: captured.append(" ".join(str(x) for x in a))
    try:
        async def weak_probe():
            return True, -90, "EB:29:F1:14:42:6B"   # below WEAK_SIGNAL_DBM (-80) -> must flag
        # also assert the exact boundary flags (-80) via a direct call
        assert "WEAK SIGNAL" in _flag_for_rssi(-80), "exact -80 must flag as weak"
        ctx, td, orig, calls = _setup(lambda _n: "quit", box_present=lambda n: True)
        C.probe_box_via_subprocess = weak_probe
        try:
            asyncio.run(C.main())
        finally:
            C.probe_box_via_subprocess, C.hold_link = orig
            td.cleanup()
    finally:
        C.log = orig_log
    hits = [l for l in captured if "WEAK SIGNAL" in l]
    assert hits, f"expected a WEAK SIGNAL log line, got: {captured[:3]}"
    assert "-90" in hits[0]
    print(f"PASS  weak connect flagged: {hits[0]}")


def test_strong_signal_is_not_flagged():
    """A connect-time RSSI at or above the floor must NOT alarm -- no WEAK SIGNAL line."""
    captured = []
    orig_log = C.log
    C.log = lambda *a: captured.append(" ".join(str(x) for x in a))
    try:
        async def strong_probe():
            return True, -45, "EB:29:F1:14:42:6B"
        ctx, td, orig, calls = _setup(lambda _n: "quit", box_present=lambda n: True)
        C.probe_box_via_subprocess = strong_probe
        try:
            asyncio.run(C.main())
        finally:
            C.probe_box_via_subprocess, C.hold_link = orig
            td.cleanup()
    finally:
        C.log = orig_log
    hits = [l for l in captured if "WEAK SIGNAL" in l]
    assert not hits, f"strong signal should not flag, got: {hits}"
    print("PASS  strong connect is not flagged")


if __name__ == "__main__":
    test_reconnect_after_drop_retried_repeatedly()
    test_probe_waits_for_box_before_attempting_connection()
    test_box_gone_keeps_probing_does_not_give_up()
    test_clean_quit_exits_without_reconnect()
    test_weak_signal_is_flagged_at_connect()
    test_strong_signal_is_not_flagged()
    print("CTL RECONNECT: PASS")