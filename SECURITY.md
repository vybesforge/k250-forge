# Security and safety

This software drives electrical current into a person. Treat every bug that could
raise, hold or hide power as a safety bug, not a nice-to-have.

## Support level: best effort, no contact

This is one person's open-source project. There is no security contact, no private
disclosure channel and no response promise. If you find something, **open a GitHub
issue** — public is fine — with what you saw and how to reproduce it. It will be read
when it is read. Do not expect a reply, a timeline or a CVE.

If you need the fix sooner than that, the licence lets you make it yourself.

## What counts

- Anything that lets power exceed `limits.json` without the wearer's own Manual
  override (the three locks are described in `agent-skill/SKILL.md`).
- Anything that lets a stop fail silently, or re-energise the box after a stop.
- Anything that lets something other than the wearer's own local page drive the
  bridge on `127.0.0.1:6969` (cross-site requests, DNS rebinding).
- A firmware-units mistake (v1 percent x100 vs v2 plain percent) on any path.

## What the bridge already assumes

The launcher binds loopback only, refuses POSTs that are not `application/json`
from a local (`file://`, `127.0.0.1`, `localhost`) page, and refuses any request
whose `Host` header is not its own name. The wearer's physical kill switch (hold
any knob ~2 s) does not depend on any of this and always wins.
