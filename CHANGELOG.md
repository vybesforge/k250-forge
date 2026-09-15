# Changelog

What changed, and when. Versions are git tags — `v1.0`, `v2.0`, `v3.0` — so any older
version stays readable and runnable forever with `git checkout v1.0`. Nothing is
deleted or hidden: `main` is the current release, and the tags are the archive.

---

## v3.2.1 — 2026-09-15

### Fixed — found by an agent doing a clean checkout on macOS

**`tests/test_pattern_change.py` could not run for anyone but the author.** Line 3 was
`sys.path.insert(0, '/home/<user>/k250')` — a literal path into the author's home directory. On that
box it worked; on a fresh clone on macOS, Windows or another Linux box it raised
`ModuleNotFoundError: k250_play`. The README tells a stranger to run it with `venv/bin/python`, and
it failed for every one of them, every time.

The sting is *which* test it was: the only regression guard for the pattern-change rule — the rule
this changelog calls out as the one that used to silently zero channels — was the one test that
never actually ran outside the author's machine. The two suites written after it use the portable
idiom (`os.path.dirname(os.path.dirname(os.path.abspath(__file__)))`), so this was a stale file left
behind by a newer pattern, not a design choice. Now fixed to match.

**Repo and clone names disagreed on case.** The GitHub repo is `K250-forge`; the README said
`git clone …/k250-forge && cd k250-forge`. On macOS's case-insensitive filesystem both spellings
work — so the author's symlinks pointed at `~/k250-forge` while a clone made from the canonical URL
was `~/K250-forge`. It would break on any case-sensitive volume, and it read wrong to anyone
browsing. The README now uses the canonical `K250-forge.git` on all three platforms, and the wrapper
fallback paths match.

**`FINDINGS.md` §6 was titled with an absolute path** into the author's box, and two later lines
instructed `cd ~/k250`. Genericised, with a note that the recon assets are not in the repo and the
paths in that section are relative to the author's working directory.

### Added
- `tests/test_portability.py` — 6 checks that this class of bug cannot come back: no `/home/<user>`
  or `/Users/<user>` path in any shipped `.py`/`.sh`/wrapper, every test self-locates via `__file__`,
  the wrappers resolve their own location, and the package imports from wherever it was cloned.
  It caught the first version of itself (its own docstring quoted the bad line) — which is the point.

### Verified
- All four suites pass from a clean copy of the tree with no venv of its own, run with a python from
  a different clone entirely — i.e. the actual condition the feedback described.

---

## v3.2 — 2026-09-15

### Fixed — a real hole in the safety contract

**The limits file was only enforced by the bash wrapper.** `k250_play.py` never read `limits.json`
at all, and its `--hardcap` defaulted to `100` — no ceiling. Mac and Linux go through
`bin/k250-scene`, so they were capped; **Windows has no bash**, and the documented entry point there
is `python k250_play.py ...`, which was therefore uncapped. The README's claim that the safety lives
in the tool rather than the prompt was, for Windows, false.

The engine now reads the limits file itself (`limits.local.json`, then `limits.json`, next to the
script, else `$K250_LIMITS` or `--limits PATH`) and applies the same rule the wrapper does: **a
command-line ceiling can only lower the file's, never raise it.** The session budget is enforced on
the direct path too, reserving the time up front; the wrapper sets `K250_WRAPPED=1` so the ledger is
not charged twice (verified: a run whose engine wall time was ~22 s moved the ledger 24 s).

Verified live on the real box: a direct call asking for `--hardcap 90` clamped to the file's 50 %,
and the box finished at zero.

### Added
- `tests/test_limits_enforcement.py` — 12 checks: the file wins over the command line, `--slew 0`
  cannot lift a file cap, a file value of 0 still means "unlimited", `null` per-channel fields are
  dropped, no-file falls back to the command line, and the session budget refuses a run that *would*
  exceed it (which it initially did not — it checked the budget before reserving, so the last run
  could overshoot; caught by this test).

---

## v3.1 — 2026-09-15

### Changed
- **Power is no longer re-sent on every tick.** The box persists `PW`, and only a **pattern change**
  zeroes it — so writing it ~5×/s was redundant traffic, and those frames are part of what makes the
  box's own LCD churn while a pattern runs. A write now goes out only when the value changes, plus a
  keepalive every 2 s so a silent reset would still self-heal. `K250_PW_REFRESH=0` restores the old
  write-every-tick behaviour. `set_pattern()` drops the cache, so the one case that genuinely zeroes
  power still forces a re-send.

### Added
- `tests/test_write_policy.py` — flat power is written once, the keepalive fires, an unchanged value
  is skipped, and a pattern change forces a re-send.

### Measured on the real box (`speed_sweep --base 10 --secs 20`, one channel live)

| | before | after |
|---|---|---|
| `AC` writes — the ones that move the box's selected tab | 1 | 0–1 |
| power writes | 62 | **9** |
| total BLE frames | 140 | 149 |

**The honest read:** the traffic *moved*, it did not shrink — with no write on every tick the loop
runs about twice as fast, so `MA` writes roughly doubled. What makes the screen jump is the `AC`
writes, and those are 0–1 per run because `select()` only writes when the channel actually changes.
The power-write change is still right (fewer redundant frames, and the semantics now match the
hardware), but it is not the thing that calms the display.

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
