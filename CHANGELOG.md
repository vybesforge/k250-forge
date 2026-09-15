# Changelog

What changed, and when. Versions are git tags — `v1.0`, `v2.0`, `v3.0` — so any older
version stays readable and runnable forever with `git checkout v1.0`. Nothing is
deleted or hidden: `main` is the current release, and the tags are the archive.

---

## v3.2.5 — 2026-09-15

### Fixed — the primary tool could not run at all on macOS

`k250-scene <pattern>` — the tool you actually drive the box with — died on macOS's stock bash
(`/bin/bash`, **3.2.57**):

```
k250-scene: line 225: EXTRA[@]: unbound variable     exit 1
```

Under `set -u`, **bash before 4.4 treats an expansion of an empty array as an unbound variable**.
The final line of the wrapper expanded `"${FWD[@]}"` and `"${EXTRA[@]}"`; `EXTRA` is empty whenever
per-channel caps are unset, which is the shipped default. So the failure was not conditional — it
was every run, on every macOS machine, with the box never touched. Bash 4.4 changed the rule, and
this was written on bash 5, which is why it looked fine here.

Both expansions now use the standard guard:

```bash
"$PY" "$PLAY" ${FWD[@]+"${FWD[@]}"} --hardcap … ${EXTRA[@]+"${EXTRA[@]}"}
```

`--list` and `--limits-show` exit before that line, which is precisely why this survived three
rounds of "does the clone work" auditing: every check passed, and only *driving the box* failed.

Credit: found by the same clean-checkout audit, on macOS, by driving the hardware rather than the
CLI surface.

### Added
- `tests/test_shell_compat.py` — 27 checks. Static, everywhere: no unguarded `"${A[@]}"`/`"${A[*]}"`
  in a script with `set -u`, no bash-4-only syntax (`${v,,}`, `declare -A`, `mapfile`, `&>>`, `|&`,
  `;;&`, `[[ -v ]]`, `${v@Q}`), and every shipped shell file parses. Behavioural, when pointed at an
  older bash (`K250_TEST_OLD_BASH=/path/to/bash`): it drives the wrapper end to end against a stub
  python with per-channel caps both set and unset, and with the argument list empty, and asserts the
  engine is actually reached. The docstring records how to fetch a pre-4.4 bash without touching
  your system one.

### Verified
- Reproduced on a real **bash 3.2.39** (Ubuntu archive package, run under its own libncurses), first
  in isolation — `A=(); f "${A[@]}"` → `A[@]: unbound variable`, exit 1 — then on the actual
  wrapper: `line 225: EXTRA[@]: unbound variable`, exit 1, engine never invoked.
- Confirmed the reported "safe forms" on the same binary: `${!A[@]}` and `${#A[@]}` survive an empty
  array.
- After the fix, on bash 3.2: a normal run reaches the engine with the full argument list
  (`tide --base 5 --secs 5 --hardcap 50 --slew 25 --frequency 2500`), per-channel caps arrive as
  `--channel-caps`, an empty argument list no longer aborts, and exit is 0. Same behaviour on bash 5.
- All six suites pass, including the new one with the old bash attached.

---

## v3.2.4 — 2026-09-15

### Fixed — a fresh install printed a traceback while it was working

`install.sh` reported the bleak version with `bleak.__version__`. **bleak stopped exposing that
attribute** (it is metadata now), so on every current bleak the install printed

```
Traceback (most recent call last):
  File "<string>", line 1, in <module>
AttributeError: module 'bleak' has no attribute '__version__'
      bleak installed ()
```

in the middle of an otherwise successful install — the first output a stranger sees from this repo,
and it reads exactly like a failed install. Cosmetic, but only in the sense that the venv still
worked; the message was a lie.

Now read from the package metadata, with a fallback so a future packaging change degrades to a plain
`bleak` instead of a traceback:

```bash
... -c 'import importlib.metadata as m; print("bleak", m.version("bleak"))' 2>/dev/null || echo "bleak"
```

Credit: found by the same clean-checkout audit as v3.2.1–v3.2.3, and it is the one finding that had
nothing to do with portability — it was a local, uncommitted edit in the reviewer's tree that no
"does the clone work" check would ever have surfaced.

### Added
- `tests/test_portability.py` → 12 checks: no shipped file may introspect a dependency's version
  attribute. The check is written in two pieces so it does not trip itself.

### Verified
- Reproduced first: a fresh `install.sh` run in a clean copy with a scratch `$HOME` printed the
  traceback above, exit 0 — install fine, message wrong.
- After the fix, same run: `bleak installed (bleak 3.0.2)`, no traceback.
- The from-scratch install was then driven end to end: the symlinked `k250-scene --list` works from
  an unrelated cwd, and `--limits-show` correctly resolved to the fresh clone's conservative
  `limits.local.json` (10 % ceiling) rather than a developer's working limits.

---

## v3.2.3 — 2026-09-15

### Fixed — `k250-scene --list` only worked from inside the clone

Reported from a clean checkout: `--limits-show` worked, `--list` didn't —
`ModuleNotFoundError: No module named 'k250_play'`. The wrapper ran the listing from a
`python -` heredoc, and **`python -` puts the caller's cwd on `sys.path[0]`, not the wrapper's
directory**. So `--list` worked if you happened to be standing in the working directory and died
everywhere else. It now passes its own directory to the interpreter and inserts it into `sys.path`
explicitly.

Two more defects on the same path, found while fixing it:

- **The interpreter check ran too late.** `[ -x "$PY" ]` sat *after* argument handling, i.e. after
  the `--list` and `--limits-show` branches had already invoked python. With no venv, those now
  gave bash's raw `line 45: .../venv/bin/python: No such file or directory` — for `--list`, yes,
  but also potentially mid-scene. The check is now done once, immediately after the working
  directory is resolved, before anything calls python.
- **A failed path resolution was silent.** `HERE` fell back to `$HOME/K250-forge` when it could not
  resolve its own location, then reported that path as though it were fact. That is how a wrapper
  ends up naming a directory that is not your clone — the same class of bug as the hardcoded path
  in v3.2.1, one layer down. Now every wrapper checks that the modules are actually where it says
  they are, and **exits 1 with the path it looked in** instead of guessing. `k250-stop` says so in
  the terms that matter: it could not run, so treat the box as still energised and switch it off by
  hand.

### Added
- `tests/test_wrapper_cli.py` — 10 checks. Copies the tree to a temp directory, then runs
  `--list` and `--limits-show` from a *different* directory and asserts both work and that
  `--limits-show` names the limits file it actually read; asserts a directory with no modules exits
  1 and says where it looked; asserts nothing is written into the repo. Read-only — it never runs
  the engine.
- `session.json` is now gitignored. Running the tools in a clone created untracked session state.

### Verified
- Live wrapper from `/tmp`: `k250-scene --list` → 35 patterns, exit 0 (was `ModuleNotFoundError`).
- `K250_DIR=/tmp/nope k250-scene --list` → exits 1 and names `/tmp/nope`.
- Repo wrapper with no venv, from `/tmp` → "run ./install.sh first", exit 1 (was a raw bash error).

---


## v3.2.2 — 2026-09-15

### Fixed — a claim in this changelog that could not be checked

v3.0 said the "feels nothing" correction had landed "in the README, and in both operator skills."
An agent auditing a clean checkout went looking for those skills and found none — correctly, because
they are **Hermes-profile skills on the author's operator box** (`kink-k250-ble` in the default
profile, `pepper-k250` in another), not files in this repo. The correction genuinely is in both of
them, verified; the changelog simply cited something a reader can never see. That is the same class
of defect as a hardcoded path: true where it was written, unverifiable everywhere else.

Two changes, so the claim is now checkable:

- **`agent-skill/SKILL.md` ships in the repo** — a portable operator skill, meant to be copied into
  an agent's skills directory. It carries the two hard rules verbatim (stop word ends everything
  instantly; no sensation means power **down** and check the loop, never more power), the limits
  contract, the tool table including the Windows path, and the protocol traps that look like bugs.
  The README's "For an AI agent" section was material *for* a skill; this is the skill.
- **The v3.0 line is corrected in place** with the note above, rather than quietly reworded. Reading
  the changelog top to bottom should tell you what was believed and when it changed.

### Verified
- The correction really is present in both operator skills (quoted from the files, not remembered).
- `agent-skill/SKILL.md` parses as a valid skill: name, description, version in the frontmatter, and
  both hard rules present verbatim.

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
  the generated JSON, in the README, and in both operator skills *(clarification added in v3.2.2:
  "both operator skills" meant the two Hermes-profile skills on the author's own operator box —
  `kink-k250-ble` and `pepper-k250` — which were **never** part of this repo, so from a clone that
  claim could not be checked at all. The portable, in-repo version of that skill is now
  [`agent-skill/SKILL.md`](agent-skill/SKILL.md).)*
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
