# Decisions

The design decisions the code relies on, one dated entry each, in one place. A
rule the code must keep is a row in [`rules.md`](rules.md); this file
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

## 2026-10-13 — Hex: one reading in any case, `0x` marks one byte, uppercase out; quotes only where text belongs

Hex reads the same in every command, any spelling and either case (`01 03`,
`0x01 0x03`, `0X01`, `0103`). A `0x` prefix marks exactly one byte:
`0xDEADBEEF` is refused with the fix in the message, because it reads as a
number whose byte order nobody stated. Bare run-together hex is bytes in the
order written, so unquoted text that happens to be hex (`CAFE`, `DEADBEEF`)
is bytes, not text; that is the rule, documented, and a behavior change for
`/proto.crc.calc` worth a line in the next release notes. Quotes exist only
where a payload can hold text: `/proto.send`, `/proto.crc.calc`,
`/traffic.snoop` and `.pro` files read with `protocol.parse_data`; the packet
commands (`/proto.crc.verify`, `.find bin=`, `.reverse`, `/mem.write`) are hex
only, with `parse_hex`. The same `0x` rule applies to a device's lines in a
hex capture, which abort on `0xDEADBEEF` as on any non-hex line. Every hex
termapy prints or returns is uppercase. Chuck chose each: calc reads "the same
as /proto.send"; uppercase, after asking whether a standard exists (none does;
software tools lean lowercase, hardware documents uppercase, every reader
accepts both); quotes "only where text belongs", after asking why quotes are
needed at all (a hex-digit token is ambiguous between bytes and characters,
and quotes are the marker that settles it, as `asc=` does for
`/proto.crc.find`); and "0x marks one byte". **Why:** four `/proto.crc.*` commands each had a
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

## 2026-10-13 — Text is escaped into markup; widgets take Text; agents and logs get plain

termapy writes two kinds of string, text and Rich markup, and never lets the
first reach a markup parser as the second. A value interpolated into markup
goes through `markup.escape_markup()`; a value that is markup already carries a
`_markup` name. Help text is prose: the renderer escapes it, and a whole-line
`help_dynamic.green` state line is the one markup a `long_help` carries.
Textual's widgets parse Textual's own markup, which has no clean escape
(`[Rec]` and `[$var]` vanish though Rich's escape leaves them alone, and a
trailing backslash still eats the close tag), so a widget showing text takes
`markup=False` or a `rich.text.Text`. The consumers that are not terminals,
the `--json` envelope, an MCP agent's `output_lines` (raw markup until now)
and the session log (which skipped the markup channel), get it flattened with
`markup.markup_to_plain()`. `escape_markup` also corrects Rich's own escape,
which handles a trailing backslash right only where a tag follows. Chuck:
"that is a big problem with textual. It has hit me many times." **Why:** Rich
drops a `[word]` tag it does not know without an error. `/var` listed
`$(TAG) = [ok] boot` as `$(TAG) =  boot`; `/help proto` said "[] sections",
and the CLI gold file had recorded it; `/port.connect` lost its Syntax line;
`/proto.crc.c` displayed `crc ^ data` for `crc ^ data[i]`; markup f-strings
interpolated values in 20 modules with no escape, and the demo traffic plugin
sent markup down a plain channel, where its tags showed as text. **Where:**
registry `markup-escape` (`test_text_reaches_markup_escaped`,
`tests/test_markup_escape.py`) and `widget-text-literal`.

## 2026-10-14 — The registry is `rules.md`: it lists rules, not only invariants

`docs/invariants.md` became `docs/rules.md`, titled "Rule registry", and its
self-check id `invariants-registry` became `rules-registry`. The 2026-10-13
entry gave every row a category, and only the `data` rows are invariants in
the strict sense; the file is the list of every rule the code relies on,
which is how CLAUDE.md already described it. The `contract` category widened
in the same change, from a handler's pre- or postcondition to a pipeline
stage's too, so that what the serial reader promises its consumers has an
honest home. Chuck, on a delivery rule filed as a `requirement`: at that
level "I would need to say you need a tui, you need a help menu" -- product
features are not registry rows. Earlier entries and dated reviews keep the
old name; they record what was true when written. **Why:** a file named for
one category of its contents invites rows to be argued into the wrong
category. **Where:** registry `rules-registry`
(`test_rules_registry_matches_the_guards`).

## 2026-10-14 — Anything stateful is tested twice and long, not once

A buffer, queue, watcher or claim is tested beyond the happy path three
ways: the operation twice in a row, two items arriving in one batch, and a
sustained run with no consumer. **Why:** the serial-path review that day
found three bugs present since March in lines the suite already executed,
each one invisible to a single call: a second `/expect` re-matched the first reply,
a line arriving in the same read as a match was never buffered, and the raw
RX queue kept every byte of a session because nothing read it between
commands. Every test of those paths called the operation once. **Where:**
CLAUDE.md "Testing"; review only, since no `ast` scan can tell a stateful
feature's tests from any other.

## 2026-10-14 — `/raw` sends its text and nothing after it

`/raw <text>` puts the text on the wire exactly as typed, encoded with the
cfg encoding, with no line ending, in every frontend; `/term.send` is the
literal send that adds the line ending. Until this date `/raw` had three
implementations with two behaviors: a typed `/raw` appended the cfg `eol`
everywhere, while the CLI's registered command, reached by any other route,
sent none. **Why:** the CLI's help and `/term.send`'s own help already
promised no line ending, a line with its ending already has a literal
command, and a partial line or a device that frames without a line ending
had no command at all. A typed `/raw` that relied on the line ending now
needs `/term.send`. **Where:** review only for the choice; the behavior is
pinned by `test_raw_sends_exactly_its_text`, `test_raw_sends_data` and
`test_raw_send_bypasses_transforms`.

## 2026-10-14 — A command string is one command; several are a `.run` file

An on-connect field (`on_connect_cmd` and its `tui_`/`cli_`/`mcp_`
variants) holds one command, dispatched as written, and `-e` takes one
command per invocation. Neither is ever split. Several commands are a
`.run` file the field or `--run` names. Until this date the on-connect
fields were split on a newline or a typed `\n`, in four inline copies, and
a repeated `-e` silently ran only the last. **Why:** the `\n` separator
predates string parsing. Splitting after JSON had already decoded the
escapes cut a payload's own `\n` (`/proto.send "AT\r\n"` became two broken
commands), and a separator inside one string is a second command grammar
beside `.run`, which already has comments, delays and `/expect`. The
v29 -> v30 migration moves each existing chain into `run/<field>.run`
(never overwriting) and points the field at it, so an old config keeps
doing what it did. **Where:** `config.on_connect_commands` (a field holding
a line break is skipped, and `validate_config` names it), `entry._OneCommand`
for `-e`, `migration._migrate_v29_to_v30`.

## 2026-10-17 — The floor is Python 3.11, and ruff's pyupgrade holds the code to it

Python 3.10 support is dropped (3.11 through 3.15 are supported), and the
code is written for the floor rather than for the oldest version ever
supported: `collections.abc` over `typing` for abstract types, unquoted
annotations under `from __future__ import annotations`, `datetime.UTC`, the
builtin `TimeoutError` (asyncio's and concurrent.futures' are the same class
since 3.11), and `fromisoformat` reading a trailing `Z` itself. **Why:** a
workaround outliving the version that needed it reads as a requirement, and
nobody can tell which ones still matter. **Where:** ruff's `UP` family in
`[tool.ruff.lint]`, which flags the next set of upgrades when the floor moves;
the gate (`.githooks/pre-commit`, `release_prep`) runs it. Other ruff families
were surveyed the same day and land one per branch, findings reviewed:
the gate's scope (`scripts/`, `examples/`), then B, A, chosen RUF rules,
small style families, and RUF100 last. S, ANN, D, PL, E501, TRY, T20, DTZ,
BLE, SIM and PTH are not planned: they mostly flag the project's deliberate
choices. `ruff format` is a separate decision.

## 2026-10-17 — Code is formatted by ruff, always, with no exceptions

Every Python file the project owns is formatted by `ruff format`, and nothing
is hand-laid-out: no aligned columns, no wrapping to taste, no `# fmt: off`,
not even for data tables. **Why:** code that always has the formatter's layout
reads the same in every project, and layout stops being something to write,
review or argue about. The one-time cost was one commit touching 288 files,
plus a second pass for two files whose layout settles only then (ruff
0.16.8); both hold nothing but `ruff format` output, `.git-blame-ignore-revs`
makes `git blame` skip them, the commit before it is tagged `pre-format`, and
`git fdiff` (a textconv diff driver, `scripts/format_textconv.sh`) formats both
sides so a branch older than it still diffs cleanly. Excluded: the vendored
tree, the generated HTML, and the generated `usb/_vendors_full.py`. **Where:**
`[tool.ruff.format]`; the pre-commit hook and `release_prep` run
`ruff format --check --no-cache` over the whole project (the cache records
what `ruff format` wrote, even when a second pass would still move it).

## 2026-10-17 — Each path loads only what it shows

A library is imported on the path that uses it, never at the top of a
module every path loads. Investigating a slow-feeling TUI start found
termapy's own startup unchanged since v0.74 (the felt slowdown was `uv run`
reinstalling the package while the MCP server held `termapy.exe`), but it
also found the print-and-exit flags and the CLI one-shot paying for
libraries they never use, each through a transitive import:
`importlib.metadata` from `entry.py`'s eager `--version` lookup, `frist`
from tables `scripting.py` built at import, Rich from `termapy.markup`
reached through the `plugins` package, `urllib` from the update check, and
prompt_toolkit from `cli.py` even for `-e`. Measured before and after the
fix, on Windows:

| Path | Before | After |
|---|---|---|
| `--ports` | 274 ms | 135 ms |
| `--usb` | 455 ms | 128 ms |
| `--chips` | 151 ms | 66 ms |
| `--check` | 246 ms | 77 ms |
| `--cli -e` one-shot | 640 modules | 436 modules |
| `import termapy.app` (TUI) | 0.64 s | 0.52 s |

**Why:** the flags and one-shots are what scripts, agents and the watcher
run over and over, and a cost one path needs should not be charged to the
rest. **Where:** the techniques are in ARCHITECTURE.md "Import cost"; the
guard is `tests/test_cli.py::TestPathsStayLight` (registry
`flag-paths-light`). The same day `termapy.protocol` stopped building
`GENERATORS` / `GENERATORS_FROM_ENTRY` at import, a crcglot 0.8.0
leftover that touched every language's `.generator` and so loaded all
eleven `crcglot.lang` modules for the one function that generates code;
it now reads `crcglot.LANGUAGES[lang]` there. The next cost is in
crcglot itself: building `crcglot.VERBS`, which termapy reads at plugin
load, imports every language generator though no verb generates code.

## 2026-10-18 — A name is honored at its size and access; `/mem.write` takes a value there

A named target is the symbol's whole extent and nothing past it.
`/mem.read` of a symbol with no type reads the unsigned integer of its
size when that is 1, 2, 4 or 8 bytes, and `/mem.write` at a named scalar
(typed, of such a size, or a register) takes a number in the address
grammar's vocabulary and encodes it at that width in the device's byte
order. Hex bytes remain the form for a literal address, an offset and an
array, bounded by the symbol a name points into. A type or a payload that
would run past the symbol's end is refused, and a symbol in `text` or
`rodata` defaults to `access: ro`, which every writing command refuses.
**Why:** a linker map knows two facts about every name, where it is and
how big it is, and the memory commands used only the first: `/mem.read
sCmdCount` asked for a type the map cannot supply, `u32` on a 2-byte
variable read the next one, and a byte-order-reversed hex payload was the
only way to set a register. Making the user spell the bytes of a named
value is the byte-order mistake the rest of termapy designs out.
**Behavior change:** `/mem.write gTemp 2C01` (bytes) is refused on a
named scalar; write `300` or `0x12C`. Bare digits are decimal, as in the
address grammar. **Where:** registry `mem-named-bounds` and
`section-default-access`; `tests/test_mem_commands.py`,
`tests/test_symbols_table.py`.

## 2026-10-18 — A build's ELF is the symbol source; pyelftools reads it

`/sym.import` reads an ELF as well as a linker map, and the ELF is the
preferred source. Its symbol table gives every name, address and size,
and its DWARF debug info gives each variable's C type: signedness, float,
pointer, enum, array element, struct members. A linker map has no types
at all, and its layout differs by toolchain. A type is written only where
DWARF states it plainly (registry `elf-types-or-none`). The format is
recognized by the file's signature before any text marker (registry
`converter-magic-first`), so there is no separate command. pyelftools
(public domain, pure Python, no dependencies) is a runtime dependency,
imported only inside the converter. **Why:** names without types made
`/mem.read` read every value as unsigned; every current 32-bit toolchain
writes ELF with DWARF because every debugger reads it. Measured on the
board build (XC32 v5, DWARF 2): 1,083 of the map's 1,097 symbols
identical, none missing, 180 more; the 6 that differ are map converter
errors (a function's `.rodata.<function>` constant pool named after the
function); 1.46 s per import. The map converter stays for toolchains
without an ELF, and as the only source of the linker script's named
regions. **Where:** `symbols/converters/elf.py`, `tests/test_symbols_elf.py`
(fixtures from GCC and Clang, DWARF 2 and 5, little and big-endian).

## 2026-10-19 — A name pattern reads one way everywhere

Every command that filters or picks names by a typed pattern reads it
through `matching.matches`: a wildcard (`*`, `?`, `[`) makes a glob
anchored at both ends, plain text means contains, and case is ignored on
every platform. A filter that matches nothing answers with no rows and
succeeds; a command that acts on what it picks (deleting files) fails
when it finds nothing, as it does for a name that is not there. **Why:**
the 2026-10-15 review (V4, decision 1) found six matchers: glob only,
glob-else-exact, glob-or-substring, substring, an exact-glob-regex-substring
ladder and regex, each with its own wildcard test, and misses that failed
in some commands and succeeded in others. `fnmatch.fnmatch` follows the
OS, so `/env pa*` found PATH on Windows while `/env path` failed. Chuck
chose contains for plain text (`/dev.lib pic32` keeps working), an empty
success for a miss (a script or agent tests the value), and glob-only for
`/sym.search` (its regex mode goes). Text search over prose or output
(`/grep`, `/find`, `/search`, `/help <term>`), identity lookups (ports,
symbols) and prefix completion are not name filters. crcglot's own
`algorithms=` and `form=` globs are crcglot's. **Where:** registry
`name-one-matcher`; `termapy/matching.py`, `tests/test_matching.py`.

## 2026-10-20 — A system name comes from the code: typed constants, never macros

The firmware says what a peripheral is for in one place a build keeps: a
typed constant. `volatile PortGroup *const SPI_PORT = &PORT->Group[2];`
reaches the ELF with its name, its pointer type and its value, so
`/sym.import` reads the initializer of every `const` scalar (registry
`elf-values-const-only`) and, when a pointer to a struct holds exactly one
loaded block's base, the merged table names that block's registers under
the constant's name: `PORTC_DIR` is also `SPI_PORT_DIR`, with the
register's address, size, type and safety trio unchanged (registry
`alias-from-pointer-constant`). An alias lives in the merged view only: it
needs the build half and the device half, is rebuilt with them, and goes
away with the code that defined it. **Why:** Chuck wanted system names
(`SPI_PINS`, not `PORTC`) and wanted them to come from the code rather
than from a hand-written board file whose addresses are copied and drift.
Of the three places such a fact can live, a `#define` does not survive
preprocessing (reading it would need DWARF macro info pyelftools does not
parse, or a C parser, ruled out with the register-table design), an
X-macro table was deferred with `feat/regs-table`, and a typed constant is
what every `-g` build already carries. The firmware side is one line with
external linkage; a `static const` pointer is folded away and never
reaches the ELF. A pointer to one register is a value, not an alias:
naming the register after it would collide with the pointer object's own
name. A pin is a pair, port and bit, so pin-level names wait for `const`
tables of structs (PinDiag's own tables) once struct-member access lands.
**Where:** `symbols/converters/elf.py` (`_constant_value`,
`_pointee_of`), `devices.alias_blocks`, `symbols/session.py`;
`tests/test_symbols_elf.py::TestConstants`,
`tests/test_devices.py::TestAliasBlocks`,
`tests/test_sym_commands.py::TestBlockAliases`; the ELF fixtures rebuilt
from `tests/fixtures/elf/elf_sample.c` with the constants added.

## 2026-10-20 — The /mem commands follow the output level

Every `/mem.*` command reads at the session output level, so it is as loud
or as terse as the rest of the prompt. The value a command computes is the
answer and rides the result channel (quiet and up), so a script in quiet
mode still gets it: `/mem.read gTemp` is `gTemp = 27`, `/mem.dump.quiet` is a
bare byte grid. Normal adds the readable context -- the dump ASCII column
and the symbol label. Verbose adds the locators that repeat what you
already know: the dump address column, the `(word 0x.. at 0x..)` after a
field read or write, the hex twin after a scalar read, and the `(was ..)`
before-value after a write or mask. The dump `addr=`/`ascii=` flags default
off and coexist with the level by raising it: `shown = flag OR the level
says so`, so `addr=on` forces the address column even at quiet, and the
level can only add a column a flag left off, never remove one a flag set.
**Why:** Chuck asked for /mem to be consistent with termapy's verbose/quiet
dial, and for a way to dump bytes with no addresses and no strings. The
dial already existed (`/term.output`, `cmd.<level>`, `cmd --<level>`) and
gated whole channels; the work was re-tiering the existing lines onto it
and moving the dump grid from the output channel (normal+) to the result
channel so quiet keeps the bytes. No raw-args re-parse: the flags became
plain off-default bools and the level is read with `ctx.output_level`. The
address column moved off the normal default deliberately -- it is the most
redundant element when you know where you are -- which is a visible change
to a plain `/mem.dump`. **Where:** `builtins/commands/mem.py` (`_verbose`,
`_normal_or_louder`, the dump column gates); registry `mem-output-levels`;
`tests/test_mem_commands.py`; the gold exercises every tier at the default
level.

## 2026-10-27 — The device moves bytes; termapy checks the addresses

A device's MEM commands read and write whatever address they are given.
Knowing the part -- its memory map, its registers, which reads have effects
-- is termapy's job, from the JSON that describes it (the device file, the
memory regions), because a chip's whole map carried in the chip does not
scale to the small devices termapy is for. What a device may add is a lock
of its own: a guard function injected at initialization,
`guard(addr, count, write)`, that sees every access before it runs and
refuses it with `ERR range`. With none, every address goes through. A MEM
line is answered in the wire spec's text whatever the board's reply
framing, as the C module writes its text beside `termapy_json.c`, so
`/mem.*` reads a board the same way in the TUI and over MCP. **Why:**
Chuck, when the MicroPython MEM module was planned with a memory map per
board copied from the vendors' manuals: "the code on the termapy side
would be responsible for checking ... supporting all knowledge about the
chip, in the chip does not scale to smaller devices", with "global locks"
in the embedded code as an injected guard. **Where:**
`boards/micropython/firmware/termapy_mem.py` (`Memory`, `direct`), the
monitor's `native` commands in `termapy_json.py`; registry
`board-mem-pure`, `board-mem-native-text`. **Open:** `termapy_mem.c` still
takes a map (`TermapyMem_Region`, the Unreleased "reference MEM module takes
the platform's memory map"), which this replaces with the same guard: a
branch of its own. termapy does not yet refuse an address outside the
part's JSON, so until it does a mistyped `/mem.read` on a board with no
guard faults the chip; the ESP32-S3 resets.

## 2026-10-29 — The output pane selects and copies; the fix is carried until Textual has it

Textual selects text in a widget that stamps its rendered lines with
content offsets and extracts the text under a selection. `Log` does
both; `RichLog`, the output pane's base, does neither, so a drag in the
pane selected the whole widget invisibly and Ctrl+C copied an empty
string, while `help/toolbar.md` said that drag and Ctrl+C copies. The
pane is now `widgets.SelectableRichLog`, which adds the two hooks over
the rendered strips (the copy is the text as shown: wrapped lines, table
rules, no colors) and drops a selection the moment its line numbers stop
meaning what they did (a clear, or `max_lines` trimming the top). The
find view is one too. **Why:** the terminal-level alternative (hold
Shift, drag over the whole screen, borders and all) is the one thing
people new to termapy reliably dislike, and the help already promised
the better one. The same change, made in `RichLog` itself, goes to
Textual as a pull request under its conventions (snapshot test,
CHANGELOG, black); when termapy's floor passes the release that has it,
the override goes and `LinkLog` subclasses `RichLog` again. With the
focus, which a click gives it, Ctrl+A selects every line. The keyboard
selects on the same hooks, which Textual offers for no log widget:
Shift+Up from the prompt selects the newest line and moves the keys into
the pane, where Shift with an arrow, Home, End, PageUp or PageDown moves
the selection's end; Escape clears it and returns to the prompt without
stopping a script, and any typed character goes back to the prompt. While
anything is selected the pane stops following new output. Chuck: up/down
and left/right both, or neither. This part stays termapy's; the mouse
half is what Textual could take. The Textual floor moves from 0.80 to 2.0, the
release with the selection API; whether the code already needs a newer
one is a chore of its own.

## 2026-10-29 — A command says how settled it is: stable, preview or prototype

Every command has a maturity, one of `MATURITY_LEVELS` in
`plugins/command.py`: **stable** is covered by termapy's compatibility
promise; **preview** works but may change in any release, outside it;
**prototype** is new and incomplete, and may change or be removed. A
family declares it on its root `Command(maturity=...)`, every subcommand
inherits from the nearest ancestor that declares one, and any of them may
override; an undeclared root is stable. `/help` shows it as a STATUS line
in green, yellow or red, and every listed row shows its level in a column
after the command's name, stable included, so no row reads as unknown
(Chuck, on seeing an untagged `/profile` among tagged rows: "everything
should be at some level"); the MCP catalog carries it on each entry and gives
the server's own level; a prototype family prints one note a session the
first time it runs. It is a label, not a gate: everything still runs.
Preview now: the MCP server and `/mcp`, `/dev`, `/sym`, `/mem`. Prototype:
`/board`, `/firmware`. **Why:** Chuck, "MCP, device files and sym files are
still in development and may change while the rest of the code is ready
for public testing", "so we know the edges of sanity". The models are
Node.js's stability index (colored banners, a warning for experimental
features), PEP 411's provisional packages (outside the compatibility
promise; graduate, stay or go), Kubernetes feature stages (written
promotion criteria) and Rust's stability attributes (an unmarked item
inherits its parent's). **Graduation:** a family becomes stable by a dated
entry here. The MCP server graduates when every host-access limit
`help/mcp-server.md` states is enforced, the 2026-10-29 security review's
Tier 1 and 2a; the others when their commands and file formats stop
changing.

## 2026-10-30 — One did-you-mean: case ignored, edit distance, a reach that grows with the name

A name that names nothing is answered with the names it most likely
meant, picked by `matching.closest` and phrased by `matching.did_you_mean`
(registry `did-you-mean-one`). It replaced four homemade copies (a
hand-written edit distance in `repl.py` for commands and flags, a private
import of it in the config editor, `difflib` in the profile loader) worded
four ways, and reaches the places that had none: config keys and names,
scripts and data-folder files, symbols and registers, devices and library
parts. **Why:** Chuck mistyped `pic32cm51641e00100` (a one for an L) and
asked that the user "work less from memory"; the algorithm had only to be
"good enough for the cases we will throw at it", and speed was not the
test ("waiting .3 seconds when you make a mistake isn't that bad"). **The
evidence:** 1,300 generated typos of termapy's real names (301 commands,
63 config keys, a PIC32CM's 2517 registers, 12 part numbers; dropped,
doubled, swapped and wrong letters, one-for-L and zero-for-O, case, two at
once) and ten words that are typos of nothing. Ignoring case was worth
about fifteen points everywhere, since neither old algorithm did. With it,
edit distance put the name meant first 94-100% of the time. `difflib`
scored about the same overall but failed where registers live: it does not
count a swap of neighbors as one edit, and its ties among numbered
siblings (SERCOM0 to SERCOM5) ranked 5, 4, 3, so `SERCOM_2USART...` never
offered SERCOM2; it also answered `status` and `config` with register
lists. A flat two-edit limit answered junk for short words (`foo` became
`/log` and `/os`), so the reach scales with the length typed: one edit up
to 4 characters, two up to 10, three beyond. A candidate whose length
alone is out of reach is skipped before its distance is computed. No
algorithm can tell which digit of a numbered family was meant, which is
why three names come back. crcglot keeps ranking its own algorithm names
(its knowledge, by the library boundary rule); termapy only phrases them.
The same day it reached every file a command looks for and misses
(Chuck: "can we extend this to file names? That could be very
useful"): the folder the command searched is listed, only the last
part of the typed path is matched, and the suggestion keeps the rest
as typed, so a mistyped vendor SVD path offers the whole corrected
path. It lists a folder only where the command could already read:
every site guards its path first or is unreachable over MCP, and
`/profile.validate`, which reads its path unguarded, is left without one.

## 2026-10-30 — A config file is laid out in one order, grouped by order and not by nesting

The config file is termapy's settings UI: a developer edits the JSON with
the editor's per-line help and validation. So its layout is part of the
interface, and it had drifted: every key a migration or backfill added
was appended, and Chuck's oldest config had 46 of its 50 keys out of
place (`serial`, port and baud, was 37th). `DEFAULT_CFG`'s order is now
the layout, in groups by what you configure, the settings edited most
first and the long button list last: identity, port, connecting, line
text, display, device replies, device profile, files, protocol tests,
startup and frontends, buttons. Every write goes through
`config.write_config_file` (registry `cfg-written-in-order`): load's
migration write-back, `create_config` and the config editor's Save, which
reorders what was typed rather than keeping it. A key the defaults lack
comes last. The v32 step makes every existing file be rewritten once.
**Related settings are grouped by order, not nesting.** `eol`, `eol_rx`
and `eol_markers` sit together as flat keys rather than an `eol` dict:
v28 named them for their `/term.eol` commands, `/cfg` looks keys up only
at the top level and in `serial`, and the editor validates by key name,
so a dict would have cost a migration and changes in all three for what
adjacency gives free. This settles the "future grouping decision" the
comment above `serial` used to leave open; only the pyserial arguments
are nested.

## 2026-11-01 — A command declares the config keys it uses, with how; the help derives the rest

Five long helps said "persisted via cfg echo" or "persist with `/cfg
eol`" when every `/term.*` setter and `/cfg <key> <value>` are
session-only and only the config editor's Save writes the file. Fixing
the sentences by hand was the second-class fix (Chuck: "you remember to
update the help"): the next command would forget again. So a command's
config dependencies are data, `Command(cfg={"eol": "appended to each
sent line"})`, a key path (`defaults.cfg_key_paths`; a bare leaf
resolves) to a phrase for how this command uses the value, validated at
load like a synopsis. `/help <cmd>` renders them as a CONFIG section
(key, live value, phrase) and one generated line on where a change
lasts, naming the config file; `/help <key>` is a new page (meaning,
value, the file and the key's JSON spelling in it, the commands that
use it and how); `/search` indexes the keys; the editor's help line
names the users; the MCP catalog carries `cfg`. The false sentences
were deleted, not replaced. A phrase rather than a role vocabulary
(reads / sets / seeds), chosen by Chuck from three: the ask was "the
dependencies and how they are used", and a phrase says it where a role
says less; it is the `help=` pattern applied to a key. The guard holds a
plugin module's literal config reads, and those of the `_helper`
modules it imports, to its declarations (registry `cfg-declared`); that
a declared key is really touched is review (`cfg-declared-read`). Found
on the way and parked for their own branch: `echo`, `hex` and `line_no`
only seed session flags at startup, so `/cfg echo on` changes nothing
visible, and `encoding` and `eol_markers` are captured into the reader
at connect. The editor's panel grew a fourth line for "Used by", which
exposed a message that had been below the fold: a `$(CFG)` in a value
was reported "variable - not resolved" because the editor expanded only
`$(env.NAME)`. It now tells an unset environment variable, an unknown
name and a known session variable apart, asking `variables.resolve_one`,
the namespace's one owner, whether a name exists.

## 2026-11-01 — One reader of a config value, with the table's default

A config value with a default is read through `defaults.cfg_value(cfg,
"path")`. `cfg.get("key", literal)` is gone from every module but the
table itself (`defaults.py`), the frozen history (`migration.py`) and
three readers of a file as written in `config.py` (`validate_config`;
`load_config`, whose `config_version` must read 0 when missing, not the
current version; `config_summary`), each allowlisted with its reason.
137 sites restated a default in a literal and five restated it wrong
(`echo_fmt`, `cli_prompt`, `request_err_pattern`, `border_color`, the
`/` prefix in the MCP server), so the same key had two defaults
depending on who asked. The sweep was an `ast` rewrite driven by the
guard's own finder, so the rewritten set is exactly the flagged set. A
subscript (`cfg["eol"]`) stays allowed: it is loud when the key is
missing, which a loaded config never is. A container default comes back
as a copy, as `default_cfg` copies, so no caller holds the table's own
list. Registry `cfg-read-one-reader`. The first half of H1 in the
2026-11-01 config review; the schema table is the second.

## 2026-11-02 — A command declares that it reports state, and /help runs it

`Command(state=True)` says that running a command bare reports its current
state. `/help <cmd>` runs it and shows the answer in a CURRENT STATE section
right after STATUS: a short value in full, a record's rows, a list's rows, and
past twenty rows the start and a link to the rest. Chuck asked for it
because the pages that showed live state were the hand-written ones: of 27
bare-query settings, 8 showed their value. A hand-written state line is the
second-class solution the `cfg=` declaration replaced, with a second defect:
the page and the command can disagree (`/help port.mode` built its own frame
text, and `/help port.dtr` said `?` with the port closed).

The word is `state`, not `status`, because STATUS is already the page's
maturity section. It is not `shows`: `/term.echo` reports state bare and acts
with an argument, so a click on it must not run it, and `shows` includes
viewer-openers. It is a plain boolean with no config key to turn it off; the
renderer handles size, so a declaration has nothing to tune.

The read is the ordinary dispatch path at the silent level, so the capability
gate still answers and `Not connected.` is shown as the state. The promise is
behavioral, so its guard is too: every tagged command is read twice against a
connected DEMO and the session, the files and the wire are compared (registry
`state-read-immutable`). A query in name is not always a query in fact, and
these stay untagged with their reason in `STATE_EXEMPT`: `/port.info` and
`/port.chip` open the port on Windows while disconnected to test whether it is
in use, `/mem.info` sends `MEM.INFO`, `/cfg.info` writes its report, and
`/app.ver.info` calls PyPI.

The hand-written state lines on the twelve port settings and signal lines are
deleted, their prose kept: the value now appears once, and it is what the
command answers. The green line's label also named a signal's direction
(`DTR (out)`); `Show or set` and `read-only` in each command's help say the same.
Summaries on a family's page (`/help port`, `/help cfg`, `/help sym`) stay
hand-written, because no single command gives them as one line.

## 2026-11-02 — The config schema is one table, and one validator answers for all three surfaces

Every config key is one `CfgKey` row in `defaults.CFG_KEYS`: its default, its
kind, what bounds it, and its help. `DEFAULT_CFG`, `CFG_HELP`, `CFG_CHOICES` and
`MANAGED_CFG_KEYS` are views of the table under their old names, so no consumer
moved. `config.check_cfg_value` reads the kinds, and a load, the config editor
and `/cfg` all ask it. The 2026-11-01 review's H1 and H2: the schema lived in five
places, "is this value OK" had three answers, and validation never refused. The
editor's baud list lacked 110, 600, 14400 and 28800, which a load accepted;
`/cfg.auto max_lines 0`, `encoding nope` and `config_version 99` all succeeded.

A new key is one row, in its group, because the table's order is the layout of a
config file. Kinds are a closed list with one check each, and a test holds every
row to it. There is no setter field: who sets a key is the `cfg=` declarations'
job. `/cfg` now refuses what the check refuses (`Invalid max_lines: 0 (must be a
positive integer)`) and `config_version` by name. A non-standard baud rate stays
allowed, since some hardware needs one: it is a finding with `refuse=False`, so a
load warns, the editor shows yellow and `/cfg` takes it with a note. The editor
keeps the two things only a live editor can say, whether a port is plugged in and
that a rate is custom.

The messages are now one vocabulary (`must be true or false`), replacing
`expected bool, got str` in load warnings and `must be positive` in the editor,
which it showed for a key that may be zero. Refusing also closed a gap: `/cfg` set
a path key without `/cfg.auto`'s sandbox check, and `file_xfer_root` was not on the
list at all. `port_control.PORT_PROPS` stays as the pyserial attribute map, held to
the table by a test.

## 2026-11-02 — A status says what it means

`stable` reads "tested and part of the standard release" and `preview` "works
and is used daily, but may change in any release". Both used to lean on
"termapy's compatibility promise", in the words the 2026-10-29 entry chose, and
no document ever defined one, so the phrase claimed something that did not exist.
Chuck's definition: stable means tested and part of the standard release.

## 2026-11-02 — A config load ends where a fresh start on that config would

Chuck: "It is very important that the ENTIRE state of the app is controlled ...
so the cfg always loads to a known state (if the user changes settings then
that is on them)." Measured by starting on config B in one process and arriving
at B from a used A in another, the config dict always matched and the session
did not. The TUI kept eight things, the CLI two, and a wider probe that flipped
every setting by its declaration found three more: `/env.set`, a running
`/run.record` and an open find bar. The cause was two paths: the CLI and MCP
rebuild the context on a load, the TUI reset a hand-kept subset, and neither
knew about module-level state.

There is now one reset, `TerminalHost._reset_session`, and a guard that compares
each frontend with a fresh start. Chuck's answers settled the choices it needed:

- **User variables reset.** So do `/env.set` values, `{seq}` counters, the device
  profile and `/find.max_count`. A capture and a recording stop.
- **The port follows `auto_connect`.** The TUI used to reconnect any port that was
  connected, whatever the new config said.
- **`color` and `output_level` are config keys** (v36), so a file can set every
  session flag. `echo_repl` stays keyless: its right default is per-host.
- **Startup flags hit after the load.** `--no-color` and `--quiet` outlast every
  load, as do a host's rules (MCP's JSON stays uncolored, quiet and without echo).

Two calls were made while building it, and either is one keyword to reverse. An
editor Save counts as a load, so the session resets, but a connected port stays
connected: a saved edit is not a request to drop the link. And a port change from
the title bar or picker is not a load at all, since the config did not change; it
used to run the whole switch, which would now have wiped the session.

Session state goes in `ctx.ns()`, which this reset empties. The first build reset
the `/find` globals from an `on_config_load` hook, the smaller change, and its guard
accepted a hook or a namespace alike. Chuck asked why state `ns` exists for was not
in it, and there was no reason. `/find` moved, and a hook no longer satisfies the
guard: module state needs an entry in `MODULE_STATE_OUTSIDE_NS` saying why. The
`/run.record` file and the replay guard stay outside on their merits, a cleared dict
cannot close a file and a replayed `/cfg.load` must not clear its own guard. The
`/env.set` values and the user variables followed once transforms and `expand_vars`
took a `ctx`; launch and context variables stay module-level as the process's, and
`SESSION_*` as the running script's, which outlasts a `/cfg.load` inside it.

A key that starts a flag is marked `session_flag`, and `/cfg` moves the flag with
the key. It used to change only the dict: `/cfg.auto echo false` answered `False`
while `/term.echo` went on answering `on`.

A script is not a load. `/run`, the Scripts button and the Run menu start one the
same way and reset nothing (registry `script-start-one-way`): it sees the session
it was launched into and leaves its changes behind. One that needs a known start
says so with `/cfg.load` as its first line, and `termapy --run` gets one anyway,
being a fresh process.

Restoring state when a script ends was considered and rejected. It protects what
runs after a script but not the script itself, which was the concern, and it
needed exceptions for serial settings and setup scripts. The picker used to clear
user variables and freeze `$(SESSION_*)` where `/run` did neither, so one script
behaved two ways depending on how it was launched. Now the picker clears nothing
and every outermost script freezes the moment.

## 2026-11-03 — The words commands share explain themselves

Chuck had forgotten how `cycle` works: only the plugin author's page said, and
`/help cycle` was an error. `--json` and `--quiet` work on every command, and no
page said that either. Tooltips were the obvious fix, but they exist only in the
TUI, so the CLI and an agent would have got nothing. Pages are the foundation and
tooltips a surface on them: `idioms.IDIOMS` is one table, `/help cycle` and
`/help.idioms` read it, and a synopsis word links to its page by being in the
synopsis, with nothing for a command to declare.

A page lists the commands that take its word, generated from the registry. Chuck
thought `cycle` was on every enumeration; it is on four. Closing that gap is its
own branch, and the page stays true either way.

A hidden command now gives way to a config key or an idiom: `/help color` used to
open the legacy alias's page rather than the key's, and `/help verbose` would have
done the same to the idiom.

A tooltip gives the answer rather than the address. Chuck, looking at a config
key's link that said only "Click to run: /help output_level": "the tool tip saves
the trip". Every definition was already written once (`help=`, `CfgKey.help`,
`Idiom.summary`), so a link looks up whatever it names at hover, in `/help`'s lookup
order (registry `link-explains-itself`). No link carries its own text, so none can
disagree with the page it opens, and a plugin's command is covered by its `help=`.

A help page shows what is about this command (registry `help-page-specific`). Chuck:
"I hate seeing a command that toggles a mode have a full page of help." `/help
term.hex` was 32 lines, and 9 of them were the same on nearly every page: a `stable`
STATUS, an all-yes AVAILABLE, the sibling list, and where a config change lasts. Those
now appear only when they are news. `--verbose` is the existing way to ask for more, so
it prints them in every frontend, and in the TUI they are a hover away, on the
command's name or the config key.

A row no longer prints its level. Chuck: the column put the status "a bit too much in
people's faces", and most commands are the tested, released system. A name not yet
stable is italic with its level in the tooltip, and stable goes unmarked, which reverses
his own 2026-10-29 choice of a column on every row. A row in a plain terminal shows no
mark; there the page's STATUS, `/help.group`'s LEVEL column and the MCP catalog carry it
in text.

A tooltip has a shape, because the name's was a wall of same-weight lines. The
definition leads in bold, the click line is last and dim, and blank lines part them.
`Link.tip` takes pieces as well as a string, so the level and the flag names can carry
the colors they have on the page; a piece's text is literal, so no markup reaches a
parser. Chuck chose this over plain text knowing it widened a published type.

## 2026-11-03 — Under MCP termapy is conservative, and no flag widens it

An MCP client never reaches a shell (registry `os-never-under-mcp`), never renames or
deletes a file (`mcp-no-file-management`), and never writes into `plugin/` or `viz/` or
over a file that exists (`mcp-no-code-writes`). Chuck: "/os should NOT be allowed under
MCP, that is an escape to freedom", and "most things with rename and delete should be
off limits". Agents lose nothing they cannot do without: captures pile up until a
person removes them, and a new capture takes a new name.

`confirm=true` stopped being the control for these. A client sets it itself, and the
gate behind it reads only the outermost line, so `/repeat count=1 cmd=/plugin.delete x`
deleted with no confirm at all. A capability is checked in dispatch, and a probe of
every nesting form found it holds. It stays the control for `/mem.write` and a
profile's destructive device commands, until that gate moves into dispatch (Tier 2a of
the 2026-10-29 security review).

No new capability, and no opt-in flag. `interactive` already means a person at a
terminal, and already refused `/os` and `/log.delete`; a test fails if MCP ever gains
it, so granting it for approval prompts would have to be decided rather than happen.
Chuck on the flag: "I don't want to give mcp access through a flag."

These are limits on termapy's own commands, and they are not containment. A client
that can write files some other way needs only to learn the folder names, and Chuck
was plain about that: "that isn't my problem". The MCP client's own permissions remain
the real boundary, as `help/mcp-server.md` says.

## 2026-11-03 — An MCP server finds its project's config, and may switch within it

Amends 2026-05-03, "One MCP server per config with a profile". Its reasons stand for a
device: one process owns one port, and two devices at once are two servers. But
`/cfg.load` has worked under MCP since 2026-05-10, a week after that entry, while the help
still said "No hot-swap". So `termapy --mcp` with no config worked by luck: the working
folder happened to be the workspace, which Claude Code's docs do not promise, and an agent
ran `/cfg.load m3_bin`.

Chuck's rules (registry `mcp-finds-its-config`): from `CLAUDE_PROJECT_DIR`, the project
root Claude Code sets for a server so it need not depend on its working folder, or else
that folder, use the first of `./termapy_cfg`, `./src/termapy_cfg`, `./source/termapy_cfg`.
Start on a config named at launch, else the only config, else the only `mcp_*`; with
several `mcp_*`, the first whose port opens. "It should continue on", so a second server
moves to `mcp_cfg2` when `mcp_cfg1` is busy. Otherwise start with none and tell the agent
what it can load: "you have a path to be explicit and you have a fall through."

"The highest priority is the command line, everything else is hierarchical convenience."
So a config named at launch is the only choice: if it does not exist the server exits with
an error, as `--cfg-dir` naming a missing folder does, instead of an agent carrying on
with another device's config. A config named by its path brings its folder. And an agent
cannot move a server off a config the command line named: `termapy-left` would otherwise
drive the right board under a name that no longer says so. Only a config the rules chose,
or none, can be left, which keeps the no-lockout case. That is also Chuck's daily setup:
`termapy-a` and `termapy-b` are two identical entries naming no config, "so I can debug
two ports at the same time", and the agent loads a device into each.

Unless the launch named a config, an agent may load any in that folder by bare name,
because "those are related to your project". Pinning every server to its first config was
considered and dropped: with several devices, "you don't want to be locked out". A `.mcp`
manifest was rejected
because a project's `.mcp.json` already names a config, and an `mcp-enable` config key
because, with loading open, it would only pick a default. A "never for agents" key is the
one use that would earn it; not until a device needs it. There is no fallback to the OS
config folder: a user-scope server enabled in an unrelated project must not see the
user's other configs.

The chain needed `exclusive=True` on the open (registry `port-open-exclusive`). Without it
only Windows refuses a second open, and on Linux or macOS a second server would share the
first device and split its bytes. The chain loads each config before trying its port
rather than trial-opening first, because an open and close can reset a board through DTR.

## 2026-11-04 — A raw traffic log is pyserial's `spy://`, documented and not wrapped

Chuck: "document it as a 'feature' and just allow pyserial to do its thing." termapy passes
a port string to `serial_for_url`, so `spy://COM3?file=traffic.txt` already logs every byte
in both directions, and `help/ports.md` says how to use it and what to expect. No code.

Three ways to wrap it were considered and dropped, because each traded a free feature for
code of our own: recording the bytes in `SerialEngine`, a `/cap.serial` command that reopens
the port (a reset on an auto-reset board, at both start and stop), and a `spy_file` config
key. So pyserial's limits are ours, stated rather than fixed: every connect starts the file
over, a serial number is not resolved inside the URL, and `raw` fails on a file.

The one thing termapy does change is where the file goes (registry `spy-log-in-cap`). Left
alone, `file=traffic.txt` landed in the working folder, usually a project's root, against
`writes-in-cfg-folder`. Chuck: "I would like for the user to ONLY provide a relative path and
the file goes in the cap folder." So `config.open_serial` moves `file=` under the config's
`cap/`, and refuses a full path or a `..`, in every frontend and under no flag.

Under MCP that keeps the URL away from `plugin/`, `viz/` and the rest of the disk. Inside
`cap/` a log may replace a file, and an agent may do that too: Chuck, "It is ok to overwrite
files and send to a comport." So a `spy://` log is the one user-named exception to "a write
never replaces a file" (registry `mcp-no-code-writes`). For the same reason there is no check
on device names: `file=COM4` is a relative path like any other. On the Windows 11 bench it
makes an ordinary file; other versions were not tried.

## 2026-11-04 — An agent may load any config in the project, however the server started

Amends 2026-11-03, "An MCP server finds its project's config", which refused to move a
server off a config the command line named. Chuck: "It is better functionality to have the
file loadable by the agent. Take out whatever mechanism we have in place to lock out changes
under mcp." An agent may want the config a user just edited, or a different one, and the lock
never held the device anyway: `/port` could move a named server to another port. So
`config_fixed` is gone, and naming a config at launch picks where a server starts.

The command line still comes first at launch: a named config that does not exist stops the
server rather than letting it start on another. `termapy-left` now means the left device only
until an agent loads another config or moves its port, and the help says so. No port lock was
added either, for the same reason.

What did not move: `/cfg.load` takes a bare name from the project's folder and nothing else,
because a path or a stray file would be rewritten as a config and its folder's plugins run.
Renaming and deleting a config still need a person.

## 2026-11-05 — Under MCP a write may replace a file inside the config's folder

Reverses the "over a file that exists" half of the 2026-11-03 entry, and with it the 2026-11-04
entry's treatment of a `spy://` log as an exception. Chuck: "mcp should be able to write json,
cfg, run etc to its own folders. My concern is writing outside of folders", then "Yes mcp can
change files". The refusal was the assistant's extension of 2026-11-03, whose quotes cover `/os`,
rename and delete; "overwriting destroys a file as a delete does" was its reasoning, not his. It
stopped work an agent needs: re-running a script with a fixed `json_file`, updating its saved
profile, `/run.legacy --fix`.

`ctx.fs.guard_replace` now refuses only a file outside the active config's folder, or any file
with no config loaded, and no flag lifts it (registry `mcp-no-code-writes`). The cost is accepted:
an agent can wipe a capture by reusing its name, though it still cannot delete one.

What did not move: `/os`, rename and delete, and writing into `plugin/` or `viz/`, whose Python
runs at the next load. `/cfg.new` over an existing config still needs a person's yes, and
`/firmware.copy`, `/board.firmware` and `/run.record` never overwrite for anyone.

## 2026-11-05 — termapy keeps a diagnostic log, one file per process

Chuck asked for a review of the glue (exceptions, logging, utilities, threading), which "just
does whatever it needs to without a lot of thoughtful consideration." The census found about
forty broad catches with three markers for a reviewed one (`BoundaryException`, a `# noqa:
BLE001` that ruff does not check, none), no Python `logging` outside the vendored tree, and no
traceback kept anywhere: dispatch reported a handler's `KeyError('port')` as `Plugin error (x):
'port'`, and `show_traceback`, whose help promised stack traces, was read by nothing.

**What it records.** Lifecycle and failures, not the transcript: the process (pid, Python,
platform, command line), each frontend and each switch between TUI, CLI and vt100 ("a major
thing", Chuck), each config load and every file it brought in (the load report's rows, so the
folder plugins a session runs are named), each on-connect command with the config key it came
from (so a script it starts reads as the config's), ports opening and closing, the reader
stopping, script and proto runs, and every broad catch's traceback beside the command that
raised. The TUI's `$(FRONT_END)` became `tui` (it was `textual`), so the log, the mode loop
and the variable name the frontends `tui`, `cli` and `mcp` alike; `/mem`'s audit line had
kept its own map from the old name. The session log stays the record of what was typed and what the device sent; copying
it would make two files that drift and would put device data in a file meant for bug reports.
Per-command tracing, if ever wanted, is an opt-in DEBUG level, not built.

**Where.** The config root's `log/` (his choice: where he already looks), else `log/` in the app
state folder, so a log never creates a `termapy_cfg/` for an MCP server or a quick session.
`TERMAPY_DIAG_DIR` overrides both, which is how the test suite keeps its logs out of the
checkout's own folder.

**One file per process, not one shared file.** Several termapy processes run at once (the TUI
and MCP servers on one project). Python's documentation says logging to one file from several
processes is not supported: on Windows an append is a seek and then a write, so two processes
overwrite each other's lines, and a file another process has open cannot be renamed, so
rotation fails. A file per process needs no lock and no platform code. A start prunes the folder
to the newest 100 and never deletes a file whose process is running; a file stops at 1 MB.
The count is high because every run makes a file, a `-e` one-shot and an MCP start included,
so twenty would let a script's loop push out the session worth reading. It is a constant, not
a setting: the app folder's `config.json` has no keys yet, and the first should come from a
need this number turned out not to meet. SQLite was considered and
declined: it would solve the sharing, but the log would stop being readable with an editor or by
an agent, and a live database in a synced folder (OneDrive) risks a corrupt copy.

**How it is called.** `diag_log.info`, `.warning` and `.exception`, never `logging` directly:
`logging` costs tens of milliseconds to import, so it loads at the first record and the
print-and-exit flags never pay for it (registry `flag-paths-light`; `TestPathsStayLight` now
checks it). The version is written before the first warning or error rather than at start,
because reading it costs ~50 ms a CLI one-shot would otherwise pay on every run. The handler is
on the `termapy` logger and the vendored libraries', never the root, so the MCP SDK's own
`basicConfig` behaves as before.

**The rule.** Every broad catch records its exception or re-raises (registry
`broad-catch-logged`, an `ast` guard), and the line the user sees names the type and the log
file. The guard checks what the body does rather than how the catch is spelled, so
`BoundaryException` stays a published name but is no longer the only mark of a reviewed catch.
Catches that only guessed at what could raise were narrowed instead (Rich's `ColorParseError`,
Textual's `OptionDoesNotExist`, pyserial's modem-line reads, `parse_format_spec`'s
`ValueError`), and a `try` around code that cannot fail was removed. `show_traceback` is removed
(config v37): the traceback is always in the log.

## 2026-11-05 — Every frontend's session log is the same kind of file

Chuck: "there is an mcp log that appears to be an irregular log file, why wouldn't this just be
another log file". It was separate for one good reason, a TUI and an MCP server can run on one
config at once and a file two processes append to is unsafe on Windows, and irregular for no
good one: `mcp/session.log` in a folder of its own, no timestamps at all, its own line markers,
a second writer in `mcp/server.py` that reopened the file per write, and its own command family
(`/mcp.log`, `.dump`, `.path`).

Now one writer, `session_log.SessionLog`, owned by `TerminalHost` and used by the TUI and MCP
alike: every line `[stamp] <marker> <text>`, with `>` sent, `<` received, `#` termapy's output,
`$` an MCP request and `!` a failure MCP recorded, and every session framed by Session Start and
Session End. The files stay two, named alike beside the config: `<cfg>.log` (or `log_file`) and
`<cfg>.mcp.log`, a sidecar that a config rename carries. A load moves an old `mcp/session.log`
there; nothing else was ever written to `mcp/`, so an emptied folder goes too.

`/log.list`, `/log.show {name}` and `/log.dump {name} {N}` reach either log from any frontend
with a config, the CLI included, which is what `/mcp.log.dump` did for an SSH session. Behavior
removed: `/mcp.log`, `/mcp.log.dump` and `/mcp.log.path` (the path is in `/log.list`'s data).
Behavior changed: a word where `/log.dump`'s count goes is a log's name, so `/log.dump abc` says
`Unknown log: abc` rather than showing the usage. Two MCP servers on one config would still
share `<cfg>.mcp.log`; the two server slots normally run different configs, so that is noted
rather than designed around.

## 2026-11-06 — A typed baud rate is a standard one unless it is asked for as custom

Chuck: "the baud rate allows ANY number, that is high risk for typos". The New Config dialog
already guarded against it, a list of standard rates unless its custom box is checked, but
everywhere else a rate is typed took any whole number. `/port.baud_rate 11520`, `/port.mode`
and `/port.connect` set it silently, `termapy --new` and `/cfg.new` derived `custom_baud: true`
from it, and `/cfg` and the config editor only warned. The 2026-11-02 schema entry had made a
non-standard rate deliberately "unusual, not wrong", since some hardware needs one.

Now one rule, `config.check_baud`: a rate on `STANDARD_BAUD_RATES`, or any from 300 up when
asked for as custom. A config asks with `custom_baud`; a command that sets a rate asks with
`custom=true` (`/port.baud_rate 9460 custom=true`, also on `/port.mode`, `/port.connect`,
`--new` and `/cfg.new`), read by one `parse_bool` reader, `port_control.take_custom`, absent
meaning false, which also sets the session's `custom_baud`. Without it the rate is refused
and the error names the remedy. `scripts/throughput.py` takes `--custom-baud` for the same
reason. The non-refusing finding that existed only for this (`CfgFinding.refuse`) is gone, so
the editor shows the rate red and `/cfg` refuses it; a load still only warns, as loads do.
Behavior removed: a non-standard rate without asking, and `new_config` guessing
`custom_baud` from the rate. None of the configs in use had one.
