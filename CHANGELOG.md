# Changelog

What changed, and when. Versions are git tags — `v1.0`, `v2.0`, `v3.0` — so any older
version stays readable and runnable forever with `git checkout v1.0`. Nothing is
deleted or hidden: `main` is the current release, and the tags are the archive.

---

## v3.0 — 2026-09-15

Session budget, the limits-page rebuild, and several safety corrections.

### Added
- **Enforced session budget.** `session.max_duration_s`, default **1800 s (30 min)**, with a
  `max_duration_s_ceiling` of 14400 s (4 h). `k250_session.py` keeps a ledger of *running* time;
  `k250-scene` refuses to start once the budget is spent, and tells you your options. A fresh
  session begins after 15 idle minutes, or deliberately with `--reset-session`.
  `K250_IGNORE_SESSION=1` is the documented, deliberate override.
- `tests/test_pattern_change.py` — regression test for the pattern-change rule.
- Hover hints on the Power / Frequency / Slew headers in `limits-form.html`.

### Changed
- **`limits-form.html`: the four channel rows *are* the page.** The separate Power, Frequency and
  Slew cards are gone, so all twelve sliders are visible without scrolling. Values sit to the left
  of their sliders.
- **The hard stops live in one place**, in Session & safety, behind a **single** acknowledgement.
  The four safety booleans and the speak flag collapse to `safety.hard_stops_acknowledged` plus the
  `safety.hard_stops` list it refers to — four ticks for one decision was duplication, not safety.
- Per-channel limits are always written; the top-level values are derived as the **most
  conservative channel**, so they only ever matter as a safe fallback.

### Fixed
- **The "feels nothing" rule was backwards, and dangerously so.** It said to hold the ceiling and go
  *longer, not harder*. In practice no sensation usually means a bad connection, and more power into
  a loose or half-attached pad concentrates the current into a smaller area — that is what burns
  people. The rule now reads: **power down first, then check the loop.** Corrected on the page, in
  the generated JSON, in the README, and in both operator skills.
- **Pattern changes are handled in code, not assumed away.** `set_pattern()` now drops the frequency
  cache — otherwise `ma()` skips the re-send and leaves the channel silently at its zeroed frequency
  — and seeds the slew state with zero, because clearing it let the next write pass through
  unclamped and jump straight past the slew limit.
- **Retracted: "a power write can go missing."** It never happened. The only thing that zeroes a
  channel's power and frequency is a **pattern change**. The driver still writes power every tick,
  but as belt-and-braces, not because the box forgets.

---

## v2.0 — 2026-09-15

Per-channel limits, and the agreed names for the three controls.

### Added
- **Per-channel limits** — power, frequency and slew per channel, enforced in code. Different
  channels sit on different skin, so one global ceiling was the wrong shape.
- **The naming: POWER / FREQUENCY / SLEW.** The box labels the second control *Multi Adjust* and the
  companion app calls it `speed` internally, which is misleading — it changes *character*, not how
  fast anything moves. The rate of change is **slew**.
- `limits-form.html` (a page to build `limits.json`) and `install.sh` (venv + PATH tools).
- **Device discovery docs**: advertised name `Kx250-4S`, service UUID, and why the MAC must never be
  hardcoded (it's a random static LE address that changes on power-cycle).
- **Multi-channel driving, verified on two channels.**

### Fixed
- **A units bug in the limits file** that would have silently frozen the frequency axis: the old
  file stored slew in display units (`25`) where the engine expected raw (`2500`).
- **Per-channel slew was silently unlimited** — the lookup still used the old key name and fell
  through to "no limit".

---

## v1.0 — 2026-09-15

First release: the protocol, the engine, and the safety contract.

- **Reverse-engineered the BLE protocol.** JSON frames on service `086e0000-…`; keys `PW`, `MA`,
  `PA`, `AC`, `MP`, `CA`, `BC`, `FV`; and the finding that `PW`/`MA` run on a **0..10000** scale, not
  0..100 — the bug that makes a fresh implementation look like it does nothing.
- **Three findings that make it work at all:** the BLE link must be **held open** (the box zeroes
  output when it drops); `MA` is a genuine **second axis**, not a garnish; and the box **holds**
  `PW` rather than needing a keepalive.
- `k250_play.py` — pattern engine with 35 patterns, hardcap and slew clamping.
- `k250_show.py` — setlists that chain patterns in one BLE session; `k250_stop.py` — panic stop;
  `k250_ctl.py` — FIFO-driven persistent link.
- `limits.json` — the safety contract, enforced in code rather than described in prose, plus the
  README guidance: **at your own risk, start low and build, and percentages do not transfer
  between pad placements.**
