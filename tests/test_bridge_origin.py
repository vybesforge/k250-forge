#!/usr/bin/env python3
"""BRIDGE ORIGIN: the loopback bridge refuses control writes that did not come
from a local page.

Threat: the launcher binds 127.0.0.1, but the wearer's own browser is on
127.0.0.1 too. A website he has open can POST to http://127.0.0.1:6969/run
with Content-Type: text/plain - a CORS "simple request" that browsers send
WITHOUT a preflight - and the box fires with manual:true (no ceiling) while
the page never learns whether it worked. DNS rebinding (evil.example ->
127.0.0.1) gets the same plus every GET. No radio needed: _run_pattern and
_stop are stubbed and must never be reached by a refused request.
"""
import http.client, json, os, sys, threading, unittest.mock as mock
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import k250_launcher as L  # noqa: E402

calls = []
def fake_run(pattern, level, secs, manual=False, address=None):
    calls.append(("run", pattern, level, secs, manual)); return True, "stub"
def fake_stop():
    calls.append(("stop",)); return True, "stub"

srv = ThreadingHTTPServer(("127.0.0.1", 0), L.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

def req(method, path, body=None, headers=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = dict(headers or {})
    if host: h["Host"] = host
    c.request(method, path, body=body, headers=h)
    r = c.getresponse(); data = r.read(); c.close()
    return r.status, dict(r.getheaders()), data

fails = []
def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

with mock.patch.object(L, "_run_pattern", fake_run), mock.patch.object(L, "_stop", fake_stop):
    body = json.dumps({"pattern": "tide", "level": 5, "secs": 10, "manual": True})
    JSON = {"Content-Type": "application/json"}

    # 1. the page's own request shapes still work
    calls.clear()
    st, _, _ = req("POST", "/run", body, JSON)                      # same-origin (no Origin header)
    check("same-origin JSON POST reaches the run", st == 200 and calls == [("run", "tide", 5, 10, True)])
    calls.clear()
    st, hd, _ = req("POST", "/run", body, {**JSON, "Origin": "null"})   # file:// page
    check("file:// page (Origin: null) is allowed", st == 200 and len(calls) == 1
          and hd.get("Access-Control-Allow-Origin") == "null")
    calls.clear()
    st, _, _ = req("POST", "/run", body, {**JSON, "Origin": f"http://127.0.0.1:{port}"})
    check("loopback-served page is allowed", st == 200 and len(calls) == 1)
    calls.clear()
    st, _, _ = req("POST", "/run", body, {**JSON, "Origin": "http://localhost:6969"})
    check("localhost page is allowed", st == 200 and len(calls) == 1)

    # 2. the cross-site simple request is refused and never reaches the engine
    calls.clear()
    st, hd, _ = req("POST", "/run", body, {"Content-Type": "text/plain", "Origin": "https://evil.example"})
    check("cross-site text/plain POST -> 403", st == 403)
    check("... and the engine was NOT called", calls == [])
    check("... and no CORS header is echoed", "Access-Control-Allow-Origin" not in hd)
    calls.clear()
    st, _, _ = req("POST", "/run", body, {**JSON, "Origin": "https://evil.example"})
    check("cross-site JSON POST -> 403, engine not called", st == 403 and calls == [])
    calls.clear()
    st, _, _ = req("POST", "/stop", "", {"Origin": "https://evil.example"})
    check("cross-site /stop is refused too (same gate on every POST)", st == 403 and calls == [])

    # 3. a POST with no JSON content type is refused even same-origin
    calls.clear()
    st, _, _ = req("POST", "/run", body, {"Content-Type": "text/plain"})
    check("same-origin text/plain POST -> 403 (forces a preflight path)", st == 403 and calls == [])
    st, _, _ = req("POST", "/run", body, {})
    check("POST with no Content-Type -> 403", st == 403 and calls == [])
    calls.clear()
    st, _, _ = req("POST", "/run", body, {"Content-Type": "application/json; charset=utf-8"})
    check("charset parameter on the JSON type is fine", st == 200 and len(calls) == 1)

    # 4. DNS rebinding: a foreign Host is refused on reads and writes
    st, _, _ = req("GET", "/status", host="evil.example")
    check("GET /status with Host: evil.example -> 403", st == 403)
    calls.clear()
    st, _, _ = req("POST", "/run", body, JSON, host="evil.example")
    check("POST with Host: evil.example -> 403, engine not called", st == 403 and calls == [])
    st, _, _ = req("GET", "/limits", host=f"127.0.0.1:{port}")
    check("Host: 127.0.0.1:port is fine", st == 200)
    st, _, _ = req("GET", "/limits", host="localhost:6969")
    check("Host: localhost:port is fine", st == 200)
    st, _, _ = req("GET", "/limits", host="[::1]:6969")
    check("Host: [::1]:port is fine", st == 200)

    # 5. a bad level is a 400, not a traceback
    calls.clear()
    st, _, _ = req("POST", "/run", json.dumps({"pattern": "tide", "level": "lots", "secs": 10}), JSON)
    check("non-numeric level -> 400, engine not called", st == 400 and calls == [])

srv.shutdown()
print()
if fails:
    print(f"BRIDGE ORIGIN: FAIL - {len(fails)} check(s): {fails}"); sys.exit(1)
print("BRIDGE ORIGIN: PASS - all checks")
