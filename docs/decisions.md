# Decisions

The design decisions the code relies on, one dated entry each, in one place. A
rule the code must keep is a row in [`invariants.md`](invariants.md); this file
holds the choices behind those rules and the choices that are not rules at all
(what is vendored, how the lock moves, what a feature may never require), so
that they are neither re-litigated nor re-derived. Until 2026-10-13 they lived
in CLAUDE.md parentheticals, in review Addenda (which only catch a decision
made during a review), in build plans, and in an assistant's memory outside
the repository.

**Reading an entry.** The heading is the date and the decision in a few words.
The body is the decision in a sentence or two, why, and where it is enforced
or recorded: a registry id, a test, a script, or "review only". Entries are
appended, never edited; a reversed decision gets a new entry naming the one it
reverses. A CLAUDE.md bullet cites an entry by date instead of carrying the
story. Dates are the day the decision was recorded in the repository; where a
practice predates its record, the entry says so.

## 2026-04-07 — Releases are `N.N.N`, cut from main, no release candidates

A version is `N.N.N` with no suffix and no leading `v`; a release is cut from
`main` only, merged back `--no-ff`, its branch pushed and kept, and the HTML
help rebuild is its own commit ahead of the version bump. **Why:** one shape of
release means the scripts can refuse anything else, and a readable diff on the
release commit. **Where:** `scripts/release_prep.py` and `release_publish.py`
refuse otherwise; `scripts/README.md` "Conventions enforced".

## 2026-05-03 — One MCP server per config with a profile

Each config directory with a v2 profile is its own `termapy --mcp <cfg>`
process and a separate entry in the MCP client's configuration; there is no
hot-switching of configs inside a running server, and none is to be added.
**Why:** one process owns one port, so there is no contention; the config
directory is the device boundary (its log, history, captures and plugins all
describe one device); the client's server list is already the device
registry, and switching inside termapy would duplicate it in a worse place.
**Where:** review only; `help/mcp-server.md` "Architecture: one server per
device".

## 2026-06-21 — `vendor/` holds only unmaintained dependencies

Only abandoned libraries are vendored (pyserial, xmodem, ymodem); everything
maintained is a declared dependency or an optional extra. Recorded on this
date; the practice is older. **Why:** vendor the dead, depend on the living. A
license question about a maintained library is answered as for a pip
dependency, not as copied source. **Where:** `tests/test_credits_data.py`
diffs the vendored tree against the `Credit` table; the policy itself is
review only.

## 2026-07-04 — No mocking or monkey-patching without confirmation

Tests drive real engines: the DEMO rig and `FakeSerial`, a `CLITerminal`,
Pilot on the real app. Where a test needs to control an input, the code takes
it as a parameter (`source=` for port enumeration) rather than being stubbed.
**Why:** a stub swallows every signature change silently; two did when
`source=` was added. **Where:** review only; CLAUDE.md Testing.

## 2026-07-14 — Exact counts live in one release-refreshed spot each

The test count lives on the README's "Test coverage" summary line, the
coverage percent on that line and its badge, the ty count on its badge, and
per-module line counts in the ARCHITECTURE.md tree. Prose everywhere else uses
floor language ("more than 2000 tests", "100+ algorithms") and never a figure.
**Why:** a hand-maintained number drifts and churns every diff; a floor stays
true as the real number grows. **Where:** `scripts/release_prep.py`, whose
substitutions fail loud (`re.subn` + `die()`) when a target is reworded.

## 2026-07-16 — `app.py` health is not a line count; no size gate

A feature-rich TUI has irreducible essential complexity, so a large `app.py`
is honest. Health is three things, none of them size: logic stays out of the
UI, new device behavior is a plugin, no duplication. A `release_prep`
line-count ratchet was built and removed. Sharpened 2026-08-18: the metric
worth moving is the untested footprint, and an AST audit found no cohesive
Textual-free module left to extract, so Pilot is the lever, not extraction.
**Why:** relocating UI code into helpers moves lines without removing
complexity; the one honest extraction is a self-contained widget or pure
function that gains a test. **Where:** review only; CLAUDE.md Architecture and
Testing.

## 2026-07-16 — Headless Pilot does not reproduce focus or worker-timing bugs

`app.run_test()` does not model real-terminal focus and blur or `@work`
scheduling, so a TUI focus or input-state bug is fixed by live iteration with
a candidate fix, never by building a headless reproduction. State transitions
(config switch, dispatch paths, dialogs) go to Pilot. **Why:** headless
restores focus correctly and masks the bug. **Where:** review only; CLAUDE.md
Testing.

## 2026-08-17 — `/proto.crc.reverse` is a permanent hand-rolled holdout

Its documented grammar puts keywords after a to-end-of-line `cmd=`, which the
declarative parser would swallow into the trigger text; reaching a primitive
such as `$(*NAME)` is never a reason to migrate a command to `params=`, since
the primitive is public. **Why:** the hand-rolled parser is load-bearing, not
legacy. **Where:** CLAUDE.md "Declarative Command Parameters"; the why-comment
on the handler.

## 2026-08-20 — Structural rules are pytest guards on `ast`, not CI greps

A boundary rule lives in `tests/test_architecture.py`, matches on AST nodes,
keeps an explicit allowlist with a companion test that fails when an entry
goes stale, and is proven to fire with a throwaway probe. Converting a grep
replaces it. **Why:** pytest is the loop that runs before a commit and on
Windows; a CI-only grep kept `main` red for a month while local tests were
green, and a grep once forced a comment to be reworded to appease it.
**Where:** registry `invariants-registry` and every guard in that file.

## 2026-08-20 — Infrastructure never lives under `builtins/`

What core needs at startup or on the dispatch path is core; a command that
exposes it is a thin surface under `builtins/`. Decided when the variable
engine was found under `builtins/` with five core modules importing from a
plugin, and moved to `variables.py`. **Why:** a plugin's privates are free to
change and core has no contract with them. **Where:** registry
`infra-not-under-builtins` (review) and `core-imports-published-only` (the
import direction, enforced).

## 2026-09-15 — Data-folder lifetime is a rule, not a migration

A data folder exists while something is in it: created at the write, pruned
when empty at load and stop, both halves in `folders` as named operations.
Chuck: "if a user empties a folder then it is deleted, that is tidy." **Why:**
a migration fires once and goes inert; a rule keeps acting, and naming it a
migration hid the ongoing behavior. **Where:** registry `folders-ensure-only`.

## 2026-09-30 — The lock moves at release only, and the lock is what is audited

`uv.lock` ships with every release and is upgraded by `release_prep` (`uv lock
--upgrade`), with the suite, tox, ruff and ty gating the result; between
releases the lock is left alone, no Dependabot, the one exception a reachable
CVE fixed with `--upgrade-package` on a chore branch. The audit is of the
exported lock, every extra and group, not of an environment. Gate tools are
pinned exactly. **Why:** an environment audit sees only what it installed; the
CI job this replaced audited zero packages and stayed green for months.
**Where:** `scripts/audit_lock.py` as a `release_prep` gate and the CI `audit`
job; CLAUDE.md Release.

## 2026-10-02 — `/mem.*` is optional, and standard when present

No termapy feature (profiles, device files, symbols, MCP, staleness) may
require a `MEM` interface on the device; "no memory interface" is a normal
state. Additions to the wire spec are optional keys or verbs with a defined
meaning for absence; the reference firmware stays at R, W, INFO and optional
M. **Why:** the spec's value is that one small drop-in buys everything, and a
device without it loses nothing else. **Where:** review only;
`help/memory.md` "The wire spec".

## 2026-10-09 — The ty gate covers `tests/`, and both gates run at every commit

`ruff check` and `ty check` run over `src/termapy/` and `tests/`, with all
extras synced, from the pre-commit hook and again in `release_prep`. **Why:**
while ty ran on `src/` alone at release time, 101 problems built up in tests
unseen, 12 of them real mistakes. **Where:** `.githooks/pre-commit`;
`release_prep` refuses a release otherwise.

## 2026-10-12 — One boolean vocabulary, one reader; `TERMAPY_DEMO_FLEET` stays a switch

Every place termapy turns user text into a boolean reads it through
`scripting.parse_bool`; absence takes the default the reader declares, a
present unrecognized token always errors, and an environment gate with a bad
value refuses startup naming the variable. `TERMAPY_DEMO_FLEET` is
presence-only, a switch rather than a boolean, and is documented as such.
**Why:** three vocabularies had grown in core while the guard scanned only
commands. **Where:** registry `bool-one-vocabulary`.

## 2026-10-13 — CLAUDE.md stays the engineering rulebook; each fact is stated once

The project's rules, conventions and rationale stay in CLAUDE.md, whose name
is the AI tooling's convention and whose content is for every contributor;
CONTRIBUTING.md defers to it and the invariants registry indexes it. Moving the
design prose to `docs/` was considered and declined. Each fact is stated in
full in one file and linked from the others: ARCHITECTURE.md for the
narrative (threading, loading order, folders, device files), the
plugin-writing guide for the PluginContext API, `scripts/README.md` for
release mechanics, this file for decisions. **Why:** the threading model
existed in three files and one copy was wrong; the project's own rule for
tables ("one table, iterated by every surface") applies to prose. **Where:**
review only.

## 2026-10-13 — Two loopbacks, two labels: `sw_loopback` runs anywhere, `hw_loopback` is gated

`sw_loopback` is the software loopback, pyserial's virtual `loop://` port; a
marker on every test that uses it, and it runs anywhere, CI included.
`hw_loopback` is the hardware loopback, the TX-RX-jumpered adapter
(`tests/test_hw_loopback.py`, `TERMAPY_HW_LOOPBACK_SN`); its marker is gated.
It runs before a branch is handed for merge whenever the adapter is attached,
and `release_prep` refuses a release unless it passes: `--require-hw-loopback`
turns an absent, busy or silent adapter into a failure rather than a skip, and
releasing without it is an explicit `--skip-hw-loopback` on the release command
line. A skipped run is reported as skipped, never as a pass. It never runs on
GitHub Actions in any form: both of its flags are refused there before
collection, so a runner's ports are never enumerated, let alone written. Every
flag, file, marker, variable and message names its loopback by label. Chuck:
"having real hardware break because it was never tested is embarrassing", "it
is critical that this doesn't ever run on github", and "I need to have clear
words and labeling for these like sw_loopback and hw_loopback." **Why:** the
hardware loopback is the only test of the OS driver, bridge FIFO and real baud
pacing, which `FakeSerial` and the software loopback cannot model; it had been
opt-in since August with no gate passing the flag, so a release could ship with
it never run, and "the loopback suite" read as either one. **Where:** registry
`hw-loopback-demanded-fails-loud`, `hw-loopback-never-on-github` and
`sw-loopback-labeled`; `tests/test_hw_loopback_gate.py`; `tests/conftest.py`;
`release_prep` step 5.

## 2026-10-13 — The invariants registry says what each row is

`invariants.md` is "the rules the code relies on", and only some of them are
invariants in the strict sense (a predicate over state that every operation
preserves). Each row now carries a `category`: `data`, `concurrency`,
`structure`, `repository`, `contract` or `requirement`. The strict state
invariants are the `data` rows and the ownership rows under `concurrency`; the
`requirement` rows are the seed of a specification that does not yet exist,
and they stay in the registry until it does. **Why:** each category has a
different strongest guard, and a `data` row written as "who reads it" rather
than "what set it is in" is a candidate to move from a test to a constructor.
**Where:** the registry's intro and the `category` column.

## 2026-10-13 — Hex: one reader in any case, uppercase out; text goes in quotes

Every byte payload a user types is read by `protocol.parse_data`: hex in any
spelling and either case (`01 03`, `0x01 0x03`, `0X01`, `0103`) and quoted
text, freely mixed. `/proto.crc.calc` follows `/proto.send`, so text must be
quoted (`/proto.crc.calc crc32 "123456789"`); unquoted text is a Parse error
that says so. Every hex termapy prints or returns is uppercase, matching the
datasheets and protocol specs its users read. Chuck chose both: "same reader
as /proto.send", and uppercase after asking whether a standard exists (none
does; software tools lean lowercase, hardware documents uppercase, and every
reader accepts both). **Why:** four `/proto.crc.*` commands each had a
private tokenizer with its own rules, and calc's guessed: `0x01 0x03 ...` and
`0103...` silently got the CRC of their characters. Values disagreed on case
(`/proto.send` lowercase, `/mem` uppercase). **Where:** registry
`hex-one-reader` and `hex-output-uppercase`; `tests/test_hex_vocabulary.py`.

## 2026-10-13 — Plugins are cookie-cutter: one handler shape, one template

A command's handler is `_handler`, a subcommand's `_handler_<sub>`, typed
`(ctx: PluginContext, args: str) -> CmdResult`, in the built-ins, the demo
plugins and the example plugins; the example plugins are the template and
match the built-ins (typed, `help=` keyword, a returned `CmdResult`). Chuck:
"it should be cookie cutter to add a new function to the system." **Why:**
five naming styles were in use, three of them in `proto.py`, and the examples
taught an untyped handler with no return value and an old folder name.
**Where:** registry `handler-naming`; `help/writing-plugins.md` "Plugin file
structure" and "Bytes and hex".
