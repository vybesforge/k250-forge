# Changelog

What changed, and when. Versions are git tags — `v1.0`, `v2.0`, `v3.0` — so any older
version stays readable and runnable forever with `git checkout v1.0`. Nothing is
deleted or hidden: `main` is the current release, and the tags are the archive.

One exception, written up in v3.2.18 rather than passed over: the repository was
re-created to take personal detail out of the history that predates v3.2.16. That is the
only time anything has been removed from this project's record, and the fact that it
happened is part of the record.

---

## v3.3.1 — 2026-09-18

### Changed — one line of the findings doc named the author's workstation

`FINDINGS.md` described the device as attached to a specific machine model. It now says the bench
machine. Docs only: no engine, tool, test or limits-value change.

---

## v3.3.0 — 2026-09-18

### Added — the limits page can act, not just generate (`k250_launcher.py`, `k250-launcher`)

The page has always generated a `limits.json` for you to save by hand. It now talks to a small
loopback-only bridge, which both serves the page and lets it do the two things you actually want
from it: **Apply** writes the file, and **Run** plays a pattern through the same engine the CLI
uses. Open `http://127.0.0.1:6969/` — the bridge serves that page itself, so the page and the
bridge are same-origin and no CORS is involved; a `file://` page still works, and the bridge
answers it too.

Apply **merges**, it does not replace: the keys the page owns are written and everything else in
your file — `pattern_notes`, your notes, the disclaimer wording — is left alone, with a
timestamped backup taken first. That distinction is not theoretical: writing the page's whole
generated document over the file would have deleted them.

New `k250-launcher` on PATH (`bin/k250-launcher`), linked by the installer on Linux/macOS;
Windows uses `py -3 k250_launcher.py` from an activated venv, and the bridge finds the venv
either way (`venv/bin/python` or `venv\Scripts\python.exe`).

### Changed — one control sets the AI ceiling

The page computed the top-level ceiling as the **lowest of the four channel sliders**, so moving
one channel up while an unused channel sat low *lowered* the ceiling — the opposite of what it
looked like. The ceiling now has its own *AI power ceiling* slider, and the per-channel rows sit
under it and can only lower a channel. The page also loads its values from the file on open, has a
single duration row with presets, and refuses to Apply until it has actually read the file (a
failed read once let page defaults overwrite a live contract).

### Changed — the session budget is a TIMER, not a wall

It used to refuse a run once the budget was spent, and it used to roll the budget over after 15
quiet minutes. Both are gone: an idle gap no longer behaves differently from playing, there is no
cooldown to wait out and nothing to reset by hand — a spent session starts a new one *immediately*.
The page shows the ledger as a live timer (`session 12m04s of 1h00m · 47m56s left`), read through
the same module the engine uses, so the display cannot disagree with reality.

### Security — the AI limits are the wearer's, and no driver may exceed them

`--override-ceiling` exists for exactly one thing: the wearer's own Manual level, pushed by the
bridge from the page when they set a level above the ceiling. It is refused **everywhere else** —
without the bridge's marker, and always on the `k250-scene` wrapper path that every tool uses — so
no script or AI-driven run can raise the agreed ceiling. `power.default_percent` is no longer
written by Apply either: it is the wearer's tuned starting figure, not the page's.

### Added — `flat`, and 28 more patterns

`flat` holds one level with MA pinned at zero: the pattern that is the absence of a pattern, for
when the scene wants steadiness rather than drama. The engine now ships 64 patterns.

### Fixed — twelve seconds before every pattern

`find()` ran a blind `BleakScanner.discover(timeout=12)`, and a discovery scan does not return
early — it waits out the whole clock even with the box advertising on the desk. Every tool calls
`find()`, so every start began with twelve seconds of silence. A filter scan returns the moment the
advertisement matches: measured `found` at **0.6 s** and first output at **2.0 s** on the same
hardware, down from 12.1 s and 16.1 s. Fixed startup `sleep`s in the engine and in
`k250_show.py` were replaced with waits on the actual reply.

### Fixed — `k250_show.py` never set the box to Manual, and ignored the limits file

It drove the box while it sat in a patterned mode, so our `PW` writes went *into* the box's own
generator, and it took `--hardcap` at face value (default 60) without ever reading `limits.json` —
a setlist could exceed the agreed ceiling while the CLI and the page both obeyed it. It now calls
`prepare_channels()` first, verifies `PA` came back `Manual`, and clamps to the file.

### Tests

Four suites added (`test_session_timer.py`, `test_no_ai_override.py`, `test_launcher_limits.py`,
`test_flat_pattern.py`), and the existing ones updated where the behaviour they pinned was
deliberately changed: `test_limits_enforcement.py` now asserts a spent session rolls over instead
of refusing, `test_limits_form.py`'s DOM stub covers the new controls and asserts the page ships
with the acknowledgement unticked and never writes that field through Apply.

---

## v3.2.30 — 2026-09-17

### Changed — the folder rule is stated once, for every OS

Cutting the long version of that warning left it living only in the Windows section, where it was told
as a Windows story. Linux and macOS lost it entirely, so a clone into `/` or `/usr` failed with no
warning at all. It is back as one line in the install preamble, covering all three platforms, and it
stays in the Windows steps where it is natively part of the instruction.

### Changed — the opening line and the repository description now say the same thing

The description said "e-stim box" where the README said "e-stim **power** box" — the device's own name
is the Kink K250-4S E-Stim Power Box, so "power box" is the one that is right, and the description was
updated to match. The README's first sentence now also carries the description's "write your own
patterns", which the next paragraph was already explaining.

Docs only: no engine, tool, test or limits-value change.

## v3.2.29 — 2026-09-17

### Changed — the install section leads with the zero-effort path

Before the per-OS steps the README now names the two ways in: hand the repo to an AI harness (this
project is written to be installed that way — it ships a portable operator skill, and the steps are
worded to be followed literally), or install by hand one OS at a time below. Same result either way.

No engine, tool, test or limits-value change.

## v3.2.28 — 2026-09-17

### Changed — install.sh offers to put `~/.local/bin` on your PATH, instead of only telling you to

It already linked the three tools into `~/.local/bin` and created the directory when it was missing,
but it would not persist the PATH line — a fresh user got `command not found: k250-scene` in the next
terminal on the OSes (Linux and macOS) where `~/.local/bin` is often not on PATH at all. The Windows
installer (install.cmd) had just removed the same first-run wall on that platform; this closes it on the
other two.

The write is never silent: it happens only on an explicit yes. Interactively the installer asks
"Add ... to your PATH ... [Y/n]" (default yes); with stdin not a terminal it reads `K250_ADD_PATH` and,
with neither, writes nothing and just prints the reminder — an unattended run cannot edit a shell rc by
accident. The added line is one commented export, marked with the installer's name so it can be removed
in one go, and a second run sees the directory already persisted and does not duplicate it.

`tests/test_install.py` pins the behaviour without a network build (the sandbox ships a fake
`venv/bin/python` and `pip` so install.sh reaches its PATH step quickly): `K250_ADD_PATH=1` writes the
line exactly once, a no-TTY, no-flag run writes nothing, `K250_ADD_PATH=no` writes nothing, and a second
run does not duplicate. Nine suites now.

No engine, tool or limits-value change.

## v3.2.27 — 2026-09-17

### Fixed — install.ps1 could not parse on Windows, at all

The first run on a real Windows box died in the parser, nine errors deep, none of them naming the cause.
`install.ps1` was UTF-8 with no byte-order mark, and **Windows PowerShell 5.1 reads a `.ps1` without a BOM
as ANSI**. Every em-dash arrived as three characters -- `E2 80 94` read as `a-euro-rightdoublequote` --
and the last of those bytes is a `"`, which closed the string it sat inside and took the rest of the file
with it.

PowerShell 7 defaults to UTF-8, so the parse check that runs on Linux was clean: the installer was broken
only on the platform it was written for. `install.ps1` and `install.cmd` are now **pure ASCII** -- no
em-dash, no curly quote, nothing above 0x7F -- so the encoding of the file cannot matter to anyone who
reads or rewrites it.

`tests/test_portability.py` asserts both files carry no non-ASCII byte, and the check was mutation-tested:
injecting one em-dash turns it red and prints the bytes responsible. Anything that puts a non-ASCII
character back into either file fails the suite rather than reaching a Windows user.

No engine, tool or limits-value change.

## v3.2.26 — 2026-09-17

### Added — `install.cmd`, because the default execution policy refuses `.ps1` files

The first Windows run of `install.ps1` returned "cannot be loaded because running scripts is disabled on
this system" — Windows' default execution policy, which blocks scripts before the installer's own checks
ever see it. The documented fix was to type the bypass by hand, which is a poor first instruction.

`install.cmd` is the two-line launcher: it runs `install.ps1` with `-ExecutionPolicy Bypass` for that one
process. No policy is set, nothing persists, and the README now leads with it. Running the `.ps1` by hand
still works, with the bypass spelled out for anyone who prefers it.

`tests/test_portability.py` scans `.cmd` files like the rest of the shipped code and asserts the launcher
carries a per-process bypass and never a machine-wide `Set-ExecutionPolicy`.

No engine, tool or limits-value change.

## v3.2.25 — 2026-09-17

### Added — `install.ps1`, so the Windows route is one command like the others

Linux and macOS have had `install.sh` from the start; Windows had a list of commands to type, and a
first attempt at them from a freshly opened PowerShell fails in three places at once. From the Windows
system folder, `git clone` cannot create the directory, so `cd k250-forge` fails, so
`.\venv\Scripts\pip` is not a command — one cause wearing three costumes.

`install.ps1` does what `install.sh` does — venv, `bleak`, a conservative `limits.local.json`, a summary
of the tools — and adds the check the Windows route was missing: it writes to a probe file in its own
folder first and, if that fails, names the real problem (this is a directory you do not own; move to one
you do) instead of leaving three errors to work out. It finds the interpreter by trying `py -3`, then
`python`, then `python3`, because the `py` launcher ships with the python.org installer but not with the
Microsoft Store build of Python. It asks package metadata for the bleak version for the same reason
`install.sh` does. It edits nothing outside its own folder: no PATH, no registry.

`tests/test_portability.py` now scans `.ps1` files like any other shipped code, looks for a Windows home
path the way it already looked for a POSIX one, and asserts the installer's promises: it gates on
`import bleak` rather than a fixed Python floor, it does not write PATH or the registry entries, it
refuses an unwritable folder, and it names python.org when no interpreter is found. When a PowerShell is
on PATH it also parses the script and fails on a syntax error; with none present it skips, the same way
`test_limits_form.py` skips without node.

Both of its failure paths were exercised: run against a read-only directory it refuses and exits 1, and
run against a venv that did not produce `Scripts\python.exe` it names that rather than carrying on.

No engine, tool or limits-value change.

## v3.2.24 — 2026-09-17

### Fixed — the Windows install steps never said where to run them

Running the Windows route from a freshly opened PowerShell lands in `C:\WINDOWS\system32`, which is a
protected system directory. `git clone` cannot create the folder there, so `cd k250-forge` then fails,
so `.\venv\Scripts\pip` does not exist — three errors with one cause, and the README caused it by
never saying where to stand. The Install section now opens with that rule (a folder you own, never a
system directory), and the Windows steps start with `cd $HOME`.

The other half of the same report was real and separate: `py -3` assumes the `py` launcher, which ships
with the python.org installer but **not** with the Microsoft Store build of Python. Both spellings are
now given, with the `python -m venv venv` fallback and a pointer to python.org if neither resolves, and
`.\venv\Scripts\pip` not being recognised is named as the consequence of a failed venv step rather
than a second problem.

No engine, tool, test or limits-value change.

## v3.2.23 — 2026-09-17

### Changed — the second half of the README stopped saying things twice

Everything from "Multi-channel" onward had drifted into restating itself: First steps re-explained what
trap 3 says about `CA` and what the Tools list says about `k250-stop`; the AI-agent section described
itself as cold-startable twice and repeated its own clamping paragraph in a table cell; and the roadmap
carried three separate items for one idea (a UI over the engine, a composer, a composer UI), plus a
"not worth doing" line that repeated both bullets of Status.

The untested part of multi-channel — different patterns per channel — moved out of Status and next to
the rules it belongs to, as a **Still untested** line. Status is now the two genuine gaps it was meant
to be. First steps, the AI-agent table and the roadmap are pointers rather than second copies.

### Changed — a review pass over the protocol section

The "Five traps" heading became **Other Tech Notes**, the retraction note about the earlier 55-second
claim was dropped (the claim itself is gone, so the correction no longer needs to keep it alive), and
the multi-channel rules lost their preamble. A cross-reference in the multi-channel list that pointed at
"trap 4" now names the note it means, since the heading it referred to no longer exists.

No engine, tool, test or limits-value change.

## v3.2.22 — 2026-09-17

### Changed — the README now says what the project is before it says anything else

The file opened with a release line that was already wrong (it named v3.2.18 after v3.2.21 had
shipped), and then restated the same safety rules four separate times. It is ordered now for someone
who has never seen the box: what the project is, the hard stops, how to install it, the detailed
safety contract, then the reverse-engineering.

The hard stops — the list the limits page already carried — are stated in full once, at the top, and
referenced everywhere else. Install comes before the detail, and the technical sections follow it.
Sentences that appeared more than once were cut, along with a firmware string printed twice, and the
safety wording no longer assumes a skin tone.

### Changed — one wording of the skin rule, in all three places

`limits-form.html` and the shipped `limits.json` both said the skin should settle "back to normal
pink". Both now say "back to normal" — the check is that the skin is unchanged, not that it matches a
particular colour. The README's copy of the rule follows.

---

## v3.2.21 — 2026-09-16

### Fixed — `install.sh` claimed "already on your PATH" when it was only true for the current session

The installer's PATH check tested the **live shell** (`case ":$PATH:"`), not whether the line was
persisted. If the current session happened to have `~/.local/bin` on its PATH (a parent shell
exported it, or you added it manually), it printed "already on your PATH" — but a fresh terminal
reads the rc file and wouldn't find the tools. The check now asks the real question: does the rc
file carry the `export PATH=…~/.local/bin…` line? It picks the right file per shell (`.bashrc` /
`.zshrc`, and on macOS the login-shell `.bash_profile` / `.zprofile` when present), reports
"persisted in <file>" when it's there, and otherwise prints the exact line to add. The live-PATH
state is no longer treated as persistence.

---

## v3.2.20 — 2026-09-16

### Fixed — `test_wrapper_cli.py` leaked the installed `limits.local.json` into its temp clone

The v3.2.19 regression test for the wrapper/engine ceiling fix could fail on an *installed* repo.
`skeleton()` copies the repo with `shutil.copytree`, which does not respect `.gitignore` — so on any
machine whose repo root held a `limits.local.json` (install.sh writes one), that file leaked into the
temp clone. The wrapper then correctly reported `limits.local.json` while section 4 asserted
`limits.json`, failing 1-of-24 — but only where the tool had actually been installed, so it passed on
clean checkouts and looked like the suite was green.

The temp clone is now a genuine clean checkout: `skeleton()` also excludes the gitignored per-install
limits artifacts (`limits.local.json`, `limits.json.new`) alongside the usual caches. Verified both
with and without a root `limits.local.json` present under it, and from a fresh clone.

---

## v3.2.19 — 2026-09-16

### Fixed — two ceiling-safety bugs from the v3.2.18-era rewrite

**1. `install.sh` refused Python 3.9 on a false premise (regression since v3.2.8).**
The installer hard-blocked any Python below 3.10, claiming "bleak 3.x declares Requires-Python
>=3.10" — but that only holds for the newest bleak. pip resolves the newest release that runs on
*your* interpreter, so a Python 3.9 box gets bleak 1.1.1 (which installs and runs fine) and never
touches 3.x at all. The static floor therefore blocked a Python that works. It's gone. The real
gates were already there and stay: `pip install` resolving (it names the actual reason a genuinely
too-old Python fails) and the `import bleak` check that follows. The README no longer repeats the
3.10-floor claim either.

**2. The wrapper and the engine enforced different ceilings when `K250_DIR` was set.**
`k250-scene` preferred `limits.local.json` only inside its `K250_DIR`-empty branch. With
`K250_DIR` set (the normal shim case) the wrapper fell back to `limits.json` while the engine
(`k250_play.py find_limits`) read `limits.local.json` — two different `power.max_percent` values on
the same run. The comfort default lives in the shipped `limits.json`; your tuned values belong in
`limits.local.json`, and a divergence meant the wrapper could clamp to one ceiling while the engine
enforced another. The preference is now derived from `DIR` (`$K250_DIR` or `$HERE`) in one place,
matching `find_limits` exactly, so both always read the same file. New regression checks in
`test_wrapper_cli.py` give a clone a `limits.local.json` that differs from `limits.json` and assert
the wrapper and engine both read the `.local` file. The shipped `agent-skill/SKILL.md` was updated
to name `limits.local.json` as the preferred ceiling file.

Also: `test_portability.py`'s 1d check restated the old floor; it now asserts the installer gates
on `import bleak` and carries no hardcoded `MIN_PY_*` floor, and that the README makes no 3.10
claim.

---

## v3.2.18 — 2026-09-15

### Changed — the repository was deleted and re-created, and the name is now lowercase

Rewriting the history (v3.2.16) removed the personal detail from every *reachable* commit, but GitHub
still served the old blobs by SHA — fetchable through the API and blob URLs, content intact. No SHA
was discoverable from the repo (no forks, no stars, nothing linking to them), but "not discoverable"
is not "deleted", so the repository was deleted and re-created instead.

**Lossless, as it turns out:** the same 49 commits and 22 tags went into the new repo, the release
pages were rebuilt from the same notes, and the description and topics were re-set. What went away is
exactly what was meant to: the old objects, which die with the deleted repository rather than waiting
on an unbounded garbage collection.

**The name is now `k250-forge`, all lowercase** — what the clone instructions have always said, and
what the working directory here has always been called. The v3.2.1 entry below describes a case
mismatch between the repo name and the README; that mismatch is now fixed at the source rather than
papered over in the docs, so the entry is left as written: a record of what was true then.

---

## v3.2.17 — 2026-09-15

### Fixed — the changelog quoted what it had just removed

The v3.2.16 entry described the redaction and, in doing so, repeated a couple of the phrases that had
been taken out. Reworded. (No section was written for this one at the time — the commit message was
the whole record, which is not the convention here, so it is written up now rather than left out.)

---

## v3.2.16 — 2026-09-15

### Changed — personal detail removed from the docs, the comments and the code

This repo described its own testing in more detail than a public project should. Removed:

- **Session narratives.** The setlist comments in `k250_show.py` read as a session narrative. They now describe the *shape* of each
  set (staged escalation, long hold with nobody reporting, denial set) with the engineering intent
  intact and the people out of it.
- **One person's verdicts.** Pattern notes were attributed to an individual and written as their
  personal favourites. The docstrings and tables now say which patterns worked and why, in neutral
  terms.
- **A name.** It was in the engine's docstrings, the setlists and `FINDINGS.md`.
- **The box's actual BLE address**, in five files. A random static address is not identifying for long
  — it changes on power-cycle — but it was still this device's address, so it is a placeholder now.
  `find()` matches on name or service UUID anyway, which is the durable route.
- **Anatomy in the safety text.** The placement warning is the most important paragraph in the repo
  and it still says percentages do not transfer between sites; it no longer names specific ones.

Kept deliberately: the calibration figures, the working power band, the sweep periods and the pattern
vocabulary. Those are the useful part, and the README now frames them as observations from one
placement rather than as numbers to copy.

Credit for the call: the repo's owner read it as a stranger would and said it was too personal.

---

## v3.2.15 — 2026-09-15

### Added — a tip cup, and the line in the README

`.github/FUNDING.yml` with Ko-fi, which gives the repo a proper **Sponsor** button (Ko-fi is a
first-class GitHub funding key, so it gets the logo and the right link rather than a generic custom
URL). GitHub Sponsors slots in beside it as one added line once that account is enrolled — the two
coexist behind the same button, which is why setting up one now is not a rework later.

The README says it in the same breath as the licence, and says the important part out loud: nothing
here is paywalled, nothing will be, and the ceiling clamping works identically whether or not anyone
ever tips.

Credit where due: the funding button is the one thing in this repo I could not verify from the
machine — Ko-fi sits behind Cloudflare and answers `403` to anything that isn't a browser, and the
headless browser gets the interstitial too. That one was confirmed out-of-band rather than from here.

---

## v3.2.14 — 2026-09-15

### Added — Apache-2.0, and a licence section that explains the choice

The roadmap had sat on "a licence — the owner's call, not a technical question" since v3.0. Decided:
**Apache-2.0**, full canonical text (fetched from apache.org, not transcribed), appendix attribution
line filled in.

Why this one, in the README rather than only in the file: the project is deliberately written to be
read, copied and adapted — a `limits.json` you rewrite for your own body, an operator skill you drop
into your own agent. Apache-2.0 keeps that open while adding an explicit patent grant and requiring
modified files to be marked, which matters for a tool whose whole point is a safety contract someone
else will edit.

Also corrected: the layout listing (which had lost `FINDINGS.md` and gained a stray duplicate) and the
tag list in **Versions**, which still stopped at `v3.2.10`.

---

## v3.2.13 — 2026-09-15

### Changed — the install notes for all three platforms, after actually running on all three

Every platform section was rewritten against what the installs actually do now, not what they did
when they were written:

- **macOS:** the section said "no extra packages" and left out the one thing that will stop a Mac
  user — the stock `python3` is often Xcode's **3.9**, below `bleak`'s 3.10 floor. It now says so, with
  the `brew install python@3.12` fix and the `PATH` ordering that makes `install.sh` pick it up.
- **Windows:** `py -3` takes your *newest* Python 3, which may still be 3.9 — the failure looks like
  a network error. Now documented, with `py -0p` and `py -3.12 -m venv venv`.
- **Linux:** added that it runs **headless** — no desktop session anywhere in the toolchain, which is
  how this repo is actually driven (a box on a headless server).
- **Windows is now field-verified:** the install, the engine, the limits contract and driving the box
  over BLE on real hardware. The only branch unexercised there is the stop tool's process-kill line.

The v3.2.12 entry below is left as written, with a dated note, because that is what was known when it
shipped.

---

## v3.2.12 — 2026-09-15

### Fixed — three test bugs found by the first real run on Windows

The engine, the limits contract and the command surface all worked on Windows on first contact. Three
suites went red, and every one of them was a mistake in a test:

- **`test_write_policy.py` asserted a wall-clock count.** `writes >= 10` for "1.0 s at 0.1 s ticks" is
  an assumption about the clock: Windows' ~15.6 ms timer granularity gives about 9 iterations in a
  nominal second, so a test about the write *policy* failed because of the platform's timer. It now
  counts the ticks the loop actually ran and asserts against those — verified timing-independent
  (10 ticks → 10 writes, 4 → 4, 1 → 1).
- **`test_stop_parsers.py` patched the wrong command.** The "cannot list processes" case replaced the
  POSIX listing command, so on Windows the check ran the real PowerShell listing and was vacuous.
  It now patches whichever command the running platform would use.
- **`test_wrapper_cli.py` compared raw path strings.** Under Git Bash the wrapper reports `/c/Users/…`
  while Python thinks `C:\Users\…` — two checks failed on a difference that does not exist on disk.
  Paths are normalised before comparison.

The pattern is the same as every other finding in this project: the thing lying was the test, never
the engine.

### Windows status, stated precisely
- Verified on real hardware: clone, venv, `pip install bleak`, engine import, `--list` (35 patterns),
  `--limits-show`, session budget.
- **Not** verified there: BLE I/O — `k250_status.py` reaching the box, and the stop tool's
  process-kill path with a pattern running. No claim is made about those.

> **Update, later the same day:** the Windows box went on to find the box over BLE and drive it —
> real hardware, real patterns — so the BLE half of that gap is closed. The docs above are left as
> written because that is what was known when this version shipped; what remains unexercised on
> Windows is only the stop tool's `killed pattern pid N` line (the `PW=0` stop path is the same code
> everywhere, and is verified).

### Verified
- All eight suites pass on Linux after the changes (and the three touched suites are the ones the
  Windows run flagged).

---

## v3.2.11 — 2026-09-15

### Fixed — the last wrapper-only inspection command

`--limits-show` was the other thing only the bash wrapper could do, so on Windows there was no way to
read the active ceilings before running anything. The engine prints them now:

```powershell
.\venv\Scripts\python k250_play.py --limits-show
```

Same fields as the wrapper, same wording, read-only, no box needed — and printed from the same
resolver, so which file it names is the file it will actually use.

The first Windows walkthrough also showed that the engine printed the ceilings on *every* run's
startup; that was already there, but it is not a pre-flight check, which is why this exists.

### Verified
- Engine and wrapper produce identical `limits file` / `POWER ceiling` / `POWER start` / `stop word`
  lines, asserted field-by-field in `test_wrapper_cli.py` (now 18 checks) so the two cannot drift.
- All eight suites pass.

---

## v3.2.10 — 2026-09-15

### Fixed — Windows had no way to find out what the patterns are called

`--list` lived in the bash wrapper. The documented Windows route is the engine directly, no bash, so
on Windows the 35 pattern names were discoverable only by reading `k250_play.py`. Passing an unknown
pattern did print them, but as one comma-joined line, as an error.

The engine now owns the list:

```powershell
.\venv\Scripts\python k250_play.py --list      # 35 patterns, no box needed
```

- The wrapper delegates to it (`exec "$PY" "$PLAY" --list`), so there is one implementation and the
  two can't disagree — a test asserts they produce identical output.
- No pattern at all now says what to do instead of printing usage; an unknown pattern prints the list
  one per line, on stderr, with the name it didn't recognise.
- Found while walking the Windows path before a real-hardware test on that platform, i.e. before it
  cost anyone a confusing ten minutes.

### Verified
- Engine `--list` from an unrelated directory: 35 patterns, exit 0 (this is the Windows code path,
  exercised on Linux).
- Wrapper `--list` unchanged; `test_wrapper_cli.py` now cross-checks wrapper against engine (13 checks).
- All eight suites pass.

---

## v3.2.9 — 2026-09-15

### Fixed — the install asked for a Python that cannot install the dependency

The README said **Python 3.8+** and `install.sh` checked nothing. `bleak` 3.x declares
`Requires-Python >=3.10`. So a Linux user on a distro with 3.8 or 3.9 — Ubuntu 20.04, Debian 11 —
followed the instructions exactly and got:

```
ERROR: Could not find a version that satisfies the requirement bleak
```

which reads like a network or PyPI problem, not a version one. That is the precise class of failure
the preflight section exists to catch, and it was the one thing the preflight didn't check.

- `install.sh` now enforces a **3.10 floor** (`MIN_PY_MAJOR`/`MIN_PY_MINOR` at the top of the file,
  checked before the venv is built) and says what to do on each platform. Verified by pointing it at
  a stub `python3` that reports 3.9: it refuses with the explanation above and exits 1.
- The README's requirement now reads **3.10+**, with the reason stated — it is *bleak's* floor, not a
  preference.
- `tests/test_portability.py` now reads the floor out of `install.sh`, compares it against the
  installed bleak's own `Requires-Python` metadata, and asserts the README states the same number.
  Three numbers in three places, one check.

### Also, from the Linux install walk-through
- **The venv is not a style choice.** Debian/Ubuntu/Fedora block `pip install` into the system Python
  (PEP 668, `externally-managed-environment`); `install.sh` builds its own venv, so anyone who
  `pip install bleak` system-wide first gets an error that looks unrelated. Now stated.
- **bash 3.2+** is what `install.sh` and the wrappers need (any distro default; Alpine needs
  `apk add bash python3 python3-venv`).
- Everything else in the Linux section held up: BlueZ running, `~/.local/bin` on `PATH`, `rfkill`
  when scanning finds nothing, and never as root.

### Verified
- Fresh end-to-end install after the change: `bleak installed (bleak 3.0.2)`, symlinked
  `k250-scene --list` works from an unrelated directory.
- The old-Python path: refuses, explains, exits 1.
- All eight suites pass (portability now 16 checks).

---

## v3.2.8 — 2026-09-15

### Fixed — two Windows gaps on the path the README tells Windows users to take

Neither was reachable from macOS or Linux, and neither showed up in a clean-checkout audit, because
the Windows route is *python directly* — no wrapper, no bash — and the tests all ran the wrapper.

**`k250_stop.py` failed silently on Windows.** Before zeroing the pads it kills any running pattern
so nothing re-sends power; it found them with `ps -eo pid,comm,args`, which does not exist on
Windows. The whole thing sat inside `except Exception: return` — no kill, no message, and the tool
still reported a clean stop. That is the worst way for a stop to fail. It now lists processes per
platform (PowerShell `Get-CimInstance Win32_Process` on Windows, `ps` on POSIX) and **says so when it
cannot list them**, instead of continuing as if nothing were running. The macOS branch is fixed too:
`comm` there is `Python`, and the filter was case-sensitive, so it never matched a pattern.

**`k250_ctl.py` threw on Windows.** It drives a FIFO, and `os.mkfifo` does not exist there — an
`AttributeError` traceback for a tool someone might reasonably try. It now explains that Windows has
no FIFO and points at the engine, which is the equivalent tool.

Also: a guard for the class. `tests/test_portability.py` now checks that POSIX-only calls
(`os.mkfifo`) never appear unguarded, since Windows is a documented platform.

### Added
- `tests/test_stop_parsers.py` — 13 checks. Neither platform's listing can be run on the other, so
  the decision logic is factored into pure functions and tested against captured `ps` output (Linux
  and macOS shapes) and PowerShell output: finds patterns, never kills itself, ignores an unrelated
  python, ignores a `grep` that merely mentions the engine. The "cannot list processes" path is real:
  point it at a command that does not exist and it must return a reason, not silence.

### Verified
- Live on the real box: `k250-stop` exercised the new listing path end to end — no patterns running,
  `PW=0 written to channels [1]`, exit 0.
- All eight suites pass.

---

## v3.2.7 — 2026-09-15

### Fixed — a test that only passed on a machine that never used the box

`tests/test_wrapper_cli.py` asserted that `--limits-show` didn't write into the repo with

```python
not os.path.exists(os.path.join(ROOT, "session.json"))
```

That is a **proxy**, not the property. `session.json` is the session ledger, and a real run whose
working directory is the clone leaves one there — so the suite failed, permanently and reproducibly,
on the author's own bench (where patterns run constantly) while passing on any clean checkout. Green
only where nobody has ever driven the box: **the v3.2.1 bug inverted.** Introduced in v3.2.3 by me,
in the same commit that added the check.

Now the test snapshots the whole repo tree (path, size, mtime) before and after the `--limits-show`
run and compares. A pre-existing ledger is fine; a new file, a rewritten ledger, or any other write
is caught and named. It also asserts the inverse — that whatever state the run *does* touch landed in
the configured working directory, not the repo.

### Verified
- Reproduced first: with a ledger sitting in the repo (as on the bench), the old check failed and the
  suite exited 1.
- Mutation-tested the replacement rather than trusting it: injecting a `touch` into the
  `--limits-show` path makes the suite fail and name `MUTATION-PROOF`; reverting the injection
  restores a pass. A check that cannot fail is not a check.
- Passes both with a ledger present and on a clean tree.

---

## v3.2.6 — 2026-09-15

### Added — `battery.report_below_percent`, and the noise it replaces

Battery was mentioned in five places and the numbers disagreed: the README's roadmap wanted a warning
below ~25 % and a refusal below ~15 %, `FINDINGS.md` called 12 % "low-ish", and every status read
invited a comment on a pack that sits in the 20–30 % band on a bench charger. That is how a report
becomes noise, and noise gets tuned out — right up until the box goes flat mid-scene.

There is now one figure, in the config, where the other limits live:

```json
"battery": {
  "report_below_percent": 10,
  "note": "NOT a limit -- a reporting rule…"
}
```

**Mention the charge at or below it, or if the box drops off BLE and low charge is the plausible
cause. Nothing above it.** The shipped `limits.json`, `limits-form.html` (new field on the limits
page, defaulting to 10) and the operator skill all carry the same value, and a test keeps them
agreeing.

The README roadmap line proposing a ~25 %/15 % warning is **removed**, not renumbered around: it was
the loudest of the disagreeing voices.

### Added
- `tests/test_limits_form.py` — 20 checks. `limits-form.html` generates the entire contract in the
  browser, and nothing tested it: an edit to `build()` could drop `power.max_percent`, or add a config
  field (like this one) that the next regeneration silently loses. The test runs the page's own
  JavaScript in node against a small DOM stub, then asserts the output parses, still carries every key
  the engine reads, that the engine's own `load_limits`/`apply_limits` honour its ceiling and
  per-channel caps (and that a command line still cannot raise it), and that the form and the shipped
  file agree on the battery threshold. Skips cleanly where node is absent.

### Verified
- Run under node v22 with a DOM stub: 12 generated channel controls found, output valid, default
  battery threshold 10, engine reads a 10 % ceiling from it, per-channel caps round-trip.

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
