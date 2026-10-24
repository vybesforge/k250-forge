# Security and safety

This software drives electrical current into a person. Treat every bug report
that could raise, hold or hide power as a security issue.

## Reporting

Open a GitHub issue **without** the reproduction details, or email the address on
the vybesforge.com contact page, and say it is a safety report. You will get a
reply before anything is published. Please do not post a working exploit for an
over-power path in public before a fix has shipped.

## What counts

- Anything that lets power exceed `limits.json` without the wearer's own Manual
  override (the three locks are described in `agent-skill/SKILL.md`).
- Anything that lets a stop fail silently, or re-energise the box after a stop.
- Anything that lets something other than the wearer's own local page drive the
  bridge on `127.0.0.1:6969` (cross-site requests, DNS rebinding, a leaked token).
- A firmware-units mistake (v1 percent x100 vs v2 plain percent) on any path.

## What the bridge already assumes

The launcher binds loopback only, refuses POSTs that are not `application/json`
from a local (`file://`, `127.0.0.1`, `localhost`) page, and refuses any request
whose `Host` header is not its own name. The wearer's physical kill switch (hold
any knob ~2 s) does not depend on any of this and always wins.
