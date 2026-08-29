# Changelog

## 0.75.0 (2026-08-28)

A serial-reliability release wrapped around three visible features: silent
RX data loss on Windows is fixed (measured, not theorized), the session has
one JSON dial that covers device and termapy commands alike, and every file
listing and picker now says how big a file is and how long ago it changed.
Also a USB bus tree, comparable port locations for FTDI adapters, and a
threading audit that closed seventeen findings.

### Ages and sizes everywhere

Every file listing answers "which one did I just make?" without being
asked. `/run.list`, `/cap.list`, `/ss.list`, `/proto.list`, `/plugin.list`
and `/run.profile.list` are newest-first and each line carries the file's
size and age:

```text
run/
  status_check.run       72 B  just now    --  Quick status check
  gps_demo.run          622 B  10 min ago  --  GPS/NMEA Demo -- Query simulated GPS receiver
```

The Run, Proto and Config pickers show the same columns (metadata dimmed,
newest first) plus a detail: the docstring summary for a script, and
`port @ baud` and the config's title for a config -- a title that only
repeats the config's name is shown blank. The dialogs widen to fit a
macOS-length port name. Button tooltips say `7 available, newest 10 min
ago`, and the live capture label counts in KB/MB with the completion line
adding wall time (`2.0 KB in 3.2s`).

Three display formatters own this -- `format_size`, `format_age`,
`format_duration` -- with `frist` (new runtime dependency) picking the
unit and doing the calendar math. **Display change:** durations of a
second or more now render to three significant figures and roll over at
clock boundaries: `1.5s` instead of `1.50s`, `2hr` instead of `7200.00s`;
sub-second output is unchanged.

Under `--json` (and over MCP) the listings return `{name, bytes, mtime,
age_s}` records, and MCP capture artifacts carry `mtime` / `age_s`, so an
agent can order captures by recency.

### One JSON dial for the whole session

Any command now accepts `--json` and answers with one envelope line in
place of prose:

```text
/port.list --json
{"cmd": "/port.list", "success": true, "error": "", "value": "COM3,COM4",
 "data": [{"device": "COM3", ...}], "output_lines": [], "elapsed_s": 0.02}
```

`/term.request on` is the session-wide form: with it on, bare device
commands render request/response envelopes and termapy commands render
result envelopes -- the device/termapy demarcation is invisible, the
session is simply in JSON mode. A command with no structured form still
answers as an envelope, with its rendered text captured into
`output_lines` rather than printed beside it. Errors arrive in the
`error` field, never as a second red line, and the envelope composes with
`--quiet` / `--silent`.

`CmdResult.data` is the structured channel behind this: `/port.list`,
`/port.usb`, `/var` and the folder listings return real JSON records;
`value` stays the scriptable scalar. **Breaking (MCP wire):** every
`run_command` response is the same nine keys -- profile-shaped and
`/term.request` replies moved from `value` to `data`, and the
self-describing unwrapped shape is gone. **Breaking (display):**
request-mode scrollback lines now carry `value` / `data` /
`output_lines` instead of `result`.

Over MCP, device ANSI is stripped by default (`/term.color on` restores
passthrough), so escape codes no longer leak into the JSON an agent
receives or corrupt `request_err_pattern` matching. Piping CLI output no
longer hard-wraps long lines at terminal width -- `termapy --cli -e
"/port.list --json" | jq` works.

### Serial reliability

- **Silent RX data loss fixed (Windows).** pyserial opens every port
  with a 4 KB driver buffer; a main-thread stall longer than
  `4096 / (baud / 10)` ms overflowed it and the driver discarded the
  excess with no error. Measured on a hardware loopback: 20.9% lost at
  921600 with a 60 ms stall, 21.6% at 115200 with a 500 ms stall -- both
  now 0.0%. The buffer is widened on open (`SERIAL_RX_BUFFER_BYTES`),
  and RX reaches the UI through a non-blocking handoff so the reader
  never waits on the main thread.
- `drain()` now purges the driver buffer too, so stale bytes no longer
  land in the next request/response reply.
- A reader thread only tears down its own port: a stale reader can no
  longer close the connection the user just opened.
- `wait_for_idle` keys off when the device last spoke rather than a
  buffer the reader keeps empty, so it no longer reports idle
  mid-response.
- `/cap.bin` byte targets above 4096 are reachable again (they could
  never be met; the capture ran until stopped).
- Teardown is a real thread join; `/delay` of a second or more no longer
  crashes in the TUI; `/repeat` no longer erases a stop the user just
  requested; one outermost script at a time is enforced in the engine
  for every frontend; an MCP timeout no longer stalls the event loop
  for the length of the abandoned command.

A hardware loopback suite (`pytest -m hardware`, jumpered adapter found
by serial number, skips cleanly when absent) now covers the defects that
`FakeSerial` and `loop://` are structurally unable to show.

### Ports and the USB bus

`termapy --usb` / `/port.usb` draws the whole USB tree with serial ports
marked (`--usb --json` for records) -- which hub an adapter is on, whether
a COM port is one function of a composite device. On Windows, FTDI ports
now show the same `1-8.3` bus-port chain as everything else instead of a
registry hub/port pair, multi-channel FTDI chips keep their channels
apart, and the interface number is its own `IF` column rather than a
suffix that changed the shape of LOCATION. The DRIVER / LOCATION columns
had been empty for FTDI devices since July's non-invasive probe change
swept the registry lookup in with the port-opening probe; they are back,
and the lookup opens nothing. `--watch` sizes its
port column for POSIX device names.

### Also

- Help pages gained a set of scripted, regenerable screenshots (pickers,
  editors, palette, capture, protocol debug, VT100 demo).
- Port discovery takes an injectable `source=` (then
  `TERMAPY_DEMO_FLEET`, then hardware); the `$(NAME)` variable engine
  moved from a plugin into core; core-to-plugin imports and the io-channel
  rule are enforced by `pytest` (AST, not grep).
- Release scripts: the USB vendor refresh is idempotent and captured tool
  output decodes as UTF-8 on Windows.

## 0.74.0 (2026-08-18)

Two new argument primitives that any command can use, multi-frame CRC
identification built on them, the device-profile format published as a
versioned spec with a forward-compatibility policy, and the MCP server moved
to the 2.x SDK. Release gates are now reproducible, and Linux CI is green
again after a month.

### Passing a value that contains a space

Termapy has never had quoting: every argument parser splits on whitespace, so
a value containing a space could not reach a single parameter. `$(*NAME)` --
star inside the parens -- closes that. Where `$(NAME)` is expanded *before*
the line is split into arguments (so its value is text, and spaces in it
become extra arguments), `$(*NAME)` resolves *after* the split, so it is
always **exactly one argument** whatever it holds -- spaces, newlines, or an
empty string.

```text
$(FRAME) = 01 03 00 00 00 0a c5 cd

/proto.crc.detect $(FRAME)     # splice: 8 arguments, so 8 frames
/proto.crc.detect $(*FRAME)    # deref:  1 argument,  so 1 frame
```

The rule to remember: **`$(NAME)` is text, `$(*NAME)` is one argument.** It
works in any command that declares typed parameters, in every argument slot
except a whole-line `rest` value. An undefined name is an error rather than a
silent pass-through, and a reference embedded in a larger token is rejected
instead of being treated as a literal.

`ParamSpec` also gained `variadic=True`, so a positional can bind a *list*
with per-element coercion and range checks -- `list[int]`, `list[enum]` and
friends work with no change to the coercion layer.

### CRC identification across several frames

crcglot 0.31 taught `detect` to take a frame list, and `/proto.crc.detect`
now surfaces it: `<frames>...` binds one frame per argument, while `frame=`
takes a single frame that may contain spaces, so a line pasted from a log
needs no variable round-trip. crcglot owns and enforces the rule that the two
forms are mutually exclusive.

This matters because a single frame is often ambiguous: a narrow CRC matches
by coincidence roughly once in 2^width, so an 8-byte frame carrying a crc8
can also fit `crc6-darc`, and `match=first` would report whichever came up.
Several frames intersect and eliminate the phantom. It composes with the
existing `width=` and `algorithms=` filters rather than replacing them --
`width=` is the cheaper fix when you know the width, several frames narrow it
when you don't.

`/proto.crc.reverse` accepts `$(*NAME)` for its packets too. It keeps parsing
its own arguments, because its documented `cmd=<trigger> count=N` form puts
keywords after a to-end-of-line value, which the declarative parser cannot
express.

**Note:** whitespace now separates frames for `/proto.crc.detect`, so the old
`detect 01 03 00 00 00 0a c5 cd` spelling means eight frames. Use
`frame=01 03 00 ...`, comma/colon separators inside a frame, or `$(*NAME)`.

### Device profiles: a published spec that survives its own evolution

`docs/profile-spec.md` is now the normative reference for the profile format,
and the format has an explicit policy for what happens when it evolves: a
profile written for a newer revision **degrades on an older host instead of
failing**. `profile_version` is the only hard gate. Unknown response formats
fall back to `text`; an unknown safety tier is gated like `destructive`
(fail-safe); an unknown type kind loads with a warning and refuses at dispatch
(fail-closed); `$`-prefixed and `x_`/`x-` keys are a blessed extension
namespace that never warns.

Leniency opens a real hole, so it ships with a typo defense: `saftey:
destructive` would have left a command **ungated**. Unknown-name warnings now
carry a did-you-mean suggestion, and `--validate-profile <path> --strict`
escalates warnings to exit 1 for CI and pre-commit hooks -- enforcement runs
where the author is, so deployed consumers never brick on a profile from a
newer producer.

Also: `response.format: "text"` returns the whole reply as one string, a
first-class replacement for the `(?s)(?P<text>.*)` catch-all that dominated
real profiles, plus descriptive unit metadata on typed args and responses.

### MCP server on the mcp 2.x SDK

mcp 2.0 removed `mcp.server.fastmcp` and replaced it with
`mcp.server.MCPServer`. The two majors share no server API, so the `[mcp]`
extra is now a floor (`mcp>=2,<3`) rather than a range: **an environment
pinned to mcp 1.x will no longer resolve.** The decorator surface termapy uses
is unchanged across the break, and the port was verified with a live
handshake against a real 2.x client over stdio.

### Terminal, ports, and scripts

The port picker offers pyserial's loopback (`loop://`) as a selectable entry,
and a config naming a URL or virtual port now connects directly on load
instead of only doing so for `DEMO`. The port table shows `?` rather than `-`
for genuinely unknown cells. REPL input focus and the bottom bar are restored
after a script finishes, and `echo_repl` defaults off inside scripts and is
restored on exit. The Help tooltip shows update status and credits crcglot.

### Reproducible release gates

`ruff` and `ty` are pinned exactly and the lint rule set is selected
explicitly, because both were floating: ruff 0.16 widened its implicit default
from 59 rules to 414, which would have redefined "zero" mid-release without
review. The gate also pins its *environment* (`uv sync --all-extras`) -- ty's
diagnostic count depends on what is installed, not just on the source, so the
two states contradicted each other.

Linux CI is green again after being red since 2026-07-15. The cause was a test
asserting `/cfg.dump` refuses an absolute path using `C:/Windows/win.ini` --
absolute on Windows, an ordinary relative name on POSIX, so the guard never
fired. CI runs ubuntu-latest exclusively, so it was invisible from a Windows
dev box.

Transitive advisories patched: aiohttp 3.14.3 and cryptography 50.0.0. Both
arrive via optional extras (`[web]` and `[mcp]`), so a plain install pulls
neither.

### Internal

`app.py`'s size ratchet is gone, replaced by guidance framed around
essential-versus-accidental complexity: a feature-rich TUI has irreducible UI
code, and relocating it to shrink a number scatters it without reducing it.
The honest extractions -- driven by testability rather than size -- landed:
`StatusBar` and a `widgets/` package, the command suggester, the help and
title tooltip formatters, and a testable `HistoryNavigator` for REPL history
browsing, each with tests.

Loop variables across the codebase follow `for singular in plural:` (1053
renames); `i`/`j`/`k`, `_`, tuple unpacking, and short whole words like `key`
are exempt, since the rule targets truncation rather than variety.

## 0.73.0 (2026-07-15)

A broad release focused on the terminal experience, a consistent
setting-command grammar, single-sourced command help, and hardening the
MCP host into a sandbox by default. Config schema advances to v29
(auto-migrated on load). It also folds in the 2026-07-14 code-quality
review.

### Terminal display and line endings

Receive-side line handling is now configurable: `/term.eol.rx`
(auto / cr / lf / crlf) mirrors TeraTerm's Receive-newline selector, with
`auto` treating CR, LF, and CRLF all as breaks so it works on any device.
Backspace and DEL in received output are interpreted rather than shown raw.
Device-command echo and REPL-command echo split into two independent flags
(`/term.echo` and `/term.echo_repl`), and a half-duplex device-echo
suppression mode strips a device's parroted command from its reply.
`/term.color` is a portable ANSI-color toggle that works in both the TUI and
CLI. The demo device gained `AT+EOL` to switch its own response line ending
so the receive-newline paths are exercised end-to-end.

### Setting commands: one predictable grammar

Every on/off or multi-value setting now behaves like `stty` and `git
config`: **a bare invocation queries (shows the value, never mutates)**;
`on`/`off` (or a value, for enums) sets; the explicit verb `toggle` (bool)
or `cycle` (enum) steps; and an unrecognized argument is a clear error
instead of a silent flip. This fixes commands like `/term.color 2` quietly
toggling. TUI buttons that flip a setting on click dispatch the explicit
`toggle`/`cycle` verb.

### Command help you can trust

Usage lines are now rendered from each command's registered declaration
(name + argument spec + active prefix), so a command's error message, its
`/help` synopsis, and a re-configured prefix can no longer disagree --
handlers `raise UsageError()` instead of hand-writing "Usage:" strings.
Argument synopses are validated at registration, so a malformed synopsis
fails at load instead of rendering wrong in `/help`. The curated REPL
command reference is guarded by a test that fails if it lists a command
that no longer exists.

### Config folder access

`/cfg.dump <name>` prints a file from the active config folder to the
terminal (JSON config when bare), resolved strictly relative to that
folder -- absolute paths and `..` are refused for every frontend. Config
keys that back `/term.*` settings were renamed to match their commands
(e.g. `line_endings` -> `eol_markers`), and the config reference table in
the docs is generated from the defaults so it can't drift.

### One history file per config

The command history is now a single plain-text file per config, shared by
the TUI, CLI, and MCP frontends, so history follows you across modes
instead of each frontend keeping its own incompatible file.

### MCP host: sandboxed by default

The MCP host is now confined by default -- host-wide filesystem access and
outbound network are off unless explicitly opted in via `TERMAPY_MCP_*`
environment flags. `/run`, the `.dump` readers, `/cfg.icon`, `$(env.*)`
expansion, and the CRC file oracle are all contained to the config
directory for a remote caller, while an operator at a local CLI/TUI keeps
full access. A short Security section and a flags table document the trust
model.

### Fixes

- The capture-complete status line printed twice in the CLI (and MCP);
  it now has a single owner and prints once.
- `/proto` boolean arguments accept the same tokens as every other command
  (`y`/`n`/`t`/`f`, not just a smaller set).
- Request-mode timeout reads from the cfg, and config identity is correct
  after `/cfg.load`.
- A `/port` config copy no longer fails on a proxied config.
- Assorted UI polish: banner label, fail-closed confirm on a thread-misuse
  path, docstring corrections.

### Under the hood

- Duplicated rendering/parsing collapsed into shared helpers (progress bar,
  log slicing, capture message, filename timestamp).
- Release tooling gates lint-suppression pragmas (each must carry a reason);
  line length raised to 100; `ty` clean on the latest release.
- Docs hygiene: exact counts live in one release-updated place with
  general-terms prose elsewhere, and spelling normalized to US.

## 0.72.0 (2026-07-07)

A large release: a new declarative command-parameter system, a `--vt100`
ANSI passthrough mode, a big CRC/crcglot expansion, a unified time layer,
and the remediation of the 2026-07-03 adversarial fitness review. Config
schema advances to v24 (auto-migrated on load).

### Declarative command parameters

Commands can now declare their arguments as typed parameter specs. The
dispatcher parses, coerces, and validates them, and synthesizes the usage
line, the help text, and the MCP tool schema from one source -- so a
command's terminal help, its error messages, and the schema an LLM sees
can no longer drift apart. Migrated: `/ping`, `/repeat`,
`/profile.validate`, `/cap.text`, `/cap.bin`, `/cap.wire`, `/cap.poll`,
`/cfg.auto`, `/grep`. Parameters compose with flags, subcommands,
level-suffixes, and a whole-line "positional rest" value. Commands with
genuinely irregular grammars (e.g. `/cap.struct`, `/profile.load`) stay
hand-rolled by design, each with a one-line note explaining why.

### --vt100 ANSI passthrough mode

A new `--vt100` mode passes raw ANSI/VT100 through to the terminal, for
devices that drive a full-screen UI. It ships with an interactive
`DEMO_VT100` demo device (a 3-screen widget tour) and a VS Code
key-capture hint; the TUI toggle is reversible.

### CRC / crcglot 0.11 -> 0.28

The CRC subsystem tracks crcglot from 0.11 to 0.28:

- The catalogue grew from ~62 to 100+ algorithms.
- Generated code now defaults to the fastest implementation per (language,
  width) instead of always bitwise; `--small` forces the compact bitwise
  form and `--fast` the table form.
- `/proto.crc.detect` -- name a known CRC from a single frame.
- `/proto.crc.reverse` -- recover an unknown/custom CRC's parameters from
  captured frames, listing every equivalent (init, xorout) labelling.
- `/proto.crc.verify` -- check a frame against a named algorithm.
- Live capture: `cmd=` on `/proto.crc.find` and `.reverse` sends a raw
  trigger to the device (line ending auto-appended, no quoting) and
  identifies the CRC in the response.
- `/proto.crc.<lang>` gained doc-comment styles (`style=`), multi-algorithm
  file bundling, and `naming=` control; the codegen roster covers eight
  languages (C, C#, Go, Python, Rust, TypeScript, Verilog, VHDL).
- Unknown-algorithm errors now suggest close matches ("did you mean ...").
- A demo `/proto.crc.*` tour (an AT+RND emitter) walks the whole flow.

The crcglot floor is now `>=0.28.0`.

### Time and variables

- One duration parser and one display formatter (`format_duration`) back
  every human-readable elapsed/delay/timeout value, so durations render
  consistently everywhere.
- `$()` datetime variables take an optional `strftime` format after a
  colon: `$(DATETIME:%Y%m%d_%H%M%S)` yields a filename-safe, colon-free
  stamp -- on the dynamic clock and the frozen `$(LAUNCH_*)` /
  `$(SESSION_*)` moments alike.
- The `{}` scripting-template system is now cleanly scoped to per-run
  stamps: sequence counters (`{seqN}`), `{starttime}`, and the new
  `{elapsed}`. The ambient `{datetime}` placeholder was retired in favour
  of `$(DATETIME:%Y%m%d_%H%M%S)`; existing scripts keep working (the `$()`
  expander rewrites it transparently on both REPL and device lines) and
  existing configs are migrated on load by config schema v24. The proto
  results filename template keeps its own `{datetime}` -- a separate Python
  `str.format` placeholder, unrelated to the `{}` system.
- **Sequence-counter reset direction corrected (behavior change).** `{seqN+}`
  now resets the *deeper* (higher-numbered) counters, so `seq1` is the top
  level and hierarchical numbering reads left-to-right: `{seq1+}.{seq2+}` ->
  `1.1`, and bumping `{seq1+}` restarts the step counter. This fixes a
  long-standing inversion (present since the first commit) where the code
  reset the *lower*-numbered counters, contradicting `/help seq` and standard
  outline numbering. Scripts that relied on the old direction must swap their
  counter digits.

### Reliability (2026-07-03 review remediation)

- Port resolution and enumeration are non-invasive -- listing or resolving
  a port no longer opens it.
- `/port.connect` reports a failure when the port can't open (previously it
  could report success); it accepts 8N1-style mode order and a `port=`
  identifier. `/port.chip` resolves serial-number and fallback specs.
- TUI command dispatch is serialized; capture aborts on write failure
  instead of silently truncating, and capture start/stop runs on the main
  thread.
- Config defaults are deep-copied on backfill (no aliasing of the shared
  default dict); `parse_hex` rejects malformed input instead of scavenging
  digits; auto-reconnect is guarded against double-spawn; an env-to-wire
  gate keeps `$(env.X)` values from reaching a device implicitly.
- CI runs the MCP test suite and the Linux platform tests; main is green.

### Docs

- New MCP Server and VT100 help pages.
- The params-vs-hand-rolled decision rule is documented, and the
  adversarial review series is tracked under `docs/review/`.

## 0.71.1 (2026-06-03)

Maintenance release: absorb the crcglot 0.11 upgrade.

### crcglot 0.11

`AlgorithmInfo` gained a required `source` field that records the
provenance of each algorithm's Rocksoft/Williams parameters
(`"reveng"` for catalogue entries, free-form citation otherwise).
termapy's `--custom` path in `/proto.crc.<lang>` builds
`AlgorithmInfo` by hand from user-supplied parameters; it now passes
`source="user"`, which is exactly what the user-supplied case
warrants.  The constraint floor moves to `crcglot>=0.11.0`.

No user-visible behavior change in either the catalogue or
`--custom` code paths.

0.11 also ships an optional `crcglot[mcp]` MCP server -- worth
mentioning here because it foreshadows the question of how
`termapy[mcp]` and `crcglot[mcp]` should compose for embedded
LLM-assisted workflows.  Not addressed in this release.

## 0.71.0 (2026-06-02)

This release continues 0.70's "thin shuttle into crcglot" arc: crcglot
bumps to 0.10, termapy adapts to its tightened API, and the
long-standing CRC byte-order wart finally gets fixed.  Along the way
the byte-dump display learned the standard hex-dump dot-sidebar style,
`/proto.send` gained `--dry-run` and `--ascii`, and `/proto.crc.find`
adopted a clean answer/context/hints output model.  Plus a quiet but
consequential serial-display fix for multi-byte UTF-8 characters split
across reads.

### CRC byte-order default fixed

The hardest-earned fix here.  Before 0.71 the protocol parser used a
fixed little-endian default for CRC fields with no `_le` / `_be`
suffix -- right for Modbus-family algorithms (`refout=True`) and
*silently wrong* for the XMODEM / CCITT-FALSE / IBM-3740 family
(`refout=False`).  A spec like `CRC:crc16-xmodem` produced
little-endian bytes when the wire expected big-endian.

Now the no-suffix default derives from the algorithm's `refout` --
the right answer for the ~95% of protocols that serialize CRC bytes
in the algorithm's natural direction.  `_le` and `_be` remain as
explicit absolute overrides for the rare protocol that wires a CRC
opposite to its natural order.

Migration: explicit `_le` / `_be` keep working unchanged; specs that
relied on the implicit LE default with a `refout=False` algorithm
will silently switch to BE -- add explicit `_le` to those to preserve
old behavior.  Demo `.pro` files all used explicit `_le` and remain
correct.

### crcglot 0.10

The underlying upgrade brought two breaking changes:

- **Codegen generators** dropped `table=bool` in favor of
  `variant=Literal["bitwise","table","slice8"]`.  All
  `/proto.crc.<lang>` paths updated.
- **Short aliases removed** from the catalogue: `crc16m`, `crc16x`,
  `crc16i`, etc.  The reveng canonical names (`crc16-modbus`,
  `crc16-xmodem`, ...) are the only ones now.  Update any `.pro`
  files or scripts that used the short forms.

### `/proto.send --dry-run` and `--ascii`

Two new flags on `/proto.send`:

- **`--dry-run`** parses, computes the CRC, assembles the frame,
  prints what would have been sent, and returns -- no serial I/O.
  Works without a connected device, useful for verifying CRC byte
  order or scripted sends.
- **`--ascii`** renders TX / RX (and dry-run output) as a quoted
  escape-string (`"AT\r\n"`) instead of hex, for protocols where the
  payload is ASCII text.

### `/proto.crc.find` rebuilt onto `crcglot.detect`

The custom matching logic (~100 lines) is replaced by a thin shuttle
into `crcglot.detect`.  Same `bin=` / `asc=` modes, same behavior,
less code in termapy.  An in-development `cmd=` mode was dropped
after evaluation: constructing a valid request to send a strict
device already requires knowing the CRC algorithm, so the live-send
workflow had a chicken/egg problem; `bin=<captured-hex>` is the
universally applicable path.

The output was also restructured on a deeper principle: a command has
an **answer** and **supporting text**, and the verbosity dial decides
which channel surfaces what.  For find:

- `result()` (quiet+) -- the match line(s), or "No matches found."
- `output()` (normal+) -- the explanation for the 0-case
- `status()` (verbose only) -- the "Generate source: ..." codegen
  pointer and the multi-match disambiguation advice

The old `1 match:` / `N matches:` header was dropped as noise -- the
count is apparent from how many match lines follow.

### Byte-dump display: hex + ASCII sidebar by default

Short binary payloads previously rendered as a confusing dual hex +
"smart text" view: bytes like `00 00 00 0A` appeared as both
`00 00 00 0A` and `"\0\0\0\n"` on the same line, where the right half
could be misread as additional data.  Now short payloads use the
standard hex-dump dot-sidebar that the long form already did:

```text
TX: 01 03 00 00 00 0A  |......|
RX: 01 41 42 43 00 0D  |.ABC..|
```

You see embedded ASCII at a glance without parsing escapes.  `--ascii`
on `/proto.send` opts into the quoted-escape view for genuinely-text
payloads.

### Serial UTF-8 split fix

`SerialReader` now uses a stateful incremental decoder, so a
multi-byte UTF-8 character split across two serial reads no longer
becomes two U+FFFD replacement chars on each side of the split.  The
issue mostly bit at low baud rates with emoji, box-drawing glyphs, or
accented characters.  Tests cover split-at-line-boundary, 4-byte
emoji split mid-line, no-replacement-char assertion, and
truncated-tail-on-idle.  Caught by an external review.

### `/cfg` and template-var v22 fix

`/cfg baud_rate 9600` (and other `cfg["serial"]` keys) no longer
crashes the TUI confirm modal with a KeyError.  The hook fell through
to a flat `self.cfg[key]` lookup that didn't account for v22's nested
layout; it now reads from the serial sub-dict.  Same fix applied to
`CFG.PORT` / `CFG.BAUD` template variables that were silently empty
post-v22.

### Internal: `ctx.engine` → `ctx.internal`

The privileged escape-hatch handle on `PluginContext` was renamed
from `EngineHandle` (`ctx.engine`) to `InternalHandle`
(`ctx.internal`).  The old name read like a peer of the four clean
capability domains (`ctx.io` / `ctx.serial` / `ctx.fs` / `ctx.ui`)
and named only half its job; the new name describes its actual
invariant -- everything on it is internal / unstable / not part of
the external plugin API.

External plugins shouldn't have been using this handle, but if you
did, the rename is mechanical.  The capability-domain handles are
unaffected.

While the rename was in flight, several members moved to better
homes:

- `ctx.internal.prefix` → `ctx.prefix` (live property derived from
  cfg, no manual resync needed).
- `ctx.internal.coerce_type` → `scripting.coerce_to_type`.
- `ctx.internal.connect` / `disconnect` / `update_port` /
  `apply_port_effects` / `rx_queue` → `ctx.serial.*`.

What remains on `ctx.internal` is now genuine escape-hatch territory
(TUI-only screens, engine forwarders) and documented as such.

### Command-tree organization

- **`/log` parent**: `/log.dump`, `/log.fingerprint`, `/log.show`
  share a `/log` parent and a single source file.
- **`/ver` → `/app.ver`**: the version command moved under `/app`; a
  legacy `/ver` forwarder remains for one release cycle.
- **`/term.eol <token>`**: set the sent line ending via named tokens
  (`cr`, `lf`, `crlf`, `none`).
- **Legacy command aliases centralized**: per-file forwarder plugins
  are gone; aliases live in one `legacy.py` table that `ReplEngine`
  registers at startup.

### Documentation

ARCHITECTURE.md gained a **Core Concepts** map, an explanation of
the five `ctx` layers, an MCP frontend section, and a worked
seq-plugin example showing state + lifecycle hooks together.  The
"SPI" jargon was dropped.  The protocol-testing help doc records the
new natural-wire-order CRC default.

### Improvements and security

- **Signed line-count selector** on `/ss.txt [N]` (and unified with
  `/log.dump` / `/mcp.log.dump`): positive `N` = last N lines (matches
  terminal/tail convention), negative `N` = first |N| lines, no arg =
  everything.  Same flag set across all three.
- **Ctrl+C clipboard copy** in the terminal pane is now documented in
  the toolbar tooltip text; the feature itself was already wired, just
  not surfaced.
- **`rich` bumped to 15.0.0** and `textual` to 8.2.7.  No
  user-visible behavior change in normal use.
- **`idna` bumped to 3.17** for CVE-2026-45409.

## 0.70.0 (2026-05-28)

The theme of this release is the CRC code generator growing up and
moving out.  What started as an in-tree helper for `/proto.crc` is now
**crcglot**, a standalone PyPI package with its own test matrix that
execution-verifies every algorithm in every target language on every
push.  termapy depends on it like any other library, the supported
languages and variants are now discovered dynamically (so a new crcglot
release adds targets to termapy with no code change), and along the way
the generator gained streaming APIs, custom-parameter support,
slice-by-8 tables, and three new target languages.  This release also
adds `/find` (interactive scrollback search) and rebuilds the command
palette on Textual's native widget.

### CRC code generation extracted to crcglot

The CRC subsystem is now its own package.  termapy declares a
`crcglot>=0.8.0` dependency and imports the catalogue, the generators,
and the calculation kernel from it; nothing CRC-specific is vendored in
the termapy tree anymore.

The win is verification.  crcglot ships its own CI that compiles and
runs generated code for every (algorithm x language x variant) cell and
checks it against the reveng catalogue's published check value -- over a
thousand exec tests per push.  termapy no longer re-implements that;
its CRC tests are now thin dispatch smokes ("the REPL routed to crcglot
and got non-empty output back").  Correctness lives where the code lives.

`/proto.crc.<lang>` is now generated dynamically from
`crcglot.LANGUAGES`, so every language crcglot ships -- and every future
one -- appears automatically.  This release surfaces eight targets:
**C/C++, C#, Go, Python, Rust, TypeScript, Verilog, and VHDL**.

### Richer CRC generators

Folded in from the work that preceded the extraction:

- **Streaming API** -- every target now emits an init / update /
  finalize trio alongside the one-shot function, so generated code can
  CRC a stream without buffering it.
- **Custom parameters** -- supply your own Rocksoft parameters
  (width / poly / init / refin / refout / xorout) for an algorithm that
  isn't in the catalogue, plus `symbol-from-file=` to name the emitted
  function after a source file.  Closes the ergonomic gap with `pycrc`.
- **Slice-by-8** -- `--slice8` emits eight-table slice-by-8 code for
  C and Rust (5-10x throughput on CRC-32/64).  Python's `--slice8`
  warns and falls back to `--table` -- measured slice-by-8 is *slower*
  in CPython, so termapy refuses to ship a misleading codepath.
- **CRC-64** -- the catalogue now covers every reveng CRC-64 variant.
- **`file=STEM`** output writes the generated source straight to disk
  with the right extension for the target language.
- Bitwise-only targets (Verilog, VHDL) now **reject** `--table` /
  `--slice8` with a clear error instead of silently emitting bitwise.

There's also a new note in the docs: on most platforms CRC-32 /
CRC-32C run on dedicated CPU instructions and are ~10x faster than
other widths, so prefer them when you get to choose the algorithm.

### `/find` -- interactive scrollback search

`/find <pattern>` searches the scrollback and lets you step through
matches with the matches highlighted in place -- a proper find loop
rather than a one-shot grep.  It's wired into the command palette too.

### Command palette rebuilt on Textual's native widget

The palette now uses Textual's built-in `CommandPalette` (top-center,
fuzzy filter, keyboard-first) instead of the hand-rolled bottom-docked
list.  New entries cover find, grep, help search, and load-and-run
script; the bottom bar gains a `≡` palette button paired with `/`.

### Config schema v22: serial keys nested

pyserial settings (`port`, `baud_rate`, etc.) now live under a
`cfg["serial"]` section instead of at the top level.  Older configs
migrate forward automatically with chatty per-step output, and a config
written by a *newer* termapy gets the correct upgrade hint rather than a
wall of "unknown key (typo?)" noise.

### Improvements

- **Live port pickers** -- the PortPicker and QuickSetup dialogs now
  poll for USB plug/unplug and update the list in real time.
- **Tooltips everywhere** -- every interactive dialog widget gained a
  tooltip; new `StrongCheckbox` for emphasis.
- **Faster test suite** -- pytest-xdist runs `-n auto` by default
  (~2.1x on an 8-core machine).
- **Windows test-env robustness** -- the suite auto-corrects the
  msys2 PATH ordering that breaks the Git-Bash gcc toolchain (emitting
  a `RuntimeWarning` so the fix is visible), and 27 stale CLI
  subprocess tests broken by the v22 nesting were repaired.
- Ghost-text command suggestions now include `--flags`.
- `CmdResult.ok` requires `value=` (type-checked) and auto-resolves
  `Path` values to absolute strings.
- User text is escaped before being wrapped in Rich color tags in
  search highlighting and CLI output.
- A bare prefix character no longer crashes dispatch.

## 0.68.0 (2026-05-20)

The theme of this release is making termapy approachable for the
non-CLI half of its audience.  A loaded config can be turned into
a double-clickable desktop icon with one command (or one checkbox
during setup), the New Config dialog now reflows on wide terminals,
and the "this config was made by a newer termapy" experience is no
longer a wall of "unknown key (typo?)" noise.

### Per-config desktop launchers

`/cfg.icon` turns the currently-loaded config into a real OS
launcher in one step, on all three platforms:

- **Windows** -- a `.lnk` on the Desktop pointing at
  `cmd /k "<python>" -m termapy "<cfg>"`.  The launcher is bound
  to the exact python install that created it, so the icon keeps
  using the same install even when PATH later changes.  Generated
  via the WScript.Shell COM API in PowerShell -- no `pywin32`
  dependency.
- **macOS** -- a small `.app` bundle in `~/Applications/`, with a
  `CFBundleIconFile`-aware launcher script that shells out via
  `osascript` to Terminal.app.
- **Linux** -- an XDG `.desktop` file in
  `~/.local/share/applications/` with `Terminal=true` so the
  desktop environment opens it in its preferred terminal.

All three platforms get the same custom termapy artwork (a DB9
serial connector with a cable receding into the distance,
"termapy" wordmark in phosphor green) rendered from a single SVG
source.  Sub-commands:

- `/cfg.icon` -- create the launcher for the current cfg
- `/cfg.icon --force` -- overwrite an existing one
- `/cfg.icon.remove` -- delete the launcher for the current cfg
- `/cfg.icon.list` -- enumerate every termapy launcher this OS
  can see (works without a cfg loaded, for auditing)

When you create a config via the **New Config** dialog there's a
new tick-box -- *"Add a desktop / menu launcher for this config"*
-- so non-CLI users don't have to discover `/cfg.icon` at all.

Deleting a config via **Cfg picker → Delete** also auto-removes
any matching launcher, so the desktop doesn't accumulate dead
icons.

### New Config dialog grows with the terminal

The QuickSetup dialog used to be capped at 130 cells wide, which
on big screens forced the port-list formatter to drop columns
even when room was available.  It now scales with the terminal
(90% of screen, capped at 200 cells), re-renders columns on
resize, and preserves your selected port across the re-layout.

### Helpful "this cfg is from a newer termapy" warning

Loading a cfg created by a newer termapy used to produce a wall
of misleading warnings:

```text
Config warning: config_version: 22 (current is 21)
Config warning: unknown key: 'record_enabled' (typo?)
Config warning: unknown key: 'protocol' (typo?)
...
```

The "(typo?)" was wrong -- those were new schema fields, not
typos.  The warning now branches on the version comparison:
older configs migrate forward silently as before; newer configs
get one explicit upgrade hint with the correct command for the
install layout it detects (uv tool / pipx / pip / dev tree), and
the per-key noise is suppressed when the cfg is genuinely from
the future.

### Improvements

- **`/cfg.icon` source refactor** -- the per-platform handler
  module shed ~30% of its post-feature LOC during same-day trim;
  clearer shared helpers (`_render_template`, `_already_exists`,
  `_by_platform`) without changing behavior.

## 0.67.0 (2026-05-18)

The theme of this release is making `.run` scripts first-class.  An
exploratory REPL session can now be captured to a `.run` file with
one command (or one button click), and the resulting script is
self-describing: a `#` comment block at the top serves as a Python-
style docstring, surfaced by `/run.list` and `/run.help <script>`.
Plus on-demand PyPI version checking, and a behind-the-scenes
refactor that collapses `/run`'s three host-specific implementations
into a single built-in.

### Record a session to a .run script

The biggest workflow win.  Three commands no longer needed:
"remember what I typed", "open editor", "copy-paste from
scrollback".

- **`/run.record <filename>`** captures every successfully-
  dispatched REPL or device command to the named `.run` file in
  the per-config `run/` directory.  Bare `/run.record` stops.
  Failed dispatches, typos, and `/run.record` itself are skipped
  so the script plays back cleanly.
- **TUI Record button** sits at the right edge of the REPL
  prompt -- green when idle, red ("Stop") while recording.
  Clicking pops a filename dialog.  Hide the button with
  `record_enabled: false` in the config; the REPL command still
  works regardless.
- **Durability**: file opens with `mode="x"` (refuse-if-exists --
  no surprise clobbers) and flushes after every write, so a
  crash mid-session leaves a partial-but-usable file on disk.
- **Single recording invariant**: starting a second recording
  while one is active is a clear error, not silent corruption.

Workflow: record a session, add a `#` docstring at the top of
the recorded file (see next section), and you have a reusable,
discoverable script.

### .run script docstrings (auto-discovery)

A contiguous block of `#` comments at the very top of a `.run`
file is the script's docstring -- mirrors Python's module
docstring shape.  First line is the summary, full block is the
long help.  Block ends at the first blank or non-comment line.

- **`/run.list`** now shows `filename -- one-line summary` for
  each script that has a docstring.  Scripts without one still
  appear, just without a summary -- the listing is never
  silently filtered.
- **`/run.help <script>`** prints the full docstring block.
  Accepts either the bare stem (`welcome`) or the full filename
  (`welcome.run`).  Undocumented scripts fail loudly ("no
  docstring; add # comments at the top") rather than silently
  printing nothing.
- **MCP catalog** picks up summaries, so an LLM choosing from a
  script library has something to read instead of guessing from
  filenames.

All eight demo scripts already started with `#` comments, so the
feature lit up automatically without any demo-content changes.

### On-demand PyPI version check

Termapy already nudges you in the background when a newer release
is available, but the cadence is 7 days and the path is TUI-only.
Two new commands ask PyPI on demand:

- **`/ver.latest`** prints just the latest PyPI version (bare
  data, scriptable via `$(L) <- /ver.latest`).  Symmetric with
  bare `/ver`, which reports the installed version.
- **`/ver.info`** prints the installed-vs-latest comparison line
  ("installed v0.66.0  ->  latest v0.67.0  (update available)" or
  "installed v0.67.0  (up to date; latest v0.67.0)").  Sets
  `CmdResult.value` to the latest PyPI version for scripting.

Both bypass the 7-day throttle (the user typed the command -- they
want a fresh answer) and surface network failure as a clear error
rather than silently succeeding.

### Internal: /run promoted to a built-in

`/run` was registered as a hook in three places (TUI's
`app_hooks.py`, CLI's `_register_hooks`, MCP's `_register_hooks`),
each with its own implementation reaching into adapter internals
(TUI's `@work`-decorated runner, CLI's synchronous wrapper, MCP's
inline lambda).  Adding or changing a `/run` subcommand meant
editing three files in lockstep.

Now `/run` is one builtin (`builtins/commands/run.py`).  Folder
subcommands (`.list` / `.dump` / `.show` / `.explore`),
`/run.help`, and `/run.legacy` are real sub_commands declared in
one place.  Host-specific behaviour (TUI's threaded execution vs
CLI/MCP's synchronous) lives on `TerminalHost._run_script`, which
TUI overrides with the `@work(thread=True)` variant that posts
overlay messages.  Three call sites collapse to one; ~190 lines
of duplicated host wiring removed.

### Internal: post-dispatch observer hook

`ReplEngine` now exposes `add_post_dispatch_observer` /
`remove_post_dispatch_observer` -- a list of `(line, result)`
callbacks fired after every dispatch.  `/run.record` is the first
consumer.  The same hook is the right shape for several future
features (audit log, `/!!` repeat-last-successful, MCP event
streams) -- built once, useful many times.  Observer exceptions
are caught and reported; a buggy subscriber can't break dispatch.

### Help-text consistency sweep

Audit of all 220 commands surfaced a few inconsistencies that
landed in one cleanup branch.

- **`proto.status` -> `proto.info`** (hard cut, no forwarder).
  Brings the lone holdout into the `.info` convention used by 8
  other commands (`cfg.info`, `port.info`, `mcp.info`,
  `profile.info`, `term.info`, `proto.crc.info`, `edit.info`,
  `ver.info`).
- **"Show" -> "Print" in help text** for 12 commands whose help
  meant "print to terminal."  Disambiguates from the `.show`
  subcommand family, which uniformly means "open in external
  viewer."  The "Show or set X" pattern for read/set fields
  stays (it's a different idiom).
- **`/var.list` and `/env.list`** are now declared as explicit
  aliases for bare `/var` and `/env`.  The duplicated help text
  was confusing -- one is the canonical form, the other says so.
- **`/cfg` "Show or change"** -> "Show or **set**" to match
  every other read/set command.

### `record_enabled` cfg key

Per the existing `cfg_enabled` / `run_enabled` / `proto_enabled`
pattern, `record_enabled: true` controls whether the Record
button appears in the TUI.  Users who don't want the button (or
who use only CLI/MCP) can hide it without losing the
`/run.record` REPL command.  Config version bumped to 21 with a
benign `setdefault` migration.

## 0.66.0 (2026-05-15)

Two themes drive this release: **CLI/MCP feature parity** (an LLM
client over MCP now has a usable command surface, not a stripped
subset) and **scripting actually works** (`CmdResult.value` is
populated everywhere a script would want to capture it).  A
breaking rename folds `/xmodem` and `/ymodem` under `/xfer`, and
the release pipeline is fixed so the publish workflow stops
showing red on every release.

### CLI / MCP feature parity

- **`/log.dump`, `/log.fingerprint`, `/log.show` are now built-in
  plugins** instead of TUI/CLI hooks.  MCP picks up builtins but
  not hooks, so before this release an LLM had no way to read the
  session log it was meant to be debugging.  `/log.show` stays
  gated on `gui_apps` so it correctly hides on headless MCP.
- **`/run.profile.*` family available in CLI.**  The five
  subcommands (`.dump`, `.list`, `.show`, `.explore`, `.cmd`)
  used to only register on the TUI host; CLI users running
  `/run.profile <script>` had no way to read the resulting
  CSV without switching to the TUI.  Handlers moved into a
  shared `run_profile_hooks` module so both hosts register the
  same code path.
- **`/run` callable from MCP.**  Added to `MCPHost._register_hooks`
  alongside the existing `/delay`.  An LLM can now trigger
  pre-defined `.run` automation files via
  `run_command("/run myscript.run")`.  `/run.list`, `/run.dump`,
  and `/run.legacy` ride along; `/run.show` and `/run.explore`
  register but stay `gui_apps`-gated.
- **Automated CLI/MCP parity tests.**  19 new tests in
  `test_cli.py` and `test_mcp_server.py` pin which commands are
  callable from which host and which capability gates apply.
  Replaces the per-branch manual smoke that had been driving the
  audit work.

### Scripting return values (`CmdResult.value`)

28 handlers across `seq.py`, `env_var.py`, `var.py`, `cfg.py`,
and `port.py` used to return bare `CmdResult.ok()`, which meant
the value-capture idiom (`$(X) <- /cmd args` and MCP's
`run_command(..., output="silent")`) saw `None` instead of the
data the command had just computed.  Affected commands now
return the formatted value the user sees:

- `/seq` returns the counter line; `/seq.reset` returns `"reset"`.
- `/var <name>` returns the variable value (`""` when undefined);
  `/var` returns newline-joined `NAME=value` pairs; `/var.set`
  returns the new value; `/var.clear` returns the count cleared.
- `/env.set` returns the new value; `/env.list <pattern>`
  returns matches; `/env.reload` returns the post-reload count.
- `/cfg <key>` returns the value; `/cfg key value` returns the
  new (or existing) value; `/cfg.list` returns newline-joined
  config names; `/cfg.explore` / `/cfg.show` / `/cfg.info`
  return the path opened.
- `/port <name>` returns the new port; `/port.disconnect`
  returns the last configured port (snapshotted before the
  tear-down); `/port.list` / `/port.info` / `/port.mode` /
  `/port.flow` / `/port.break` / `/port.chip` return the same
  multi-line text the user sees, via a new `_joined_value()`
  helper.

A scripted end-to-end smoke
(`tests/fixtures/value_capture.run` +
`tests/test_value_capture_e2e.py`) exercises every value path
through a real CLI subprocess and asserts the captured value
appears in stdout.  Verified to catch regressions by stashing
the source fix and watching the test fail.

### Breaking: `/xfer` consolidates file-transfer commands

`/xmodem.send`, `/xmodem.recv`, `/ymodem.send`, and `/ymodem.recv`
are gone.  The file-transfer family lives under `/xfer`:

```text
/xfer.root [path]             (unchanged)
/xfer.xmodem.send <file>
/xfer.xmodem.recv <file>
/xfer.ymodem.send <file> ...
/xfer.ymodem.recv {dir}
```

`/help xfer` describes XMODEM / YMODEM inline.  No legacy
forwarders -- `/xmodem` and `/ymodem` return `Unknown command`.

### `/color` -> `/term.color`

Continues the v0.64 rename pattern.  `/color` survives as a
hidden legacy forwarder.  Auto-run cfg strings (`on_connect_cmd`,
default scripts) are migrated.

### Release pipeline fix

The publish workflow has been showing red on every release back
through v0.64.0 -- `uv publish --check-url` does not actually
skip a duplicate upload, even when the wheel has been on PyPI's
Simple index for 24h.  Two changes:

1. `release_publish.py` now uploads to PyPI **before**
   `gh release create`, so the `release.published` event fires
   only after the file is on PyPI.
2. The workflow uses a Warehouse JSON pre-check
   (`pypi.org/pypi/<pkg>/<v>/json`) instead of the broken flag.
   HTTP 200 -> skip publish (with a `::notice::`); HTTP 404 ->
   the safety-net upload runs.

### CLI smoke coverage

`--version` and `--help` are now guarded by tests
(`TestVersionFlag`, `TestHelpFlag`).  `--help` is asserted to
exit 0, produce a multi-line block, and document every major
flag -- catches argparse rewires that silently drop a flag.

### Internal refactors

These are no-op for users; they show up in `ls src/termapy/`
as named subsystems instead of one 4225-line `app.py`:

- `app.py` is now ~3050 lines (was ~4225).  Extracted into:
  `app_hooks.py` (TUI REPL hooks), `capture_view.py`
  (modal capture display), `title_bar.py` (title-bar rendering),
  `pickers.py` (modal-picker callbacks), `info_views.py`
  (info-report modals).
- `MCPHost.run_command_async` inlined into the class body.
- Profile `transport` block dropped; wire-level settings live
  in cfg directly.
- `pic_map` parser inlined into the example plugin that used it;
  no callers remained in core.

## 0.65.1 (2026-05-14)

Maintenance release.  Drives the lint baseline to zero and hardens
the release process so it can't drift back.

### Code-hygiene baseline

- **ty diagnostics: 23 -> 0** across `src/termapy/`.  Touch
  points: annotate the monkey-patched `MCPHost.run_command_async`
  and `_run_lock` attrs, declare `TerminalHost.prefix` and
  `_setup_context` on the base, type the `typedef_to_catalog`
  mixed-value dict as `dict[str, Any]`, widen `format_kv_lines`
  rows to `Sequence` (covariant), narrow `demo_ndjson` args via an
  explicit local, switch the `UIHandle` host-wiring sites (app,
  cli, mcp) from method-shadowing dot assignment to the existing
  `_*_impl` pattern, drop the now-dead `screen_capture` gate from
  `get_screen_text`, swap `proto_debug.serial_io()` for the
  current `ctx.serial.io()` API, and import `PluginContext` via
  `TYPE_CHECKING` in `plugins/command.py`.
- **ruff issues: 32 -> 0.**  Mostly `F401` unused imports cleaned
  by `ruff --fix`; a handful of unused locals and a stray lambda
  assignment in `test_handles` needed manual deletes.

### Release-process gates

- **release_prep now hard-fails** if ruff or ty has any issues.
  Operators fix the source on a chore branch first; the release
  can't bake regressions into a published version.
- **Coverage % auto-refreshes** every release.  `measure_coverage_percent()`
  parses the `TOTAL ... N%` line from `pytest --cov`; the result
  flows into the README's test-coverage summary, the discussion
  paragraph, AND the shields.io coverage badge URL.
- **Coverage badge** moved from a misleading static
  `coverage-testing-yellow` sticker to a real `coverage-N%`
  shield with green / yellow / red thresholds (80+, 65-79, <65).
- Reconciled the README's pre-existing 67%/70% coverage drift
  (the discussion paragraph said 70%; the summary line still
  showed the hard-coded 67%) -- the next release_prep
  substitution applies cleanly to both.

### Profile-type vocabulary

- Demo's `LED` argument type renamed `onoff` -> `on_off` for
  snake_case consistency with the rest of the type vocabulary.
  Authoring guide updated.  HTML help rebuilt by this release.

### Internals

- `plugins/__init__.py` now re-exports `OUTPUT_LEVELS`,
  `LEVEL_FLAGS`, `format_kv_lines`, and `parse_output_level`
  directly from `termapy.plugins.output_levels` (the previous
  pass-through via `plugins.context` was redundant after the
  package split).
- Fixed a stray "you you" typo in the `protocol/crc_codegen`
  module docstring caught during the lint pass.

### Docs

- CLAUDE.md Precommit + Release sections now spell out the
  ruff=0 / ty=0 gate and enumerate every figure release_prep
  auto-refreshes (test count, coverage %, ty count + color,
  per-module line counts, rounded UI line counts).

No user-facing API or CLI changes from 0.65.0.

## 0.65.0 (2026-05-14)

### Headline: profile-local typed args

Device profiles can now declare a top-level `types` block that
`typed_args[i].type` references by name.  Six kinds in the first cut:
`enum`, `int_range`, `float_range`, `str_length`, `pattern`, and a
`format_spec` stub wired to the protocol-format-spec language for
future binary-arg validation.  Five builtins (`int`, `float`, `bool`,
`hex`, `str`) remain available without declaration; custom names
cannot shadow them.  The MCP dispatcher validates bound `typed_args`
against the active profile's type registry before bytes hit the wire
-- bad values short-circuit to a structured failure naming the arg,
type, value, and command.  The catalog inlines a `type_info` field
per typed_arg so an LLM reading `termapy://commands.json` sees the
full contract without cross-referencing the top-level block.

### V2-only profile path: `/profile.load` absorbs `/include`

`/include` and its companion family (`/include.reload`,
`/include.list`, `/include.dump`, `/include.clear`) are retired.
`/profile.load` grows three new shapes that replace them:

- `/profile.load <path>` -- load from file (existing).
- `/profile.load cmd=<command>` -- fetch a v2 profile JSON from the
  connected device, install as the active profile.  Replaces
  `/include cmd=...`.
- `/profile.load` (no args) -- reload current source (file or cmd,
  whichever was last used).
- New: `/profile.save [<path>]` -- write the active profile to disk
  (default `<cfg_dir>/<cfg_name>.profile.json` so the next connect
  auto-loads it).  Warns when every command has `enabled: false`.
- New: `/profile.unload` -- clear the active profile.

The active profile is now the single source of truth for device
commands; the catalog reads from `active_profile.commands`.  Schema
is `profile_version: const 2` (v1 retired).

**Auto-migration**: cfgs with `auto_include_on_connect: true` AND
`device_json_cmd: <cmd>` get rewritten on first load to set
`mcp_on_connect_cmd: "/profile.load cmd=<cmd>"` so existing
auto-fetch behavior is preserved with zero manual intervention.

### MCP server hardening

- `format: "none"` is now **verify-silence**: the executor waits a
  short window (default 100 ms) and fails the call with a structured
  `unexpected_output` payload if the device replies anyway.  Catches
  stale profiles and firmware regressions instead of silently
  dropping bytes.  Override with `response.timeout_ms`; opt out
  entirely with `timeout_ms: 0`.
- `async_events` field included in every `run_command` response
  (push delivery; no more polling for unsolicited device events).
- `/profile.info` shows per-field cfg-vs-profile transport drift
  so silent-override confusion is visible at a glance.

### CLI / TUI

- New cfg flag `validate_typed_args` (default false) opts into
  CLI-side typed-arg validation, mirroring MCP semantics.  Off keeps
  raw access (device errors are the source of truth); on catches
  bad values before the wire.
- Disconnect banner now names the port: `Disconnected: COM4`
  mirroring `Connected: COM4 ...`.
- `cfg_data_dir()` refuses to live-activate a cfg path inside the
  installed termapy package (no more accidental source-tree pollution
  from `termapy --cfg src/termapy/builtins/demo/demo.cfg`).

### Internals reorganized into library-shaped subpackages

Three top-level grab-bags became self-contained subpackages with
public-API re-exports.  No consumer code changes (existing imports
keep working via re-exports); the new layout positions each
subpackage as a viable standalone PyPI release when ready:

- `termapy.profile` -- schema, loader, type registry, matcher.
- `termapy.protocol` -- format-spec parser, CRC catalog (64
  algorithms) + codegen, .pro runner, visualizer loader.
- `termapy.usb` -- USB lookup tables (manufacturer alias, VID/PID
  -> chip info, VID -> vendor name + ~3,400-entry fallback).
- `termapy.dialogs` -- monolithic `dialogs.py` split per-class.

### Migration

- `config_version` bumped 16 -> 17.  Drops `auto_include_on_connect`
  and `device_json_cmd`, optionally rewriting them into
  `mcp_on_connect_cmd`.  Both retired keys land in `DEPRECATED_CFG`
  with helpful messages for hand-edited cfgs.
- `migrate_json_to_cfg` tightened to strict exact-name match
  (`<dir>/<dir>.json`) so profile files aren't renamed during
  migration.

### Other polish

- `FolderSpec` defaults flipped to the common case so FOLDERS lists
  declare only exceptions.
- Demo's `AT+HELP.JSON` publishes a `types` block, demonstrating
  the new contract end-to-end.
- Authoring guide gains a "Drafting a profile with an AI" section.
- ARCHITECTURE.md fully refreshed to reflect post-refactor tree;
  release scripts updated to handle subpackage line counts.

## Unreleased

### Added

- **``-e`` / ``--exec`` one-shot CLI mode** -- ``termapy --cli <cfg>
  -e "AT+INFO"`` connects, dispatches a single command, prints the
  response to stdout, and exits with status 0 (success) or 1
  (failure).  Implies ``--cli`` and ``--no-color``; suppresses the
  connect banner, input echo, and cfg-driven autorun so captured
  stdout contains only the command's output.  Pair with
  ``request_mode=true`` cfg for JSON-envelope output.  Mutually
  exclusive with ``--run``.  Fills the long-standing capability gap
  behind ``--cli``: previously the only way to run a single command
  non-interactively was to write a one-line ``.run`` file.

- **``ctx.is_oneshot``** -- ``PluginContext`` callable that returns
  ``True`` when the host is in ``--run`` or ``--exec`` mode (false
  for interactive TUI / CLI REPL).  Plugins authoring chatty
  on-connect logic can read this to suppress output that would
  corrupt captured stdout.

### Changed

- **``PluginContext`` namespaced capability handles (breaking for
  external plugins)** -- the old flat ``ctx`` surface (~50 fields like
  ``ctx.write``, ``ctx.serial_io``, ``ctx.cap_dir``, ``ctx.notify``) is
  replaced by five capability-domain handles: ``ctx.io``, ``ctx.serial``,
  ``ctx.fs``, ``ctx.ui``, and ``ctx.engine``.  Plugin authors now write
  ``ctx.io.write(...)``, ``ctx.serial.io()``, ``ctx.fs.cap_dir``,
  ``ctx.ui.confirm(...)``, etc.  Plugin author surface drops from ~50
  visible names to 12.  TUI-only methods on ``ctx.ui`` raise
  ``MissingCapability`` when called from CLI without declaring the
  matching capability on ``Command.needs``, replacing today's silent
  no-ops with hard failures.  See [Writing
  Plugins](src/termapy/help/writing-plugins.md) for the new shape.

- **``plugins.py`` split into ``plugins/`` package** -- the
  ~1700-line monolith becomes a 12-file package with clear domains:
  ``context.py`` (PluginContext), ``command.py`` (Command, CmdResult,
  Transform, Directive), ``capabilities.py`` (CapabilitySet,
  MissingCapability), ``loader.py`` (discovery), ``output_levels.py``
  (silent/quiet/normal/verbose), and ``handles/`` (the five capability
  handles).  All public re-exports preserved at ``termapy.plugins``.

- **``builtins/plugins/`` renamed to ``builtins/commands/``** -- removes
  the long-standing confusion between the framework package
  (``src/termapy/plugins/``) and the directory of built-in command
  modules.  37 command files moved.  No user-facing change.

- **CLI mode no longer loads Textual** -- ``_run_cli_mode`` moved from
  ``app.py`` to ``cli.py``, and ``_run_tui_mode`` is now lazy-imported
  from ``entry.py`` only when the user picks TUI.  ``termapy --cli -e
  "/help"`` previously pulled in 144 textual modules (~370ms of import
  time); now it loads zero.

- **``--cli`` argparse help text** -- now reads "Use plain-text mode
  instead of the TUI (interactive REPL by default; pair with --run
  or --exec for one-shot)" so the interactive-REPL semantics are
  obvious from ``termapy --help``.  No flag rename; the umbrella
  "CLI mode" label now covers both REPL (default) and one-shot
  (--run / --exec) forms.

- **Connect-time autorun gate** -- the cfg-driven autorun checks
  (``device_json_cmd`` auto-include, ``on_connect_cmd``, the help
  banner) previously skipped only ``--run``.  They now uniformly skip
  any one-shot mode via ``CLITerminal.is_oneshot``, so ``--exec``
  output is never polluted by autorun output.

## 0.64.0 (2026-05-01)

CLI and output-system overhaul.  The pre-0.64 boolean ``verbose`` toggle
and the all-or-nothing ``.quiet`` suffix are replaced by a single
monotonic dial -- ``silent`` < ``quiet`` < ``normal`` < ``verbose`` --
that works the same way at three scopes (session default, per-call
suffix, per-call flag).  ``termapy --ports`` gains a stable JSON shape
with multi-axis filters, and the new ``vendor`` / ``location`` /
``driver`` fields make port identification practical when you have
identical adapters or unrecognized chips.  ``--watch`` is much
lighter on CPU (3-4% during a session, was ~50% on multi-port hosts)
thanks to a fast-gather path that skips the per-port in-use probe;
user-observable reaction time is bounded by Windows USB-enumeration
latency, which polling can't move.  Old command
names continue to work as hidden forwarders, with ``/run.legacy``
extended to rewrite them.

### 0.64.0 New Features

- **Four-level output dial** -- ``/term.output {silent|quiet|normal|
  verbose}`` replaces the boolean ``/term.verbose`` toggle.  Each level
  adds a channel: ``silent`` shows nothing (script consumers read
  ``CmdResult.value``), ``quiet`` shows the answer, ``normal`` adds bulk
  data output, ``verbose`` adds progress chatter.  The same vocabulary
  works as a per-call flag (``cmd --quiet``) or suffix (``cmd.quiet``);
  the universal level dispatch means every command accepts them
  without per-handler setup.  CLI startup flags ``--silent`` /
  ``--quiet`` / ``--verbose`` set the default before the REPL boots,
  which matters most for ``--run`` script invocations.

- **``--ports --json`` with stable schema** -- one JSON object per port
  with snake_case keys, ``null`` for unknown values (not omitted),
  numeric ``vid`` / ``pid`` plus the formatted lowercase-hex ``vid_pid``
  string, and a boolean ``in_use``.  Schema is documented in
  ``help/cli.md`` and treated as API for scripts.  ``--chips --json``
  similarly emits the bundled chip lookup as structured records.

- **Multi-axis port filters** -- ``--vid`` / ``--pid`` / ``--mfg`` /
  ``--sn`` AND-compose with each other and with the existing
  ``--ports <name>`` filter.  Hex flags accept ``0x0403`` or ``0403``;
  ``--mfg`` is a case-insensitive substring; ``--sn`` is exact.
  Distinct exit codes for "bad input" (2) and "no match" (1) so CI
  pipelines can distinguish typo from missing-device.

- **Vendor / location / driver columns** -- three new fields on every
  port record, surfaced in ``--ports``, the JSON output, both port
  pickers (PortPicker and QuickSetup), and the title-bar port hover
  tooltip.

  - ``vendor`` is the silicon-vendor name resolved from the VID via a
    new curated ``usb_vendor`` table (~30 entries covering FTDI,
    Silicon Labs, WCH, Microchip, Microsoft, Espressif, ST, NXP, ...).
    Independent of the descriptor / driver-INF ``manufacturer`` string,
    so a Microchip USB-CDC chip running on Microsoft's ``usbser.sys``
    correctly reports ``Microchip`` as the silicon vendor while
    ``manufacturer_raw`` still preserves the literal ``Microsoft``.

  - ``location`` is the bus path (``1-2.3`` on Linux,
    ``Hub_#0009.Port_#0004`` on Windows after registry fallback for
    FTDI, hex location ID on macOS).  Disambiguates two physically
    distinct adapters that share VID/PID/SN, which cheap clones and
    duplicate-of-the-same-product scenarios both produce.

  - ``driver`` is the bound kernel module (Linux) or service name
    (Windows): ``ftdi_sio``, ``cdc_acm``, ``cp210x``, ``FTSER2K``,
    ``usbser``, etc.  Surfaces driver-binding problems at a glance.

- **DEMO synthesis for hardware-free CI** -- ``termapy --ports DEMO
  --json`` and ``termapy --info DEMO`` now return synthetic records
  for the reserved virtual port names so CI pipelines can exercise
  the CLI without plugging in real hardware.  Bare ``--ports`` (no
  filter) does NOT include DEMO -- it appears only when explicitly
  named, the same way pyserial's ``loop://`` URL handler is reachable
  but not enumerated.

- **``--watch`` lighter on CPU** -- the watch loop previously called a
  per-port ``_check_in_use`` probe that opens each port via
  ``serial.Serial()`` to detect contention.  On Windows this is
  ~250 ms / port (driver init on each ``CreateFile``), so gather
  scaled linearly with port count and consumed ~50% CPU during a
  watch session on a multi-port host.  A new fast-gather path skips
  the in-use probe for the watch loop only -- ``--ports`` and
  ``--info`` keep the full check -- so gather drops from 250+ ms to
  ~7 ms regardless of port count, and CPU during watch falls to
  ~3-4%.

  Note: this does **not** lower user-observable hot-plug reaction
  time meaningfully on Windows.  ``comports()`` itself returns stale
  data for 1-3 seconds after a USB event (longer for FTDI, whose
  driver does an EEPROM-read handshake on every connect), so the
  floor is OS USB-enumeration latency, not termapy's polling loop.
  Reaching that floor would require event-driven APIs (``WMI
  Win32_DeviceChangeEvent``, ``RegisterDeviceNotification``), and
  even those fire only after the driver finishes init -- so the
  practical improvement on Windows would be small.  Linux with
  ``udev`` events would benefit more; future work.

- **``/port.connect`` and ``/port.disconnect``** -- ``/port.open`` and
  ``/port.close`` are renamed to match the user-facing language already
  used in status messages (``Connected:`` / ``Disconnected.``).  Old
  names keep working as hidden forwarders.

- **Three-field manufacturer breadcrumbs** -- the ``--ports --json``
  record now exposes ``manufacturer_raw`` (literal descriptor / INF
  string), ``manufacturer`` (column-friendly aliased short form), and
  ``vendor`` (silicon vendor by VID).  These often agree; when they
  diverge -- typically because a device uses a generic class driver --
  all three are visible so engineers can spot the layering instead of
  having one source silently override the others.

### 0.64.0 Improvements

- **Faster CLI startup** -- the import-discipline guard for
  ``termapy.cli_flags`` is now mechanically enforced by a test that
  fails if Textual / Rich / prompt_toolkit ever sneak into the
  ``termapy --ports`` import path.  Cold-start under 50 ms is
  preserved.

- **Migration forwarders and ``/run.legacy`` updates** --
  ``/verbose on|off`` continues to work, translating to
  ``/term.output verbose|normal``.  ``/term.verbose`` similarly.
  The old "set silently" idiom (``echo.quiet on``, ``term.echo.quiet
  on``) is now recognized by ``/run.legacy --fix``, which rewrites it
  to the ``.silent`` form.  Existing ``.run`` scripts continue to
  work; migrate at your own pace with ``/run.legacy *``.

- **Port pickers widened to 130 columns** -- the QuickSetup ("New
  Config") and PortPicker dialogs gain horizontal space for the new
  vendor/location columns.  ``DROP_ORDER`` reprioritized so vendor
  outranks chip / vid_pid / driver when narrow -- ``Microchip`` in
  9 characters beats ``04D8:9036`` or a 32-character chip name when
  the row is squeezed.

- **FTDI Windows location recovery** -- pyserial returns ``None`` for
  ``location`` on FTDI ports because the FTDIBUS driver hides bus-
  location info from SetupAPI.  termapy now falls back to the registry:
  reads the FTDI device's ``ContainerID``, walks ``HKLM\\SYSTEM\\
  CurrentControlSet\\Enum\\USB`` for the matching USB partner node,
  and reads ``LocationInformation`` from there.  Strings are
  normalized so hub appears before port (``Hub_#0009.Port_#0004``,
  not ``Port_#0004.Hub_#0009``) to match natural reading order.

## 0.63.1 (2026-04-29)

Documentation-only patch release.  Sync of the bundled in-app help
with the v0.63.0 UI changes -- title-bar button labels (``Help``, no
longer ``?``, plus the relocated Exit ``X``), the new ``Ctrl+Shift+1..5``
hotkey aliases, the ``/term.*`` namespace, and one-line entries for
the new commands (``/run.legacy``, ``/credits``, ``/var.list``, plus
the chip-aware ``/port.list`` table).  No code changes.

## 0.63.0 (2026-04-29)

REPL UX overhaul.  Termapy's title-bar buttons, pickers, and command
popup are now reachable from typed commands, with parity between TUI
and CLI.  Display and session toggles consolidate under a new
``/term.*`` namespace, session logs gain a ``/log.*`` family for
inspection, and ``/proto.crc.find`` identifies an unknown CRC algorithm
from a captured packet.  Old command names keep working as hidden
forwarders, with a ``/run.legacy`` tool to find and rewrite them in
existing scripts.

### 0.63.0 New Features

- **``/term.*`` namespace** -- display and session toggles consolidate
  under one namespace, matching ``/port.*`` and ``/cfg.*``.  Nine
  subcommands cover the ground that used to be scattered across
  top-level commands: ``echo``, ``line_no``, ``line_endings``,
  ``verbose``, ``timestamps``, ``hex``, ``encoding``,
  ``send_bare_enter``, ``info``.  The old names (``/echo``,
  ``/line_no``, ``/show_line_endings``, ``/verbose``) keep working as
  hidden forwarders -- they redirect transparently to the ``/term.*``
  equivalents and emit a one-time dim note suggesting the new spelling.

- **``/run.legacy`` migration tool** -- scans a ``.run`` script for
  old command names and either reports or (with ``--fix``) rewrites
  in place.  ``/run.legacy *`` scans every script in the config's
  ``run/`` directory in one pass, so a project full of pre-0.63
  scripts can be migrated with a single command.

- **Bare command opens a picker** -- ``/cfg`` ``/run`` ``/proto``
  ``/port`` (no args) open the matching picker dialog in TUI mode and
  fall through to ``/help <name>`` in CLI mode, with parity across
  all four.  Each command gains a ``.help`` subcommand that prints
  the same long help plus an ``AVAILABLE FILES`` tree of what's
  actually in the relevant folder.  ``/cfg.show`` is new, opening the
  loaded config in the system viewer for symmetry with
  ``/run.show`` / ``/proto.show`` / ``/ss.show``.

- **``/log.*`` namespace for session-log inspection** -- alongside
  the existing ``/log.clear``:

      /log.show         opens the session log in the system viewer
      /log.dump {N}     prints the entire log (or last N lines) to terminal
      /log.fingerprint  writes OS, terminal, Python, termapy, config,
                        and serial port state to the log so old logs
                        are unambiguous when read back later

  Plus ``/term.log <text>`` for annotating the log without echoing
  to screen -- useful for markers, timestamps, and notes that are
  only interesting on later review.

- **``/proto.crc.find``** -- identify an unknown CRC algorithm from
  a captured packet.  Accepts ``bin=<hex>`` (tries trailing 1/2/4
  bytes as CRC, both endians) or ``asc=<text>`` (trailing 2/4/8
  chars as hex-ASCII CRC).  Iterates all 64 catalogue algorithms,
  collapses catalogue aliases to one canonical line, and prints the
  source-code generation command when exactly one algorithm matches.

- **Title-bar Exit, config-gated buttons** -- the Exit button moves
  out of the bottom bar and into the title bar as a red ``X``,
  positioned by OS convention (top-left on mac/linux, top-right on
  Windows).  The ``?`` help button is renamed to ``Help`` and moves
  after the ``Cfg`` / ``Run`` / ``Proto`` trio.  New config booleans
  ``cfg_enabled`` / ``run_enabled`` / ``proto_enabled`` (default
  true) hide the corresponding title-bar buttons for projects that
  don't use them.

- **``/proto.crc.find`` attribution & ``/credits``** -- a new
  ``ACKNOWLEDGMENTS.md`` page (bundled into help) names the projects
  Termapy depends on: Greg Cook (reveng CRC catalogue), Will McGugan
  / Textualize (Textual + Rich), Jonathan Slenders (prompt_toolkit),
  Chris Liechti (pyserial).  ``/credits`` prints it in-terminal.
  The Help button tooltip is restructured into a colored Rich table
  with author attribution.

### 0.63.0 Improvements

- **Animated ``/delay`` progress bar in TUI** -- the CLI has had
  ``[####....] 3s/10s`` for a while; the TUI now shows the same bar.
  Interactive ``/delay`` renders into the status bar; script
  ``/delay`` appends to the script overlay so
  ``delay.run [1/4] [bar]`` stays in one line.

- **Escape dismisses every modal** -- ``ConfigEditor``,
  ``MarkdownViewer``, ``QuickSetup``, ``NamePicker``, and the proto
  debug screen now accept Escape as an alternate dismiss key
  alongside Ctrl+Q.

- **Filtered command popup** -- clicking the ``/`` button with
  ``po`` typed in the input now shows only commands containing ``po``
  (``/port``, ``/proto``, ``/stop``, ...) instead of the full list
  from the top.  ``/po`` and ``po`` behave identically; arguments
  past the first token are ignored.

- **Ctrl+Shift+1..5 hotkeys** replace the unreliable ``Alt+F*``
  aliases for Help/Cfg/Run/Proto/Edit-Config.  F-keys remain primary;
  the ``Ctrl+Shift+digit`` aliases survive VS Code's terminal capture
  via ``modifyOtherKeys`` / csi-u.  The VS Code detected banner now
  labels each shortcut so users know what ``Alt+P`` / ``Alt+S`` /
  ``Alt+T`` actually do.

- **Richer ``/port.list``** -- now uses the picker-style table
  (PORT / MFG / DESCRIPTION / CHIP / SPEED / VID:PID / SN, with
  terminal-width-aware column drop) -- same format as bare ``/port``
  and the ``--ports`` CLI flag.  The zero-config welcome screen
  picks up the richer output for free.

- **``cli_intellisense`` -> ``cli_completion`` rename** --
  IntelliSense is a Microsoft trademark, and "completion" is the
  accurate name anyway (tab completion + auto-suggest + help
  toolbar).  Migration v13 -> v14 handles the rename for existing
  configs.

- **``/proto.crc.help`` -> ``/proto.crc.info``** -- the command
  shows algorithm parameters, not command help, so the name was
  misleading.

- **Command and error-message consistency audit** -- ``{on|off}``
  spellings are uniform (no inner spaces, all toggles optional).
  Error messages adopt sentence case and a small set of standard
  phrasings (``Unknown X`` / ``Invalid X`` / ``X not found`` /
  ``No X loaded.`` / ``Usage: /cmd ...``).  ``/var.list`` added as
  an explicit alias for bare ``/var`` listing.

- **New ``Environment & compatibility`` help page** -- one place
  for OS support, terminal emulators, VS Code quirks, macOS
  Option-as-Meta, KVM cross-platform keyboards, and SSH/WSL/container
  notes.  Replaces the inline VS Code block that used to live in
  ``getting-started.md``.

- **``FileTree`` extracted to ``tree_render.py``** -- single source
  of truth for box-drawing connectors, dim/cyan/blue style triplet,
  and child-indent pattern that used to be duplicated between
  ``cfg._build_tree`` and ``help.append_files_section``.  Pure
  module, no Textual or serial deps; 14 new unit tests.

- **Loopback integration tests** -- 7 new tests using pyserial's
  ``loop://`` URL exercise the termapy <-> pyserial send path:
  line-ending config, ``/raw`` bypass, CRC append via
  ``/proto.send``, latin-1 encoding on the wire, plain round-trip.
  ``/cap.bin`` with exact byte-count targets so captures close on
  target-hit (reliable) rather than timeout.

## 0.62.0 (2026-04-19)

CLI-mode release.  Termapy's ``--cli`` frontend gained a lot of
polish: it now runs without a config file, knows how to switch
configs in a live session, keeps long device responses from
colliding with the next prompt, and got one bug fix that was
actively breaking ``help``-style commands on firmware.  The
``cfg["port"]`` field learned to take USB serial numbers with
pipe-separated fallback chains, and every folder-owning command now
exposes the same ``list / explore / show / dump / clear`` subcommand
family.

### 0.62.0 New Features

- **USB serial numbers in the port field** -- ``cfg["port"]`` now
  accepts a USB serial number (``"A1B2C3D4"``) in addition to a
  device name, with a ``|``-separated fallback chain:

      "port": "A1B2C3D4|COM3"

  *"Prefer serial number A1B2C3D4; if it's not connected, fall back
  to COM3."*  Stable across replugs, stable across machines, stable
  across reboots.  Resolution happens at every connect, so
  unplug/replug events automatically find the device's new COM
  number when the SN survives.  Composes cleanly with environment
  expansion: ``"$(env.DEVICE_SN)|COM3"`` works.  Ambiguous serial
  numbers (two devices sharing ``"0001"`` from cheap CH340 clones)
  are a hard error naming both colliding devices, not a silent
  guess.

- **Zero-config ``--cli`` mode** -- ``termapy --cli`` with no config
  file used to print ``"no config found"`` and exit.  Now it shows
  a welcome banner listing available ports, names the built-in
  defaults (115200 N81 cr noecho), and hints at ``/port.open
  <name>``.  You can talk to a serial device without ever writing a
  ``.cfg`` file.

- **``/cfg.load <name>``** -- switch configs inside a live CLI
  session.  Pairs with zero-config mode: ``termapy --cli`` ->
  ``/cfg.load myproj`` -> you're in the project.  No more exiting
  and re-running to change configs.

- **``/port.open`` line-ending + echo tokens** --
  ``/port.open COM4 9600 N81 crlf echo`` sets baud + frame mode +
  line ending + echo in one command.  New tokens: ``cr`` / ``lf`` /
  ``crlf`` for line ending, ``echo`` / ``noecho`` for echo.  Port
  name must come first; everything else is order-independent.

- **Uniform folder subcommand family** -- every folder-owning
  command (``/ss``, ``/run``, ``/proto``, ``/cap``, ``/cfg``,
  ``/plugin``, ``/app``) now exposes the same shape:

      /<folder>.list       list files
      /<folder>.explore    open folder in file manager
      /<folder>.show       open newest file in system viewer
      /<folder>.dump       print newest (or named) file to terminal
      /<folder>.clear      delete all files (only on ss, cap, prof)

  Which of show/dump/clear is exposed follows the folder's nature:
  user-authored folders (run/, proto/, plugin/) don't get a
  ``.clear``; machine-generated folders (ss/, cap/, prof/) do.  No
  more guessing whether it's ``.configs`` or ``.dir`` or ``.list``.

### 0.62.0 Improvements

- **Long device responses no longer chop the next prompt** -- a
  device streaming 40+ lines of ``help`` output in ``--cli`` mode
  used to race with prompt_toolkit's prompt redraw and leave the
  prompt overlaid mid-stream.  The interactive loop now runs under
  prompt_toolkit's ``patch_stdout`` so reader-thread output gets
  buffered and inserted above the active prompt instead of
  colliding with it.  The TUI was never affected (it has a
  separate input widget); only ``--cli`` had this bug.

- **``/port <SN>`` status line names the actual device** -- when
  you typed ``/port A1B2C3D4``, the status said ``Port changed to
  A1B2C3D4 (session)`` even though you were actually on COM4.
  Now says ``Port changed to COM4 (session)``, matching the
  already-correct connect banner.

- **``termapy>`` prompt in zero-config** -- previously the CLI
  prompt read ``none>`` when no config was loaded (the ``$(CFG)``
  variable fell back to the literal ``"none"``).  The zero-config
  fallback is now ``"termapy"``, so you see ``termapy>`` instead.

- **Bottom toolbar removed** -- prompt_toolkit was reserving a
  full-width row below the prompt for a help tooltip that never
  populated.  The completion dropdown (still there, via Tab) is
  scaled to terminal height: 8 rows of dropdown on a 40+-line
  terminal, 4 rows on smaller, 0 (no dropdown) below 10 rows.

- **Empty ``"port": ""`` in configs now warns** -- a valid port is
  any of: literal device name, USB serial number, reserved name
  (``"DEMO"``, ``"DEMO_FAIL"``), or pyserial URL.  Empty string is
  invalid and now produces a validation warning on config load.
  The zero-config CLI path is the only legitimate place ``port=""``
  appears; it synthesizes in memory and never persists.

- **CI publish workflow handles PyPI double-upload** --
  ``release_publish.py`` publishes to PyPI from the dev machine,
  then GitHub's ``release:published`` event triggers the Actions
  workflow which tries to upload again.  Previously the second
  upload failed with "file already exists" and the workflow showed
  red on every release.  Now uses ``uv publish --check-url`` which
  detects the already-published file and exits 0.

### Breaking changes

- ``/cfg.configs`` renamed to ``/cfg.list``.
- ``/ss.dir`` removed; use ``/ss.list`` (which actually lists files,
  not just the folder path).
- The ``/cfg.<folder>`` listing subtree is gone --
  ``/cfg.run``, ``/cfg.ss``, ``/cfg.proto``, ``/cfg.cap``,
  ``/cfg.prof``, ``/cfg.plugin``, ``/cfg.viz`` and their
  ``.dump/.show/.explore/.clear`` children.  Use top-level
  ``/run.list``, ``/ss.list``, ``/proto.list``, ``/cap.list``,
  ``/plugin.list``, etc. instead.

## 0.61.0 (2026-04-18)

Quality-of-life release.  Termapy now tells you when a new version is
available on PyPI, consolidates its own app-level state (update checks,
recent configs, window geometry) into a single `state.json` that you
can inspect with the new `/app` command, names the process holding a
locked port on Unix, and makes the command palette a little smarter on
non-Linux platforms.  Under the hood, the `cmd_prefix` override is now
fully plumbed so users with a non-`/` prefix see it everywhere in help
output.

### 0.61.0 New Features

- **PyPI update check + Update title-bar button** -- termapy quietly
  checks PyPI on startup (no network call blocks the UI) and, when a
  newer release is available, lights up an `Update` button in the
  title bar.  Clicking it jumps straight to the installation docs so
  you can see how to upgrade for your install method.  The check runs
  at most once a day and is fully opt-out via `update_check: false`.
- **`/app` command + `state.json`** -- app-wide state (last-seen
  version, recent configs, update-check timestamp, window geometry,
  custom buttons) now lives in a single `state.json` under the
  user-level app directory instead of scattered files.  The new
  `/app` REPL command lets you query or edit any field interactively:
  `/app`, `/app recent`, `/app.set custom_button_4 "/port.info"`, etc.
- **Friendlier "port in use" errors** -- when the OS refuses to open
  a port because another process has it, termapy now reports which
  process on Linux / macOS (via `lsof`).  Instead of a bare
  `PermissionError`, you get `Cannot open /dev/ttyUSB0: permission
  denied (held by arduino, PID 12847)`.
- **Smarter `/` palette** -- the command palette hides Linux-only
  commands (`/port.latency_timer`, `/port.permissions`) on macOS and
  Windows so they don't clutter the view with never-applicable rows.
  Help text is column-aligned at position 50 for a tidier two-column
  look at wider terminal widths.
- **Plugin-source labels in startup status** -- the green startup
  banner now annotates each plugin with its origin (`builtin`,
  `global`, `config`) so you can see at a glance where a custom
  command came from when a config pulls plugins from multiple
  sources.
- **`TERMAPY_DEMO_FLEET` environment variable** -- set this and
  `--ports`, `--watch`, `--info`, and `/port.list` enumerate a
  fixed three-port synthetic fleet (FTDI FT232R on COM3, Silicon
  Labs CP2102 on COM4, Microsoft USB Serial on COM7) instead of
  real hardware.  Handy for docs, screenshots, bug reports, or
  trying the tool before you own an adapter.  Sibling to the
  existing `port: DEMO` (fake open) config value.

### 0.61.0 Improvements

- **`cmd_prefix` override is now complete** -- users with a non-`/`
  prefix in their config (`cmd_prefix: "!"`) previously saw stray
  `/` literals in help output, search hits, subcommand listings,
  and CRC error messages.  Every help-rendering site now runs the
  live prefix through the same interpolation helper, and the plugin
  source strings use a `{prefix}` sentinel that's substituted at
  render time.  A new end-to-end test proves dispatch + output
  both honor the override.
- **Friendlier CRC error messages** -- `/proto.crc.c`,
  `/proto.crc.python`, `/proto.crc.rust`, `/proto.crc.calc`, and
  `/proto.crc.help` now fail gracefully and point you at
  `/proto.crc.list` when you pass an unknown algorithm name
  (previously some paths crashed or leaked the `{prefix}`
  placeholder to the screen).
- **`proto_frame_gap_ms` now has a default** -- previously a fresh
  config was missing this key and `/proto.send` would complain;
  it now defaults to 50 ms so the first thing you try works.
- **Better shutdown hygiene** -- race conditions between the reader
  thread firing a final callback and the Textual widget tree
  tearing down are now caught by a dedicated `SHUTDOWN_RACE` tuple
  instead of a broad `except Exception`; and `open` /
  `xdg-open` child processes are reaped by a daemon thread so they
  don't leak zombies when you close help or a capture file from
  the TUI.
- **Deprecated vs. mistyped config keys** -- the config validator
  now distinguishes keys that *used to be real* (deprecated --
  silently dropped with a yellow hint) from keys that are likely
  typos (flagged in red with a did-you-mean suggestion).
- **Release-tooling improvements** -- the release script now
  asserts HTML help freshness after rebuild so stale `*.html` can't
  slip into a tag, and a new `scripts/check_dep_updates.py` surfaces
  outdated dependencies for pre-release review.

## 0.60.0 (2026-04-17)

Command-line release.  Termapy now has a set of one-shot shell flags --
`--ports`, `--watch`, `--info`, `--chips`, `--check` -- that skip the TUI
entirely and print plain text to stdout, so you can answer "what's plugged
in?" from a shell or script without waiting on Textual.  The flags pay
only pyserial's import cost and start fast.

### 0.60.0 New Features

- **`termapy --ports`** -- one-line-per-port table matching the in-app
  port picker.  Filter to a single port with `--ports=COM4`.  Row width
  adapts to the terminal so narrow windows drop low-priority columns
  (speed / chip / vid_pid) before wrapping.  Physical ports only --
  pyserial URL handlers remain a config-side concern.
- **`termapy --watch`** -- live log of port events; start watching,
  plug the cable in, see which COM number it got.  One-character
  event markers (`+` plug, `-` unplug, `~` chip re-EEPROM, blank for
  baseline and open/close transitions) make plug events punch out
  of the stream visually.  `Ctrl+C` to exit.
- **`termapy --info`** -- full per-port chip dump (same data as
  `/port.chip`), identifies chip model, USB speed class, serial
  number, and VID:PID.  Unknown chips print the VID:PID so you can
  identify them manually and nudge you to file an issue so the
  lookup table can grow.
- **`termapy --chips`** -- dump the USB-serial chip lookup table;
  `--chips=ftdi` filters to substring matches.
- **`termapy --check`** -- validate your config and print a JSON
  status line without launching the TUI.
- **USB manufacturer / serial-number columns in the picker** --
  the port picker and `/port.chip.list` now show USB serial numbers
  and an aliased manufacturer column (FTDI / MSFT / SiLabs) for
  faster disambiguation between identical cables.

### 0.60.0 Improvements

- **Command-Line Flags help page** -- a new page in the docs
  walks through `--ports`, `--watch`, and `--info` with real
  captures and an annotated marker-column legend.
- **Faster CLI startup** -- the entry point and all print-and-exit
  flags have been split into Textual-free modules, so `termapy --ports`
  no longer pays the ~300 ms / 40 MB cost of importing Textual.
  `python -m termapy --help` now answers in stdlib+pyserial time.
- **Cleaner config-path resolution** -- `find_config`, `resolve_config`,
  and `infer_config_from_run_file` moved to their own Textual-free
  module, eliminating a previously-duplicated copy in the CLI.

## 0.59.0 (2026-04-16)

Help-system release.  Every built-in command's `/help` page now opens with a one-line live status, device commands brought in by `/include` become first-class citizens in both `/help` and `/search`, and the device JSON schema picks up an optional version field so firmware updates automatically refresh the cache.

### 0.59.0 New Features

- **Dynamic help** -- `Command.long_help` (and `PluginInfo.long_help`) now accepts a callable `(PluginContext) -> str` as well as a static string. Callables are invoked at render time so a command's DESCRIPTION section can reflect live runtime state (loaded files, current connection, cached counts). Existing static-string declarations are unchanged. See `writing-plugins.md` for the pattern.
- **Dynamic help on built-in commands** -- `/help` for `/cfg`, `/port` and each port subcommand, `/include`, `/cap.*`, `/ss`, `/run.edit`, `/proto` (+ subcommands), `/edit.run|proto|plugin`, `/var`, `/env`, and `/seq` now opens with a green single-line status reflecting the current state (active cfg name, connected port, counter count, file counts in the relevant folder, etc.). Single-value commands show only the state line; multi-option commands keep their existing prose.
- **Dynamic-help helpers** -- new `termapy.help_dynamic` module supplies small reusable building blocks (`state_line`, `folder_line`, `port_status`, `cfg_status`, `ns_count`, `compose`, `green`) so plugin authors can wire dynamic DESCRIPTION content in one or two lines. See `writing-plugins.md`.
- **Target-command parity** -- `/include`'d device commands now support optional `long_help` and `flags` fields in their JSON. `/help <target>` renders a full man page (NAME, SYNOPSIS, DESCRIPTION, FLAGS, source marker) when those fields are present. `/search` indexes target commands alongside REPL plugins, tagging device-command hits with `(target)` so they're visually distinct. The demo device's `AT+HELP.JSON` uses the new fields on `AT+INFO`, `AT+TEMP`, `AT+LED`, and `AT+BINDUMP` so the feature is live out of the box in `--demo`.
- **Device schema versioning** -- `/include` JSON may carry an optional top-level `version` string (device's own schema version, not termapy's). On fetch, if the device's version is strictly newer than the cached one (PEP 440 compare), the cache is overwritten; otherwise the cache is kept. First-time fetches with no cache always apply. `/include.reload` still bypasses the gate for manual refresh. The demo device advertises `"version": "1.0.0"` so the round-trip is exercised end-to-end.

### 0.59.0 Changes

- **`/help` is now case-sensitive on exact match** -- the argument is no longer lowercased before lookup. Plugin names (conventionally lowercase) and device AT commands (conventionally upper) both match exactly. Typing `/help INCLUDE` used to silently lowercase and hit `/include`; it now falls through to the forgiving candidate list (which is still case-insensitive internally), where `/include` surfaces as a candidate. This is the change that makes `/help AT+INFO` work for included device commands.

## 0.58.0 (2026-04-15)

### 0.58.0 New Features

- **Forgiving `/help`** -- `/help` now does substring matching across command names, flags, args, and help text when there is no exact match. `/help crc` surfaces every CRC-related command; `/help dev` finds `/help.dev` and `/help.search --dev`. Results group under headings (Command Name, Flags, Help String, Arguments, Long Help) so you can see *why* each command matched, and the matched terms are underlined throughout.
- **Multi-term `/help` search** -- combine terms to narrow down in a sea of commands:
  - `/help table crc` -- commands matching both words
  - `/help crc -table` -- CRC commands, excluding the `--table` codegen ones
  - `/help --table` -- literal flag lookup

  Long-help hits render the full long_help inline so you don't need a second command.
- **`/help.search <pattern>`** -- regex search across every command. Pass `--dev` to also search handler docstrings. Returns matching command names as `CmdResult.value` so scripts can capture the list with `$(VAR) <- /help.search ...`.
- **First-class flags** -- plugin commands now declare their flags on a `Command(flags={...})` dict. The dispatcher parses and strips declared flags before the handler runs; handlers read them via `ctx.flag("--name")`. Benefits you will notice:
  - Flags appear under their own **Flags:** section in `/help <cmd>` output
  - `/help table` finds `--table` directly
  - Typos like `--tablet` get a `did you mean --table?` suggestion instead of being silently ignored

  - Short aliases work uniformly: `/run foo.run -v` and `/run foo.run --verbose` both set the same flag
- **`/cap.poll`** -- new command for polling one or more device commands on a schedule, streaming the responses to a CSV or JSONL file. Supports `count=`, `delay=`, `file=`, `labels=`, `regex=` extraction, and `--overwrite` / `--notime` flags.
- **Network serial ports** -- pyserial URL-style ports (`loop://`, `socket://<host>:<port>`, `rfc2217://...`) are now accepted wherever a port name would go.
- **`CmdResult.value` for scripting** -- plugin handlers can now return a captured value that scripts read back with `$(VAR) <- /command`. Enables composing command output into variables without parsing terminal text.
- **`/cap.poll --table` CRC codegen** -- `/proto.crc.{c,python,rust}` now ship a worked bit-by-bit vs table-driven example in the help pages, so you can see what the generated code looks like for either form.

### 0.58.0 Improvements

- **Getting Started** page gains a concise "finding commands" section listing the `/help` search forms.
- **`/cfg.info --display`**, **`/cap.poll --overwrite / --notime`**, **`/help.search --dev`**, **`/run --verbose`** all converted to first-class flags. Behavior unchanged; discoverability improved.
- **`long_help`** text added for `/cap.text`, `/cap.bin`, `/ping`, `/ping.quiet`, `/repeat` so `/help <cmd>` actually explains what they do.
- **Config help examples validated** -- README and `config.md` examples are now checked at release time to make sure they match the real config schema.

### 0.58.0 Bug Fixes

- **`/cap.poll` JSONL output** -- fixed two type-checker errors that could surface when writing JSON Lines capture output with the `--notime` flag. No behavior change.

## 0.57.0 (2026-04-12)

### 0.57.0 New Features

- **Custom baud rates** -- new `custom_baud` config option unlocks non-standard baud rates (>= 300) for hardware that needs them. By default only standard rates are accepted to catch typos that silently break connections. QuickSetup dialog adds a toggle button to switch between standard list and custom entry. Config editor blocks save on non-standard rates unless `custom_baud` is enabled.
- **TUI-only command warnings** -- CLI now shows "Only available in /tui mode" for commands that require the TUI (screenshots, line numbers, proto debug) instead of silently doing nothing.

### 0.57.0 Improvements

- **Serial engine decoupling** -- DTR/RTS/break toggle, reconnect loop, and hardware signal queries moved from the TUI into SerialEngine. All serial operations now go through the engine rather than touching pyserial directly.
- **TerminalHost base class** -- connect/disconnect lifecycle, serial helpers, capture proto_active handling, port switching, and context callbacks consolidated in the shared base class, removing duplication between TUI and CLI.
- **New Config dialog** -- first serial port selected by default, Connect button disabled with "No Ports" label when no ports are available, title case labels.
- **PyPI publish** -- release_publish.py now builds and uploads to PyPI as part of the release flow.

## 0.56.0 (2026-04-12)

Architecture and CLI experience release. Extracts a shared TerminalHost base class from the TUI and CLI frontends, replaces pyreadline3 with prompt_toolkit for cross-platform CLI intellisense, and adds serial observer hooks, plugin config persistence, and status bar APIs for plugin authors.

### 0.56.0 New Features

- **CLI intellisense** -- tab completion, fish-style auto-suggest from history, and a help toolbar that shows command docs as you type. Toggle with `/cli.intellisense on|off` or the `cli_intellisense` config key.
- **Serial observers** -- plugins can register RX/TX observer callbacks to monitor serial traffic without disrupting the display pipeline.
- **Status bar API** -- `ctx.status_bar(text)` shows transient text in the TUI bottom bar (no-op in CLI).
- **PluginConfig API** -- persistent per-config key-value storage for plugins (`ctx.plugin_cfg("name")`), backed by JSON files in the plugin/ folder.
- **PIC map plugin** -- parse Microchip PIC memory map files and look up symbols by address.
- **Port mode command** -- `/port.open` accepts baud+mode args, `/port.mode` sets serial parameters inline.
- **Lifecycle hooks** -- new `on_app_start`, `on_app_stop` hooks for plugin initialization and cleanup.

### 0.56.0 Improvements

- **TerminalHost extraction** -- shared base class for TUI and CLI consolidates serial I/O, dispatch, capture, context wiring, and hook handlers. ~240 lines of duplicated code removed, 27 callbacks written once.
- **CLI scrolling fixed** -- terminal output now scrolls correctly at the bottom of the window on all Windows console hosts.
- **Shared history** -- TUI and CLI modes use the same history file per config.
- **Test coverage boost** -- 68 new tests covering terminal_host.py (92%), repl.py (89%), overall 66% to 70%.
- **Type checker clean** -- all `ty check` overrides resolved.
- **Port in-use detection** -- correctly identifies when termapy itself has a port open vs another process.

## 0.55.0 (2026-04-09)

Serial-port diagnostics release.  Termapy now identifies the USB chip behind any serial port -- model, USB speed class, driver, latency timer, max baud, in-use state, permissions, VID:PID -- and surfaces the data through the REPL, the title bar, the port picker, the new-config dialog, and a new shell flag.  No breaking changes.

### 0.55.0 New Features

- **`/port.chip` and `/port.chip.<field>` REPL commands** -- 16 single-field subcommands (model, usb_speed, driver, latency_timer, max_baud, permissions, in_use, vid_pid, serial, negotiated, ...) accepting an optional port name or `*` for every connected port.  Backed by a `USB_SERIAL_CHIPS` lookup table covering FTDI, Silicon Labs, WCH, Prolific, and MCU native USB.
- **`/port.info` rewritten as a unified full report** -- configured serial parameters, the complete chip field set, and live DTR/RTS/DSR/CTS signal lines for the currently-connected port.  Rejects a port argument with a pointer to `/port.chip`.
- **`termapy --info[=PORT]` shell flag** -- one-shot chip diagnostics without launching the TUI.  `termapy --info` lists every connected port; `termapy --info=COM4` targets one; exit 0 on success, 1 on error.  Pipe-friendly for shell scripts and CI.
- **Column-aligned port picker** -- PORT / MANUFACTURER / DESCRIPTION / CHIP / SPEED / VID:PID with header and separator rows, data-driven column widths, and shrink-on-overflow (description first, chip second).  The new-config QuickSetup dialog uses the same helpers so its port list is byte-for-byte identical.
- **Expanded QuickSetup baud rate list** -- 300 through 3,000,000, covering the full span from legacy serial devices to FTDI H-series at 12 Mbaud.
- **`help/ports.md` -- a new serial-ports help page** -- 10-second happy path (plug in, pick a port, 115200 8N1, connect) followed by scenario-based troubleshooting: multiple cables, Linux+FTDI latency tuning, Full-Speed vs High-Speed cable purchasing, unknown-chip lookups, "in use" diagnosis, and Linux permission-denied recovery.  Cross-linked from the toolbar page and index.

### 0.55.0 Improvements

- **Unified title-bar tooltips** -- the Cfg, Port, and Connection buttons all use the same three-section format (title / aligned `key = value` body / "Click to: action" line) via a shared `_format_title_tooltip` helper.  Consistent visuals, no more bracket-markup rendering glitches.
- **`In use` detection that actually works on Windows** -- `_check_in_use()` now tries to open the port (and close it immediately) rather than returning `unknown`.
- **Windows FTDI latency-timer hint** -- reports `Latency timer: n/a (Windows - check Device Manager)` for FTDI cables on Windows, nudging users toward where the setting lives instead of a bare `n/a`.
- **Port picker screenshot added** -- `help/img/com_port_select.png` in `help/ports.md` shows what the picker looks like with real chip data.
- **New-config screenshot added** -- `help/img/new_cfg.png` in `help/config.md` under a new "Creating a new config" section documents the QuickSetup first-run flow.
- **Windows FTDI latency-timer screenshot added** -- `help/img/latency_picker.png` in `help/ports.md` shows the Device Manager Advanced port-settings dialog for the tuning walkthrough.

## 0.54.1 (2026-04-08)

Type-check cleanup release.  Brings `uvx ty check src/termapy/` from 370 diagnostics down to 0 while fixing a handful of real correctness issues surfaced by the audit.  No user-visible behavior changes.

### 0.54.1 Improvements

- **Zero ty diagnostics in first-party code** -- a mix of ~8 genuine fixes (narrowing, widening function signatures to `Mapping`, a discriminated-union return type for `load_proto_script`, fixing `QueueByteReader.getc`'s `timeout` annotation from `int` to `float` to match what ymodem actually passes) and ~4 well-documented `# ty: ignore` comments for things the type checker can't see through (POSIX-only typeshed stubs for `readline`/`sys.stdout.reconfigure` on Windows, Textual's heavily-generic `push_screen` overloads, an optional `textual_serve` import, and a cross-variable runtime invariant in `proto_debug`).
- **`pyproject.toml` excludes vendored pyserial from ty** -- the vendored tree references Linux-only modules (`fcntl`, `termios`, `select.POLL*`) in its platform shims, producing ~340 false-positive diagnostics on Windows.  `[tool.ty.src]` now excludes it; `extra-paths` in `[tool.ty.environment]` still makes `import serial` resolve from first-party code.
- **`load_proto_script` has a discriminated-union return type** -- `tuple[Literal["toml"], ProtoScript] | tuple[Literal["flat"], tuple[dict, list[Step]]]` instead of the previous untagged union.  Callers that branch on `result[0]` now get `result[1]` narrowed automatically without `# type: ignore` workarounds.
- **ty badge in the README, auto-updated by `release_prep.py`** -- the release prep script now counts diagnostics from `uvx ty check src/termapy/` and bumps the badge alongside the existing test-count automation.  Thresholds: 0-9 green, 10-19 yellow, 20+ red.  Drift is visible on every release without manual inspection.

### 0.54.1 Fixes

- **`QueueByteReader.getc` `timeout` parameter widened from `int` to `float`** -- the body always did float arithmetic (`deadline = time.monotonic() + timeout`, `min(remaining, 0.05)`) and `ymodem_xfer._read` passes fractional-second timeouts through it, but the `int` annotation was wrong.  No runtime change; just the annotation catching up to reality.
- **`CliEngine._run_script_mode` takes the script path as a parameter** -- previously it read `self.run_script` (typed `str | None`) and passed it to `Path()`, which is correct at runtime because the only caller guards with `if self.run_script:` first.  Making the parameter explicit moves the guarantee into the method signature rather than an implicit runtime invariant.

## 0.54.0 (2026-04-08)

Plugin API release. Two new primitives let plugins own their own session state and lifecycle, and five built-ins (seq, target_commands, echo, hex_mode, verbose) migrate onto them as worked examples. `EngineAPI` is now a legible escape hatch containing only Textual/threading/serial handles - anything that could live in a plain dict has been moved out.

### 0.54.0 New Features

- **`ctx.ns(name)` namespace primitive** -- plugins get a sanctioned, session-scoped `dict` for their state instead of monkeypatching `ctx` or using module globals. Lazy creation on first access, same dict returned on every call with the same name, flat visibility (any plugin can read any namespace) so cooperating plugins can share state on purpose. See `ARCHITECTURE.md` for the full pattern.
- **Plugin lifecycle hooks** -- plugins can now declare `on_app_start`, `on_app_stop`, `on_script_start`, and `on_script_stop` as top-level functions in their module. The loader discovers them alongside `COMMAND`/`TRANSFORM`/`DIRECTIVE`. Exceptions are caught per-hook so one bad plugin cannot block others. Script hooks fire only at the outermost script boundary; nested `/run` does not re-fire. No base class, no decorators - a plugin is still a module that exports stuff.
- **`flags` namespace** -- engine-reserved namespace holding `echo`, `verbose`, and `hex_mode`. Defaults are seeded once in `_build_context` and read sites use bare key access, so a missing key is a construction bug not silent drift. Third-party plugins should use their own namespace name to avoid collision.

### 0.54.0 Improvements

- **`seq` plugin is now self-contained** -- sequence counters and the `{starttime}` timestamp live in `ctx.ns("seq")`, and the plugin wires its own `on_app_start`/`on_script_start` hooks to seed and reset state. `ReplEngine` no longer has `_seq_counters`, `_seq_start_time`, or `_reset_seq`. A reader can now open `seq.py` top-to-bottom and understand every aspect of sequence-counter behavior in one file.
- **Nested `/run` no longer resets counters** -- previously, a nested script would blow away its parent's sequence counters because `start_script` cleared them unconditionally. Now the reset happens in the seq plugin's `on_script_start` hook, which only fires at the outermost script boundary. Inner scripts inherit their parent's counters, which is the less surprising behavior.
- **`EngineAPI` class docstring rewritten** -- reframed as a privileged escape hatch for Textual/threading/pyserial handles rather than a general plugin surface. The field list is now the set of things that genuinely must remain frontend-coupled; anything that could live in a plain dict has been migrated off.
- **CLI install instructions restructured** -- README leads with uv as the preferred package manager, breaks install into numbered steps with time estimates (under a minute from scratch), and simplifies the CLI-mode quickstart to `termapy --cli --demo` so readers can try it without a config.

### 0.54.0 Fixes

- **`/echo on` now actually works in CLI mode** -- previously the CLI hardcoded `get_echo=lambda: False` and `set_echo=lambda val: None` in its `EngineAPI` wiring, so `/echo on` silently did nothing and the plugin reported `off` regardless. After the namespace migration the toggle honestly flips the flag and REPL commands echo when requested, matching TUI behavior. The CLI still defaults to echo off (readline shows input) but users can now turn it on for script debugging.

### 0.54.0 Notes

- **No user-facing config changes.** All three migrated flags (`echo`, `verbose`, `hex_mode`) still read their defaults from the same places they always did.
- **`EngineAPI` lost 11 fields** (`get_echo`/`set_echo`, `get_seq_counters`/`set_seq_counters`/`reset_seq`, `target_commands`/`set_target_commands`/`clear_target_commands`, `get_hex_mode`/`set_hex_mode`, plus `PluginContext.verbose`). External plugins that reached into these via `ctx.engine` will need to migrate to `ctx.ns()`. External plugins that were already using only the public `PluginContext` surface are unaffected.

## 0.53.3 (2026-04-07)

Bug fix and tooling release.

### 0.53.3 Fixes

- **`/cap.bin` help text** -- shortened so the rendered line stays under 120 columns. The previous text wrapped at slightly different points across Rich versions, causing the CLI gold test to fail intermittently under tox.

### 0.53.3 Improvements

- **Forensic dump on CLI gold failure** -- when `tests/test_cli_gold.py` fails it now writes raw output, normalized output, expected output, and a unified diff to `tests/cli_gold/_failures/` so intermittent failures leave evidence behind.
- **Numbered step progress in `release_prep.py`** -- `[1/10] ... [10/10]` headers so a failed prep tells you exactly which stage died.

## 0.53.2 (2026-04-07)

Documentation release.

### 0.53.2 Improvements

- **`scripts/README.md`** -- documents the release automation workflow (prep, manual CHANGELOG edit, publish), enforced conventions, and recovery steps for a failed prep run.

## 0.53.1 (2026-04-07)

Maintenance release. No user-facing changes to the terminal itself.

### 0.53.1 Improvements

- **Release automation** -- new `scripts/release_prep.py` and `scripts/release_publish.py` handle version bumps, doc count updates, CHANGELOG stub generation, test runs, HTML rebuild, tagging, and GitHub release creation. Stdlib only, two-stage with a manual review checkpoint, no force pushes, no auto-merge without `--yes`. This release is the first one cut with them.

## 0.53.0 (2026-04-07)

### 0.53.0 New Features

- **Universal `.quiet` modifier** -- any command can now be invoked as `<cmd>.quiet` to suppress its terminal output, handled by a single dispatcher in `repl.py` instead of per-plugin subcommands. `/echo.quiet`, `/verbose.quiet`, and so on are all routed through the same mechanism.
- **`/expect quiet=on` keyword** -- suppress per-step status from a single `/expect` line in scripts.
- **`parse_bool` shared helper** -- all plugins now accept the same boolean tokens (on/off/1/0/true/false/yes/no) via a single helper.
- **On AI assistance** -- new help page (`on-ai-assistance.md`) documenting how termapy was built with Claude and the role tests play in that workflow.
- **Quiet mode and output channels** -- new section in `scripting.md` documenting the result/output/status channels and how to silence each.

### 0.53.0 Improvements

- **Voice and style pass** -- removed em dashes from prose, normalized headings to sentence case across README, ARCHITECTURE.md, COMPARISON.md, reddit_post.md, and all 17 help pages. No content removed; rationale for the plugin system, CRC catalogue, demo, and CLI surfaced where readers will see it.
- **Plugin help strings** -- `/echo` and `/verbose` advertise their `.quiet` variant inline instead of as a subcommand listing.
- **README cross-platform pitch** -- "runs on Windows, macOS, Linux; under 10 seconds with uv" surfaced in the intro.

## 0.52.0 (2026-04-06)

### 0.52.0 New Features

- **`/repeat` command** -- `/repeat count=<N> {delay=<dur>} {var=<name>} cmd=<command>` repeats a command N times with optional delay. Sets iteration variable (default `REPEAT_N`). Stoppable via Escape key or `/stop`.
- **sum8/sum16 CRC tests** -- 18 tests for built-in checksum modules.

### 0.52.0 Improvements

- **Vendored dependencies** -- pyserial (3.5), xmodem (0.4.7), and ymodem (1.5.3) are now bundled. Eliminates 4 install-time packages (pyserial, xmodem, ymodem, ordered-set).
- **Trimmed tree-sitter** -- only json, toml, and bash grammars installed (was 16). Drops 13 unused grammar packages.
- **textual-serve optional** -- `--web` mode requires `pip install termapy[web]`. Clear error message if missing.
- **Runtime dependencies reduced** -- ~46 packages down to ~16.
- **Assert messages** -- ~1200 test asserts now include descriptive failure messages.
- **"Who This Is Not For"** -- README section for honest expectation-setting.
- **CHANGELOG headings** -- version-prefixed sub-headings fix MD024 duplicate warnings.

## 0.51.0 (2026-04-05)

- **Web mode (experimental)** -- `termapy --web` serves the TUI in a browser via textual-serve. Use `--web-port` to customize the port.

## 0.50.0 (2026-04-05)

- **Fix help page loading** -- Removed `navigation.instant` feature that caused blank pages on localhost server.

## 0.49.0 (2026-04-05)

- **Local help server** -- Help docs served via localhost HTTP server instead of file:// URLs. Zensical search and navigation work correctly.
- **Fully offline help** -- Disabled Google Fonts; all HTML/CSS/JS is bundled with no network requests.

## 0.48.0 (2026-04-05)

- **Published to PyPI** -- `pip install termapy` or `uv tool install termapy`.
- PyPI metadata: authors, classifiers, keywords, project URLs.
- GitHub Actions workflow for automated publishing on release.

## 0.47.0 (2026-04-05)

### 0.47.0 New Features

- **`/help.run`** -- List available .run scripts with descriptions extracted from header comments.
- **`/help.plugin`** -- List loaded plugins grouped by source (application, built-in, user).
- **Scripts section in `/help`** -- Bare `/help` now shows available scripts alongside commands and target device help.
- **Script description convention** -- First line `# comment` followed by blank line is extracted as help text.

## 0.46.0 (2026-04-05)

### 0.46.0 New Features

- **Demo virtual filesystem** -- `AT+FS.LIST`, `AT+FS.INFO`, `AT+FS.DELETE` commands with 3 pre-loaded files for testing file transfers without hardware.
- **Transfer root directory** -- `/xfer.root {path}` command to show or set `file_xfer_root` config key. Both XMODEM and YMODEM resolve relative paths against this directory.
- **Escape cancels transfers** -- press Esc during XMODEM or YMODEM transfer to abort immediately.

### 0.46.0 Improvements

- **Unified dispatch threading** -- all user commands (interactive, buttons, sequences, pickers) now run on background threads, preventing UI freezes from blocking handlers.
- **XMODEM progress** -- shows packet count and byte count, deduplicates repeated lines.
- **XMODEM recv padding** -- strips trailing 0x1A padding bytes so text files open cleanly.
- **Full paths in transfer output** -- completion messages show the full resolved file path.
- **Config migration v10->v11** -- adds `file_xfer_root` key automatically.
- **Transfer root in /port.info** -- shows the configured transfer directory.
- **17 new tests** -- VFS (15) and migration (2), total 1191 tests.

## 0.45.0 (2026-04-04)

### 0.45.0 New Features

- **XMODEM file transfer** -- `/xmodem.send` and `/xmodem.recv` for sending and receiving files over serial using the XMODEM protocol. Automatic CRC/checksum negotiation, 128-byte blocks, progress reporting.
- **YMODEM file transfer** -- `/ymodem.send` and `/ymodem.recv` with batch transfer, 1024-byte blocks, and automatic filename/size metadata. Send multiple files in one session.
- **Demo device XMODEM/YMODEM** -- `AT+XMODEM=SEND/RECV` and `AT+YMODEM=SEND/RECV` in demo mode for testing file transfer without hardware.

### 0.45.0 Improvements

- **File transfer help page** -- new documentation page covering both protocols with comparison table and workflow examples.
- **32 new tests** -- 23 XMODEM tests (QueueByteReader, protocol, library integration) and 9 YMODEM tests.

## 0.44.0 (2026-04-03)

### 0.44.0 New Features

- **Silent screenshots** -- `/ss.svg.quiet` for doc automation; `.quiet` echo suppression works with any command
- **Documentation screenshots** -- `doc_screenshots.run` script generates 10 documentation SVGs from demo mode

### 0.44.0 Improvements

- **Help restructured** -- Installation=onboarding, Getting Started=real device, Demo Mode=reference. Quick Setup dialog documented with screenshot
- **11 documentation images** added across 6 help pages
- **Config keys** -- `hex_mode` and `show_line_numbers` are now config keys (reset on config switch)
- **Target commands** cleared on config switch when no `device_json_cmd`
- **`/help` shows top-level only** with subcommand count
- **CLI echo permanently off** -- readline shows input, scripts can't override
- **`delay.quiet`** suppresses output in scripts
- **Lint fixes** -- walrus operator, unused vars, Any typing, return types, @staticmethod, unused imports
- **Removed mkdocs-material** dev dependency (using `uvx zensical build`)

## 0.43.0 (2026-04-02)

### 0.43.0 New Features

- **CRC code generation** -- `/proto.crc.c`, `/proto.crc.python`, `/proto.crc.rust` generate standalone CRC functions from any of the 62 catalogue algorithms. Use `--table` for table-driven implementation. Both bit-by-bit and table-driven Python output verified against all catalogue check values.

## 0.42.0 (2026-04-02)

### 0.42.0 New Features

- **Inline delays in `/proto.send`** -- `~duration` syntax inserts timing gaps between data segments (e.g. `/proto.send 00 ~25ms "AT\r"`). Supports `us`, `ms`, `s` units. Delays under 1ms use spin-wait for precision.
- **Microsecond durations** -- `parse_duration()` now supports `us` unit throughout the app.

### 0.42.0 Improvements

- **TX/RX display** -- `/proto.send` now shows both hex and smart text for all packets. Inline delays shown as cyan hex + dim text markers.
- **Help docs split** -- "Serial Tools" (interactive send, CRC, hex mode) and "Protocol Testing" (scripts, visualizers, format specs) are now separate help pages with cross-links.
- **`\n` command separator fix** -- literal `\n` in user input no longer splits commands. Only custom button commands support `\n` as a separator. Fixes `/proto.send "text\n"` being split into two commands.
- **Input clears before execution** -- command input box clears immediately and shows "running..." during long-running commands.
- **Help formatting** -- fixed 25/25 column widths, script-only commands section, deduplicated target device rendering, button dispatch fix.
- **AT+HELP.JSON** -- demo device now lists the help command in its own JSON descriptor.

## 0.41.0 (2026-04-01)

### 0.41.0 New Features

- **Quick setup dialog** -- new config creation uses a single dialog with port picker and baud rate selector instead of multi-step flow. Auto-connects after setup.
- **CFG.\* path variables** -- 15 new context variables (`$(CFG.DIR)`, `$(CFG.FILE)`, `$(CFG.PORT)`, `$(CFG.BAUD)`, `$(CFG.PORT_FULL)`, etc.) for use in scripts and commands
- **Config directory precedence** -- `--cfg-dir` flag > `TERMAPY_CFG_DIR` env var > `./termapy_cfg` (if present, never auto-created) > OS default (`%APPDATA%\termapy`, `~/.config/termapy`, `~/Library/Application Support/termapy`)
- **Dot syntax in variables** -- `$(NAME.SUB)` supported (e.g. `$(CFG.PORT_FULL)`)

### 0.41.0 Improvements

- **Resolved paths everywhere** -- all error messages and config info show fully resolved absolute paths
- **Config info verbose-only** -- config dir/file/log paths shown only with `/verbose on`
- **Clear screen on config switch** -- clean slate when loading a new config
- **Focus command input on startup** -- input field gets focus automatically
- **Dotfiles filtered from pickers** -- config/script/proto pickers hide files starting with `.`
- **Clickable paths** -- documented in getting-started help

### 0.41.0 Bug Fixes

- **Plugin module duplication** -- builtin plugins loaded via `importlib` now share module state with package imports, fixing `$(CFG)` not expanding and `FRONT_END` showing as `unknown`

## 0.40.0 (2026-03-31)

### 0.40.0 New Features

- **Reconnect spinner** -- auto-reconnect now shows an animated spinner with amber title bar; click to cancel
- **Connection tooltip** -- title bar shows auto-connect/auto-reconnect status on hover

### 0.40.0 Improvements

- **CLI unit tests** -- 45 tests for CLITerminal (0% -> 53% coverage)
- **defaults.py tests** -- 29 tests (26% -> 97% coverage)
- **Standardized error messages** -- "Not connected." consistent across app, CLI, REPL, port control, and plugins
- **Unified file counting** -- `_count_files()` helper with `FOLDER_PATTERNS` from `folders.py`
- **`on_mount()` refactored** -- broken into `_setup_vars()`, `_build_context()`, `_register_tui_hooks()`, `_load_plugins()`, `_run_startup()`
- **`on_button_pressed()` refactored** -- 89-line if-elif chain replaced with `_BUTTON_DISPATCH` dict routing to 17 named handlers
- **`_serial_op()` helper** -- centralizes serial try/except pattern for DTR, RTS, Break, and send
- **`_sync_all_buttons()`** -- single call replaces 5 scattered sync calls after config switch
- **Protocol module split** -- CRC engine extracted to `protocol_crc.py` (292 lines), visualizer loader to `protocol_viz.py` (132 lines); `protocol.py` reduced from 1,770 to 1,370 lines
- **CI documented** -- README now describes GitHub Actions pipeline (test matrix, coverage, audit)
- **Rename `/import` to `/include`** -- avoids Python keyword collision

### 0.40.0 Bug Fixes

- **`/include` (was `/import`)** -- renamed to avoid Python keyword collision causing import issues

## 0.39.3 (2026-03-31)

### 0.39.3 Bug Fixes

- **Circular import on Python 3.11-3.13** -- lazy import of `var` in `app.py`, add `from __future__ import annotations` to `plugins.py`
- **CI audit job** -- fix `pip-audit` flag syntax, remove `continue-on-error` so security vulnerabilities fail the build

### 0.39.3 Security

- **Pygments >= 2.20.0** -- pin to fix ReDoS vulnerability (CVE in GUID lexer regex)

## 0.39.0 (2026-03-31)

### 0.39.0 New Features

- **`/import` command** -- fetch device command help from JSON over serial; auto-import on connect when `device_json_cmd` is configured
- **`/help.target`** -- show only imported target device commands
- **`/import.reload`** -- force re-import from device, ignoring cache
- **`/import.dump`** -- pretty-print imported commands as JSON
- **`/import.list`** -- list imported commands with args
- **`/import.clear`** -- remove imported commands and delete cache
- **Device help integration** -- new help page documenting how to add a JSON help command to your firmware
- **Disk caching** -- imported commands saved to `.target_menu.json` for instant reload on restart

### 0.39.0 Improvements

- **Rewrite using-git.md** -- simplified help page focused on env vars and .gitignore
- **CLI: no bare print()** -- all output routed through `_raw()` / `_err()` methods for consistent handling
- **`/proto.send` verbose control** -- CRC info, TX bytes, and timing are now verbose-only; quiet mode shows only RX
- **Better error messages** -- friendly serial open errors, config editor live JSON validation, edit-distance command suggestions
- **`repl.cmd()` helper** -- avoids hardcoded command prefix in code
- **Demo device** -- `AT+HELP.JSON` replaces text `HELP` command; GPS commands included in JSON export

### 0.39.0 Config

- **`device_json_cmd`** -- new config key for the serial command that returns device help JSON

## 0.38.1 (2026-03-30)

### 0.38.1 Bug Fixes

- **Circular import on CI** -- CmdResult re-export from scripting.py caused import cycle on clean Python 3.11 installs
- **Toolbar buttons table** -- missing separator row broke table rendering in help docs

## 0.38.0 (2026-03-30)

### 0.38.0 New Features

- **`/log.clear`** -- delete the session log file (TUI + CLI)
- **Variables help page** -- new dedicated docs page for `$(NAME)` syntax, built-ins, env vars, sequences
- **`/edit.run`, `/edit.proto`, `/edit.plugin` without args** -- lists available files instead of showing usage error

### 0.38.0 Improvements

- **`CmdResult` moved to `plugins.py`** -- plugins import from a single module (`from termapy.plugins import CmdResult, Command`)
- **`CmdResult.err_prefix`** -- `ClassVar` for global error prefix customization
- **CLI context dirs fixed** -- `ctx.scripts_dir`, `ctx.proto_dir`, etc. were defaulting to cwd instead of config dir
- **CLI hint ordering** -- "Type commands..." appears before `on_connect_cmd` output
- **Docs coverage** -- custom button JSON example, UI modes, script profiling, `/demo` commands, missing config keys

## 0.37.0 (2026-03-29)

### 0.37.0 New Features

- **Mode switching** -- `/tui` and `/cli` commands switch between TUI and CLI modes
- **`$(CFG)` variable** -- context variable resolves to current config name, usable in prompts
- **`default_ui` config** -- choose default launch mode (`tui` or `cli`), `--cli` flag overrides
- **Unified echo** -- single `_echo_cmd` function for both REPL and serial command echo
- **CLI `on_connect_cmd`** -- CLI now runs startup commands after connecting (was TUI-only)

### 0.37.0 Improvements

- **`CmdResult.err_msg`** -- consistent "Error: " prefix on all error messages
- **run_script refactored** -- `ScriptCtx`, `BLOCKING_COMMANDS` dispatch table, context manager
- **`start_script` returns `(Path, CmdResult)`** -- no more double error messages
- **TUI title tooltip** -- full connection info, features, config path on hover
- **`echo_input_fmt` supports `$(CFG)`** -- prompt shows config name in both modes
- **Config migration v9->v10** -- adds `default_ui`
- **`/ss` and `/grep` CLI errors** -- proper `CmdResult.fail()` instead of yellow warnings
- **Sub-millisecond timing** -- 6-digit precision when `< 0.001s`

### 0.37.0 Bug Fixes

- **No more double error on missing script** -- `start_script` owns error display
- **`/tui` in TUI and `/cli` in CLI** -- no-op instead of unknown command error
- **Demo table in help** -- missing separator row and column alignment fixed
- **Unused `json` import** -- removed from repl.py

## 0.36.0 (2026-03-29)

### 0.36.0 New Features

- **Output channels** -- `ctx.result()`, `ctx.output()`, `ctx.status()` for structured output
- **`/verbose` toggle** -- suppress status messages with `/verbose off`
- **`CmdResult.value`** -- commands return programmatic values (e.g. `/ver` returns `"0.36.0"`)
- **`folders.py`** -- single source of truth for folder names, patterns, and capabilities
- **`connection_string()`** -- centralized formatting with hardware signals (DTR/RTS/CTS/DSR/RI/CD)
- **`/ping` built-in** -- serial response timing with `serial_io` for accuracy
- **`/clr`** -- alias for `/cls`
- **`/raw`**, **`/help.open`** -- now available in CLI mode
- **`/demo`**, **`/demo.force`** -- work in CLI mode
- **`$(FRONT_END)`** -- launch variable (`cli` or `textual`)
- **`cli_prompt`** -- configurable CLI prompt (default `"> "`)
- **`cli_echo_input`** -- control serial echo in CLI (default off)

### 0.36.0 Improvements

- **Folder renames** -- `scripts/` -> `run/`, `plugins/` -> `plugin/` with auto-migration
- **All handlers return `CmdResult`** -- structured success/failure with timing
- **`/var` output colorized** -- cyan names, green values
- **`/cfg <key>` returns clean value** -- `115200` not `baud_rate: 115200`
- **`/echo`, `/verbose`, `/show_line_endings` return on/off** -- clean programmatic values
- **`/env` root handler** -- lists variables (same as `/env.list`)
- **CLI echo off** -- readline shows input, no redundant echo
- **`wait_for_idle` only for serial commands** -- scripts with `/print` run instantly
- **Sub-millisecond timing** -- shows 6 digits when `< 0.001s`
- **CLI `wait_for_idle` 20ms** -- test suite 43s -> 21s
- **`--no-ff` merges** -- preserve branch history in git graph

### 0.36.0 Bug Fixes

- **CLI `serial_send`/`serial_claim` not wired** -- now available
- **CLI `/cls` was no-op** -- now clears terminal
- **`/port.info` RI/CD alignment** -- fixed column spacing
- **Null handler guard** -- parent commands with no root handler no longer crash

## 0.35.0 (2026-03-29)

### 0.35.0 New Features

- **`CmdResult` dataclass** -- all plugin/hook handlers return structured success/failure with error messages and elapsed time
- **`/ping` built-in command** -- measure serial response time with `serial_io` for accurate first-byte timing
- **`/ping.quiet`** -- suppresses device response output
- **`/run.profile` in CLI mode** -- script profiling now works in both TUI and CLI
- **`/expect` keyword syntax** -- `match=`, `timeout=`, `quiet=on` keywords; `parse_keywords()` shared utility
- **`/expect.regex`** -- regex pattern matching in scripts
- **`ctx.serial_send()`** -- send text with configured line ending and encoding
- **`ctx.serial_wait_for_data()`** -- wait for first byte from device
- **`parse_keywords()`** -- shared keyword argument parser in `scripting.py` with space normalization

### 0.35.0 Improvements

- **Centralized error display** -- `dispatch()` handles all error messages in red; handlers just return `CmdResult.fail()`
- **`dispatch()` returns `CmdResult`** -- callers can detect command success/failure and read elapsed time
- **Profiler uses `CmdResult.elapsed_s`** -- timing from dispatch when available, local fallback for TUI
- **`call_from_thread` returns results** -- TUI dispatch now propagates `CmdResult` back to script thread
- **`serial_claim`/`serial_release` wired in CLI** -- `serial_io()` works in CLI mode
- **REPL command echo in cyan** -- was red, now cyan; red reserved for errors
- **Script `wait_for_idle`** -- replaces fixed 100ms sleep between commands, adapts to device response time
- **CLI lambda param names** -- consistent with `PluginContext` signatures

### 0.35.0 Bug Fixes

- **CLI gold test flaky TEXTDUMP** -- `wait_for_idle` fixes race condition with multi-line responses

## 0.34.0 (2026-03-28)

### 0.34.0 New Features

- **`/expect` command** -- wait for serial output containing a pattern in scripts. `/expect {timeout} <pattern>` blocks until matched or aborts on timeout.
- **`/expect.quiet`** -- silent on success, red on timeout
- **`ctx.wait_for_match(predicate, timeout)`** -- engine primitive for plugins to build custom matching (regex, exact, numeric, etc.)
- **`ctx.serial_send(text)`** -- send text with configured line ending and encoding. Plugins no longer need to manually assemble line endings.

### 0.34.0 Improvements

- **DEMO port recognized** -- config editor shows green with "(simulated port)" hint, port picker skipped on load
- **Script abort message** -- scripts that fail on expect timeout show "Script aborted" instead of "Script finished"
- **Demo expect_test.run** -- test script exercising expect match and timeout

### 0.34.0 Bug Fixes

- **cmd.py hardcoded line ending** -- crcsend plugin now uses `ctx.serial_send()` instead of hardcoded `\n`

## 0.33.0 (2026-03-27)

### 0.33.0 Improvements

- **Port auto-detection** -- new configs auto-select the port when only one is available; prompts with a port picker when multiple ports exist
- **Port validation on load** -- loading a config whose port is missing prompts the port picker instead of silently failing
- **Default port changed** -- default port is now empty (`""`) instead of `COM4`, making configs portable across platforms
- **Modal key handling** -- up/down/escape keys no longer leak through to the REPL when a modal dialog is open

### 0.33.0 Documentation

- **README badge rows** -- Project Status, Powered by, and Built with badge sections
- **Docs badge** -- links to GitHub Pages site
- **GitHub Actions docs workflow** -- automatic docs deployment on push to main
- **Config help** -- fixed missing table separator, removed empty table, documented port auto-detection

## 0.32.0 (2026-03-26)

### 0.32.0 Improvements

- **CLI refactored to class** - `CLITerminal` replaces closure-based `run_cli()`
- **Progress bar** - real elapsed time, sub-character resolution (ASCII/Unicode), never shows 100% early
- **`/delay.quiet`** - silent delay subcommand for scripts
- **Config editor** - port validated against available ports, `$(env)` vars resolved and validated, cyan highlighting for variables, italic valid values, bold red "DO NOT EDIT" on config_version, baud rate yellow warning for non-standard values
- **Hook tree override** - registering a hook clears all children from plugins (clean ownership)
- **`/edit` plugin** - uniform edit tree for scripts, proto, plugins, cfg, log, info
- **Visualizer docs** - rewritten with byte-level examples, expected vs actual comparison
- **Smart config resolution** - bare names, directories, file extensions resolved automatically
- **`/ss` stubs** - "not supported in CLI mode" instead of unknown command
- **`/ver` command** and `--version` flag
- **ANSI regex consolidated** - shared `ANSI_RE` and `strip_ansi()` in `scripting.py`
- **Dead code removed** - `parse_script_lines` and 7 tests
- **Unicode cleanup** - 285 em dashes, right arrows, en dashes replaced with ASCII
- **`cfg_dir()` safety** - rejects paths with file extensions
- **`load_config()` safety** - raises `FileNotFoundError` instead of auto-creating configs

### 0.32.0 Documentation

- Installation page (uv-only, no pip)
- CLI Mode section in README
- Reordered help nav: Install -> Demo -> Getting Started -> Config
- Removed manual prev/next nav tables
- CRC catalog note (62 algorithms)
- CONTRIBUTING.md, CHANGELOG.md, LICENSE (MIT)

### 0.32.0 Testing

- 854 tests across 19 test files
- CLI gold-standard integration test (476 lines)
- 16 tests for config resolution chain

## 0.31.0 (2026-03-25)

Initial public release.

### 0.31.0 Features

- **TUI terminal** - full-featured Textual UI with serial I/O, modals, config editor, custom buttons
- **CLI mode** (`--cli`) - plain-text terminal for automation, scripting, SSH, and CI/CD
- **Plugin system** - drop a `.py` file in a folder to add commands. 23 built-in plugins.
- **Scripting** - `.run` scripts with delays, prompts, variables, sequence counters
- **Protocol testing** - binary send/expect tests with 62 CRC algorithms and packet visualizers
- **Data capture** - text, binary, struct, and hex capture modes with format spec decoding
- **Demo mode** (`--demo`) - simulated device for trying everything without hardware
- **Smart config resolution** - bare names, folders, and file extensions resolved automatically
- **Port control** - full serial port management as a plugin (`/port.*` with 17 subcommands)
- **Environment variables** - `$(env.PORT)` in configs and scripts with fallback defaults
- **Git-friendly** - config folders with .gitignore, scripts/plugins/tests versioned together
- **Version** - `--version` flag and `/ver` REPL command
- **Cross-platform** - Windows, macOS, Linux. Python 3.11-3.14.
- **MIT licensed**

### 0.31.0 Architecture

- `app.py` - Textual TUI frontend
- `cli.py` - plain-text CLI frontend
- `repl.py` - REPL engine with plugin dispatch, scripting, transforms, directives
- `plugins.py` - plugin system with `PluginContext` stable API and `EngineAPI` for builtins
- `serial_engine.py` / `serial_port.py` - serial I/O layer (no Textual dependency)
- `capture.py` - capture state machine
- `protocol.py` - binary protocol engine, CRC, format specs, visualizers
- `port_control.py` - pure functions for serial port control

### 0.31.0 Testing

- 861 tests across 18 test files
- CLI gold-standard integration test (476 lines of expected output)
- Passes on Python 3.11, 3.12, 3.13, 3.14
