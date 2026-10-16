# CLAUDE.md

## Project

Termapy — TUI serial terminal. Textual + pyserial. Python 3.11+.

## Key Files

- `app.py` — Textual app (UI, serial I/O, modals, config)
- `repl.py` — REPL engine (dispatch, scripting)
- `plugins/` — plugin system package: `command.py` (Command, CmdResult), `params.py`, `capabilities.py`, `loader.py`, `context.py` and `handles/` (PluginContext and its handles)
- `scripting.py` — pure functions (templates, duration parsing)
- `help/` — in-app help pages (Markdown source; the release HTML rebuild renders them to `html/`, which is what the wheel ships)
- `builtins/` — built-in REPL commands
- `ARCHITECTURE.md` — architecture overview, at the repo root

All other paths relative to `src/termapy/`.

## Architecture

- **Every rule the code relies on is a row in `docs/rules.md`** (the rule registry) -- a stable id, whether a test enforces it (**guarantee**) or review does (**assumption**), and the test. A change that relies on one names its id; a new rule gets a row the same day, an assumption until its guard exists; the bullets below carry `(registry <id>)` pointers to theirs. **Enforced by `tests/test_architecture.py` (registry `rules-registry`)**: a row's tests must exist, a guarantee must name one, and every guard in that file must lead its header with its id. A decision that is not a rule — what is vendored, how the lock moves, what a feature may never require — gets a dated entry in `docs/decisions.md`, and a bullet here cites the entry by date rather than carrying the story.
- Textual is confined to the UI layer: `app.py` (the main app) plus `dialogs/`, `widgets/`, `capture_view.py`, `title_bar.py`, `proto_debug.py`, `info_views.py`, `palette_provider.py`. That list is EXACT, not illustrative — those six modules and two packages are the complete set today. Core modules (`repl.py`, `plugins/`, `scripting.py`) never import Textual. Any NEW module that imports Textual is UI-layer and must be added to this list. (`pickers.py` is Textual-free — it builds picker *data*, not widgets — so it is not in this list.) **Enforced by `tests/test_architecture.py` (registry `ui-layer-textual`)**, which pins the set; the complementary runtime check in `test_cli.py` asserts importing `termapy.entry` pulls in no textual module at all, catching TRANSITIVE imports the static pass cannot see.
- **Handlers never touch `ctx.io._write` / `._write_markup`** — use the semantic channels (`result` / `output` / `status`, or their `_markup` variants) so the frontend decides how each kind of message renders. **Enforced by `tests/test_architecture.py` (registry `io-semantic-channels`)**, matching on the AST attribute rather than the text, so aliasing (`io = ctx.io`) and `getattr` are caught while a comment or docstring naming the primitive is not (decision 2026-08-20: guards are `ast` in pytest, never CI greps).
- **Text never reaches a markup parser unescaped** (decision 2026-10-13). Two parsers read `[word]` as a style tag and silently drop one they don't know: Rich (the output pane and the CLI console, via the `*_markup` channels) and Textual (widget content, `Button` labels, `notify`, tooltips). Into Rich markup, every interpolated value goes through `markup.escape_markup()` (exported from `termapy.plugins`) unless it is markup already, and then its name ends in `_markup`; help text is prose the renderer escapes, a `help_dynamic.green` state line the one markup a `long_help` carries. **Enforced by `tests/test_architecture.py` (registry `markup-escape`)**. A Textual widget showing text gets `markup=False` or a `rich.text.Text`, never an escaped string -- Textual's markup has no clean escape. **Enforced by `tests/test_architecture.py` (registry `widget-text-literal`)** for every `Static`, `Label`, `Button` and `notify` built in the UI layer; a later `.update()`, `.tooltip =` or `.label =` with text is review. Consumers that are not terminals (`--json`, MCP `output_lines`, the session log) get `markup.markup_to_plain()`.
- **app.py health is NOT a line count** (decision 2026-07-16, sharpened 2026-08-18). A feature-rich TUI has irreducible *essential* complexity, so a large `app.py` is honest, not a smell, and relocating UI code into mixins or helper modules scatters it without reducing it. Health is three things, none of them size: (1) **logic stays out of the UI** — serial / dispatch / scripting / capture live in the engines (`SerialEngine`, `ReplEngine`, `TerminalHost`, plugin handles); `app.py` is UI wiring only; (2) **new device behavior is a plugin**, not `app.py` code; (3) **no duplication**. Judge these in review; there is deliberately **no line-count gate**. Extract from `app.py` only for concrete pain (real duplication, logic that leaked in, a change that's genuinely hard to make), **never for the number**, and only when the moved code gains a test.
- `plugins/` and `scripting.py` — zero Textual/pyserial deps
- `repl.py` bridges plugins and app via `PluginContext` callbacks
- Load order: builtins → global → per-config → app hooks, later overrides earlier, resolved by the engine (`ReplEngine.resolve_plugins`) identically in every frontend and rebuilt on every config switch (registry `plugin-load-order`). The mechanism, the skip-with-warning rule and `TERMAPY_TRUSTED_PLUGINS_ONLY` are in ARCHITECTURE.md "Loading order", the one home for them.
- External plugins use `PluginContext` only. `InternalHandle` (`ctx.internal`) is internal/unstable.
- **The reverse direction has a rule too: core may call INTO a plugin, but only its published surface.** Core (`app.py`, `cli.py`, `repl.py`, `mcp/server.py`, `pickers.py`, …) importing a *public* function from `builtins/` is fine — `app.py` calls `find.dismiss()` when live data should close the find bar, and that is the plugin's documented entry point. What is NOT fine is reaching for a private handler or private state (`_handler_clear`, `_VARS`), because a plugin's privates are free to change and the app has no contract with them. **Enforced by `tests/test_architecture.py` (registry `core-imports-published-only`)** — an `ast` walk over every core module with an explicit allowlist, so a new core→`builtins` import fails `pytest` locally (not just in CI) until it is either moved into core or added to the allowlist as a published name.
- **Data folders come and go through `folders.ensure_folder` / `folders.prune_empty_folders` only.** A data folder (`folders.FOLDERS`) exists while something is in it: created at the write, never at a read, pruned empty by `fire_lifecycle`. A raw `.mkdir(` on a data folder is a bug. A rule, not a migration (decision 2026-09-15). **Enforced by `tests/test_architecture.py` (registry `folders-ensure-only`)** — an `ast` walk with a short allowlist of non-data-folder sites, probe-proven. The lifecycle in full is ARCHITECTURE.md "Config and filesystem".
- **A read never writes** (registry `reads-never-write`). A lookup that a listing, viewer, query or MCP path can reach returns a location and touches nothing: `cfg_data_dir`, `cfg_dir`, `find_config` and the `app_dirs` folders create nothing, and a config folder's setup and layout migrations live in `config.prepare_cfg_folder`, run at load (`load_config`) and at creation. Found 2026-10-14, when a mere `/cfg.list` rewrote every config through `load_config`.
- **Infrastructure never lives under `builtins/`.** If core needs it at startup or on the dispatch path, it is core by definition, and a command that exposes it is a thin surface on top. `variables.py` (the `$(NAME)` namespace) ← `builtins/commands/var.py` (`/var`), exactly as `port_control.py` ← `builtins/commands/port.py`. The test is "who calls it": something every frontend and the REPL engine need is not a plugin, however natural the matching command feels (decision 2026-08-20, when the variable engine came out of `builtins/`).

## Config

- Config dirs: `termapy_cfg/<name>/` plus the data folders in the tree at ARCHITECTURE.md "Config and filesystem", each created on first write and removed when empty. `plugin/`, `dev/` and `lib/` also exist at the cfg root as a global layer; the per-config one wins a name clash.
- **Device files (`dev/*.device.json`) are the board's registers, never the build's** (registry `device-files-not-build`). They merge over the sidecar's symbols at load and survive `/sym.import`; one installer (`symbols.session.install_build` / `install_devices`) rebuilds the merged `table`, and nothing else writes `ctx.ns("symbols")`. **Enforced by `tests/test_architecture.py` (registry `symbols-one-owner`)**: outside `symbols/session.py` nothing calls `ctx.ns("symbols")` at all (`ast`, probe-proven) — read through `get_table` / `get_devices` / `get_build_table`. Why there is no shipped catalog and no cfg key, and how the library and `ref` files work, is ARCHITECTURE.md "Device files". Stem-named sidecars (history, log, report, profile, symbol table) are listed ONCE in `folders.SIDECARS`; `rename_config` carries every entry and `prepare_cfg_folder` migrates any with a folder out of the root (registry `sidecars-listed-once`)
- Most users have a single config per project folder (one device), but multiple configs are supported for working with more than one device — each folder gets its own isolated `termapy_cfg/` so setups don't bleed together
- `termapy_cfg/` is gitignored
- New `DEFAULT_CFG` options must also be added to `builtins/demo/demo.cfg`
- New config keys must be documented in: `DEFAULT_CFG` (defaults.py), README.md config reference table, `help/config.md` field reference, and the demo.cfg template

## Rules

- Never edit files/folders on main branch.
- Always merge with `--no-ff` to preserve branch history in the graph.

## Library references

- **crcglot conceptual map**: <https://raw.githubusercontent.com/hucker/crcglot/main/llms.txt> — pull this before working on `/proto.crc.*` integration; it summarizes the four verbs (compute / detect-reverse / generate / encode-verify), the `LANGUAGES` and `ALGORITHMS` registries, and links to the per-surface docs.  Pinned to current crcglot floor (see `pyproject.toml`).

## Conventions

- Plugin args: `""` = none, `{braces}` = optional, `<angle>` = required, trailing `...` = repeatable (`<frames>...`, synthesized from `ParamSpec(variadic=True)`)
- No spaces inside brace/angle groups: `{on|off}` not `{on | off}`, `{name|*}` not `{name | *}`
- The synopsis grammar is ENFORCED at registration (`validate_synopsis` via `PluginInfo.__post_init__`): square brackets, `{{` artifacts, spaced `|`, and unbalanced groups fail loud at load/boot instead of rendering wrong in /help
- **Setting commands never mutate on a bare invocation — bare QUERIES.** This
  is the `stty`/`git config` standard. A boolean setting takes `{on|off|toggle}`:
  bare shows state, `on`/`off` (any `scripting.parse_bool` token) sets, the
  explicit verb `toggle` flips, anything else errors
  (`Invalid value: X (use on/off/true/false/yes/no/1/0/y/n/t/f or toggle)`).
  An enum setting takes
  `{val1|val2|...|cycle}`: bare shows, a value sets, `cycle` advances (wraps),
  anything else errors. Route booleans through `scripting.parse_bool_setting`
  (QUERY/TOGGLE sentinels + bool; it raises `ValueError` on anything else,
  which the handler returns as its failure) and `_bool_setting`/`_cfg_toggle`;
  enums through `next_in_cycle`. TUI buttons that flip on click dispatch the
  explicit `toggle`/`cycle` verb, never the bare command. NEVER treat an
  unrecognized argument as a flip (that hid the `/term.color 2` bug).
- **A bare command does what its button does, and says so by declaration.**
  Bare `/cfg` `/run` `/proto` `/port` (and the edit/run twins) open their
  picker: `Command(picker="run")`, which the dispatcher honors in the TUI;
  where there is no picker (CLI, MCP) their `bare_sub="list"` runs, the
  picker's contents. Bare `/ss` `/cap` `/log` open what their buttons
  open: `bare_sub="explore"` / `"show"`. Never code a bare branch in a
  handler that the declarations can express; `/help` renders them as
  the command's bare form (registry `bare-declared`).
- **Booleans have ONE vocabulary and ONE reader.** Wherever termapy turns
  user text into a boolean, in any frontend or file, the text is read by
  `scripting.parse_bool` — directly, or through `parse_bool_setting` and
  `ParamSpec(type="bool")`. The twelve tokens
  (on/off/true/false/yes/no/1/0/y/n/t/f) are one table; every hint is
  built from `BOOL_TOKENS_HELP`, never typed by hand. Absence (`None` or
  blank) yields the `default=` the reader declares, and a reader with no
  declared default fails loud (`Missing <what>`); an optional bool
  `ParamSpec` must declare its default at load. A present token outside
  the vocabulary ALWAYS raises `Invalid <what>: <text> (use ...)`; it
  never silently means False or the default — the env gates record the
  error and `termapy` refuses to start, naming the variable. A file
  format's own boolean (JSON cfg, TOML `.pro`) is not parsed from text:
  it is type-checked at load, and a templated `$(env.X)` on a boolean cfg
  key is read with the vocabulary after expansion. Exempt by the rule's
  own wording: a device's on/off grammar (the DEMO device's `AT+LED on`),
  device response text (`response_parsers._coerce`), a confirmation
  prompt (`cli._confirm` tests for yes), and presence-only switches that
  are not booleans (`TERMAPY_DEMO_FLEET`). Never compare a token to
  `"on"`/`"off"` with `==`/`in`, never declare an on/off `EnumValue` pair
  (that is what the `bool` param type is).
  **Enforced by `tests/test_architecture.py` (registry `bool-one-vocabulary`)**
  (ast over all of `src/termapy/` minus `vendor/` and a one-entry
  allowlist that only shrinks: bool-token comparators, wrapped set
  literals, on/off enums) plus the reader's own tests in
  `tests/test_scripting.py` — widened 2026-10-13 after the command-only
  scan let three more vocabularies live in core (`env_flags`, the
  `TERMAPY_GUI` override, `port_control.parse_bool_value`).
- **Every value category has one vocabulary and named readers** (audited
  2026-09-03; booleans above were the only drift):
  - **Numbers.** Plain counts are decimal: `ParamSpec(type="int")`, or
    try/`int()` in a documented hand-rolled holdout with an
    `Invalid <thing>: X` error — never a silent default on garbage.
    Anything address/size/mask-shaped (memory, protocol contexts) uses
    `symbols.parse_number` (decimal, `0x` hex, `Nh` — bare hex is NEVER
    guessed). USB ids are the one bare-hex number, read only by
    `port_format.split_vid_pid`.
  - **Durations.** `ParamSpec(type="duration")` / `scripting.parse_duration`
    only. A `* 1000` after parsing is unit conversion, not parsing.
  - **Hex payloads / packets.** Hex reads the same everywhere, every
    spelling and case (`01 03`, `0x01 0x03`, `0X01`, `0103`); a `0x`
    prefix marks exactly one byte (`0xDEADBEEF` is refused: a number of
    unstated byte order), and bare run-together hex is bytes in the order
    written. Quoted text is allowed only where a payload can hold text:
    `protocol.core.parse_data` (hex + quoted text) for `/proto.send`,
    `/proto.crc.calc`, `/traffic.snoop` and `.pro` files; `parse_hex`
    (hex only) for the packet commands `/proto.crc.verify`, `.find bin=`,
    `.reverse` and `/mem.write`; `parse_pattern` for expect wildcards. A
    bad payload is `Parse error: {e}`. **Output is uppercase**: `format_hex`
    for display, `.hex().upper()` for a value, `X` in format specs.
    **Enforced by `tests/test_architecture.py` (registry `hex-one-reader`,
    `hex-output-uppercase`)**: an `ast` walk with a function-level
    allowlist, each entry with the reason it is not user text (decision
    2026-10-13, which also records the calc text-guessing bug it
    replaced).
  - **Strings.** Identity (`str`/`path`/`command` params, rest args).
    Quoted-string escapes are `protocol.core._ESCAPE_MAP`
    (`\r \n \t \\ \0`).  A command string is ONE command, never split
    on `\n` -- an on-connect field and `-e` alike; several commands are
    a `.run` file (decision 2026-10-14, `config.on_connect_commands`).
  - **File names.** A command that reads an existing file of a known type
    takes the name with or without its extension, through
    `folders.default_file_name` at each location it searches (registry
    `file-name-default-ext`); it adds the extension only when that file
    exists. A command that creates a file appends a missing extension
    instead. `/edit` needs the extension: its folders are mixed. A command
    whose first argument names a data-folder file declares the folder,
    `Command(files="run")`, and every frontend's completion reads that
    declaration; no frontend keeps a command-to-folder table (registry
    `file-arg-declared`).
  - No ast guard for numbers, durations or strings: unlike bool tokens
    and hex, an `int()` on user text is indistinguishable from internal
    arithmetic, so enforcement there is this rule plus review.
- REPL prefix: `/`
- Modals return tuples: `("run", path)`, `("new",)`, `("edit", path)`
- Buttons: rainbow palette, Exit always red (`error`)
- SS/Scripts buttons always visible with file-count tooltips
- Plugin files end with `COMMAND` dict preceded by:
  `# ── COMMAND (must be at end of file) ──────────────────────────────────────────`
  (`COMMAND` is the last *command* export; a plugin that also exports a `TRANSFORM` or `DIRECTIVE`, as `env.py` and `var.py` do, places those after it)
- **Handlers are named `_handler` (root) and `_handler_<sub>` (subcommand)**: `_handler_crc_calc` for `/proto.crc.calc`, typed `(ctx: PluginContext, args: str) -> CmdResult`, in the built-ins, the demo plugins and `examples/plugins/` alike, so a new command is a copy of any existing one. **Enforced by `tests/test_architecture.py` (registry `handler-naming`)**; factory-built handlers are exempt.
- Subcommands = distinct operations (`port.list` vs `port.connect`)
- Toggles/values = args (`echo on`, `cfg baudrate 9600`)
- `needs=`: use the named `CapabilitySet` profiles (`.SERIAL_CONNECTED`,
  `.INTERACTIVE`, `.GUI_APPS`, `.SCREEN_CAPTURE`, `.TUI_MODE`,
  `.BLOCK_UNTIL`, `.SERIAL_INTERACTIVE`) for the recurring needs — one
  frozen shared instance each, docstring on hover; spell out unusual
  combinations inline; never a module-local `_NEEDS` constant
- Handlers that produce scriptable data must return `CmdResult.ok(value=...)` — without it, scripts in quiet mode get nothing. See `CmdResult` docstring in `plugins/command.py`.
- **`CmdResult.data` is the structured twin of `value`** — real JSON shape (dict/list/number) for agent consumers (MCP, `--json`); never a prose string. `value` stays the scriptable scalar. Optional: `data=None` is a legitimate permanent state (side-effect and prose-page commands); convert commands agents actually call, not all 185. Handlers with LARGE listings branch on `ctx.wants_data` and build either the prose or the records — never both (no doubled payload). Producers for `data` live in core beside the prose producers (`port_format.facts_to_json_record`, `usb_tree.to_json_records`, `variables.snapshot`), never inline in handlers.
- **A list command returns `CmdResult.table(title, noun, columns, records, value=...)`** (files: `folder_ops.file_table`): raw-valued records become `data`, the table is a `views.TableView`, and the handler renders nothing -- dispatch's exit prints it unless the caller wants data (`--json`, `/term.request`, MCP), so the table reads the same in every list and the JSON is the same records. Never hand-format a list or branch on `ctx.wants_data` for one (registry `list-one-format`). When each row is one value under a unique name (`/env.list`), pass `data=` the name-to-value mapping so a consumer looks the name up; rows with several fields stay records.
- **A status page returns `CmdResult.record(data, rows, value=...)`**, the single-record twin: the facts with real JSON types are `data`, the `(label, text)` rows are a `views.RecordView` that dispatch prints unless the caller wants data, so a JSON answer never carries the facts a second time as display text. Never call `format_kv_lines` from a handler (registry `record-one-format`, enforced by `tests/test_architecture.py`).

## Declarative Command Parameters (`Command.params`) vs hand-rolled

Some commands declare their args as `params=[ParamSpec(...)]` (the dispatcher
parses/coerces/validates + synthesizes usage/help/MCP-schema); others parse `args`
by hand. This split is **intentional** — use this rule, and if a command is
hand-rolled where a reader might expect params, leave a one-line comment saying
why (anchored to this section). See `docs/param-spec-implementation.md` and
`plugins/params.py`.

**Use `params` when** the grammar is positional tokens + `key=value` keywords +
at most one *rest* (a whole-line value, or a keyword that runs to end-of-line) +
at most one *variadic* positional (a repeatable token that binds a list).
Composes fine with flags, subcommands, level-suffixes (`.silent`), and
`raw_args=True` (raw_args skips only the `$(VAR)`/`$(env)` transform step, not
param parsing, so values arrive literal — e.g. `/cap.wire`). This is the default
for any multi-arg command.

**Keep it hand-rolled (documented boundary) when:**

- **Variable-arity mode dispatch** with a mutual-exclusion synopsis — `profile.load`
  (`{path|cmd=<command>}`: path XOR cmd XOR empty), bare `/cfg` (picker/query/set).
- **A keyword value with spaces followed by more keywords** — `cap.struct`/`cap.hex`
  `fmt=Temp:U1-2 Pressure:F3-6 records=5` (the spec syntax is space-separated by
  definition, shared via `protocol.parse_format_spec`); and `proto.crc.reverse`,
  whose documented `cmd=AT+RND.CUSTOM count=13 crc_bytes=2` puts keywords AFTER
  a to-end-of-line `cmd=`. `parse_keywords` would swallow them into the trigger
  text, so the hand-rolled parser (which recovers them) is load-bearing, not
  legacy. This is a PERMANENT holdout — do not "migrate" it.
- **Bare `-flag`-style literal grammar** flag parsing would eat — `search`'s `-term`.
- **Variadic positional lists** — `ymodem.send <file> {file2} ...`. (`params` can
  now express these via `ParamSpec(variadic=True)`, so this is a *not-yet-migrated*
  holdout rather than a permanent boundary; migrate when the command is next touched.)
- **An ordering constraint** where a state check must precede arg validation —
  `os_cmd` (env-gate before the arg check).

**Also weigh:**

- **Direct-handler unit tests** (`_handler(ctx, "raw args")`) must move to the
  dispatch contract when a command migrates — factor that cost into go/no-go
  (why `/env.set` was left hand-rolled).
- **Trivial single-arg** commands are net-zero-value to migrate (the parse is a
  one-liner); do it only if the typed MCP schema genuinely matters.
- **A holdout is not second-class.** `$(*NAME)` dereference is a public
  primitive (`plugins.params.resolve_deref`), not dispatcher-internal, so a
  hand-rolled handler opts in with two lines — resolve each token before
  coercing it. `proto.crc.reverse` does exactly this. Never migrate a command
  to `params` *just* to reach a primitive; call the primitive.
- Within one file, prefer all-params or clearly-documented holdouts — don't
  cherry-pick subcommands without a "why" comment (see `cap.py`).

## Error Messages (`CmdResult.fail(msg=...)`)

- Sentence case: capitalize the first letter (`"Count is required"`, not `"count is required"`)
- Exception: when the subject is a literal keyword/flag name, keep its native case (`"cmd= must have at least one command"`)
- No leading whitespace — the dispatcher adds the `Error:` prefix and formats the line
- **A failure is a failure, reported once, by dispatch** (registry `failure-is-failure`, `failure-reported-once`). A command that refuses returns `CmdResult.fail(msg)` with the reason, never `ok` beside a red line, never `fail(msg="")`, and prints none of it itself. A handler that answers with a nested command's result dispatches it with `report=False` (`ctx.internal.dispatch` always does) and returns it, so its own exit prints the failure once and `error` keeps the reason for scripts and agents. **Enforced by `tests/test_architecture.py`** for the empty message and the print-then-fail shape.
- State statements end with a period: `"No config loaded."`, `"Not connected."`, `"No log file."`
- Validation/lookup errors do NOT end with a period: `"Unknown config key: X"`, `"Invalid delay: Y"`
- Standard phrasings (reuse these verbatim before inventing a new form):
  - `"Unknown <thing>: <name>"` — categorical lookup failure (command, algorithm, folder, key)
  - `"Invalid <thing>: <value>"` — value failed validation (regex, duration, count, format spec)
  - `"<Thing> not found: <name>"` — file, directory, or config missing from disk
  - `"No <thing> loaded."` / `"Not connected."` — app state precondition unmet
  - Wrong arity → `raise UsageError()` (optional detail arg) — the dispatcher renders `"Usage: <prefix><cmd> <args>"` from the registered declaration. NEVER hand-write a `"Usage: ..."` string in a handler; the only sanctioned hand-written forms are the two documented holdouts (multi-form synopses like `/proto.crc.<lang>`, bare-parent redirects like `/proto.<sub>`)
  - `"<Thing> error: {e}"` — wrap an exception from a named subsystem (`"Type error"`, `"Parse error"`, `"Read error"`)
- Module-prefix style (`"Ping: ..."`, `"Include: ..."`, `"Expect: ..."`) is acceptable when **every** error from that plugin uses the same prefix — don't mix styles within one plugin

## Development

```sh
uv run pytest              # run tests
uv run termapy             # run the app
uv run termapy --cfg-dir . # use cwd for configs
git config core.hooksPath .githooks  # once per clone: ruff + ty gate every commit
```

## Testing

- `uv run pytest` — full suite (~110s); use this before commit/merge
- `uv run pytest -m "not slow"` — fast suite (~25s) for tight iteration. Skips ~230 subprocess-spawning, real-serial-loopback, and sleep-based tests. Use during dev; ALWAYS run the full suite before pushing.
- `uv run pytest -m slow` — only the slow tests (useful when debugging a specific subprocess test)
- **Two loopbacks, two labels, never conflated.** `sw_loopback` is the *software* loopback, pyserial's virtual `loop://` port (`tests/test_sw_loopback.py`, `tests/test_sw_loopback_io.py`, and any test that opens `loop://`): no hardware, runs **anywhere**, CI included, and every test that uses it carries the marker (registry `sw-loopback-labeled`). `hw_loopback` is the *hardware* loopback, a **TX-RX-jumpered USB-serial adapter** found by serial number (`TERMAPY_HW_LOOPBACK_SN`, default `BG03U7VTA`) via termapy's own `resolve_port`, so it survives COM-number reshuffles (`tests/test_hw_loopback.py`), and it is **gated**: deselected unless `--run-hw-loopback`, and under that flag it skips cleanly when the adapter is absent; `--require-hw-loopback` makes an absent, busy or silent adapter FAIL instead (registry `hw-loopback-demanded-fails-loud`); and it **never runs on GitHub Actions** (registry `hw-loopback-never-on-github`): `conftest.pytest_configure` refuses both flags there before collection, so a workflow that adds one goes red rather than touching a runner's ports. Say which loopback you mean, by label, every time. Run hw_loopback with `uv run pytest --run-hw-loopback -m hw_loopback -n 0 --no-cov`: serially and without coverage, because CPU contention under the parallel suite fakes the data-loss signature it exists to catch. **Run it before handing a branch for merge whenever the adapter is attached, and `release_prep` requires it** (decision 2026-10-13): a skip is reported as a skip, never as a pass. It covers defects that are structurally invisible to `FakeSerial` and to sw_loopback — neither has an OS driver, a bridge FIFO, or real baud pacing, so neither can drop a byte (that blind spot is how the 4 KB driver-buffer data loss survived a 41-finding audit). The fixture refuses to run unless the port demonstrably echoes itself: these tests write tens of KB at speed, which is harmless into a jumper and unacceptable into a live device. They carry `xdist_group` (one process may hold the port) — hence `--dist loadgroup` in `addopts`; without it xdist scatters them and every worker but one "skips" in a way that reads exactly like absent hardware.
- Coverage omits `__init__.py`, `builtins/*.py`, and `vendor/*` (see `[tool.coverage.run]` in `pyproject.toml`). **`app.py` and `dialogs/` are measured** — `app.py` is Pilot-tested by `tests/test_app_pilot*.py`, which boot the real `SerialTerminal` headless via `app.run_test()`, and every dialog by `tests/test_dialogs*.py`, which push it into a bare host app, so their numbers are real and movable. `builtins/` are plugins covered behaviorally, `vendor/` is third-party. Pilot gotchas, each paid for once: a dialog taller than the 80x24 headless default needs `run_test(size=...)` or its lower buttons are out of bounds to a click; Textual drops a click on a Button still showing its 0.2 s active effect, so a repeat click on the same button must wait for the `-active` class to clear; the output widget wraps a long path whatever the screen size, so compare with whitespace removed; never name a Screen attribute `_running` (it shadows Textual's `MessagePump._running`, True while mounted).
- `app.py` is not UNIT tested — its logic is orchestration glue (`_switch_config` is 36 lines of `self._disconnect()` / `self.repl.replace_cfg()` / `self._history_nav.reset()`), which cannot be made pure because it IS the wiring. Test it by DRIVING the app: `app.run_test()` +
  `pilot.pause()`, then assert on state. An AST audit (2026-08-18) found only 418 Textual-free logic lines left in its 3634, none forming a cohesive module — extraction is exhausted, Pilot is the lever. Limits: `run_test()` does not model real-terminal focus/blur or `@work` timing, so cover STATE TRANSITIONS there and keep input-focus work on live iteration.
- Run tests before commit; full suite before merging to main
- **Anything stateful is tested twice and long, not once.** A buffer, queue, watcher or claim gets three tests beyond the happy path: the operation twice in a row (the second `/expect` must not re-match the first reply), two items in one batch (the line after a match must still be there), and a sustained run with no consumer (what is held must stay bounded). One-shot tests pass on exactly the sequence and lifetime bugs that ship (decision 2026-10-14).
- **A new command ships with a test that names it.** `tests/test_architecture.py` fails for any registered command no test file mentions as a dotted token, a family generated from one handler (the `/proto.crc.<lang>` commands, folder subcommands, `/port.chip.*`) counting once; `KNOWN_UNTESTED_COMMANDS` is what was untested when the guard landed and only shrinks (its stale-entry test forces a name off once a test names it). The fix for a red guard is the test, never a new allowlist entry. **Enforced by `tests/test_architecture.py` (registry `command-has-test`)**.
- **Every test opens with a one-line docstring header** saying what it pins — the behavior or invariant a reader needs when they come back to the test later, not the function name reworded and not "Test that ...". Multi-line only when the setup itself needs explaining; not Google-style (no Args/Returns). A header may lead with a spec ID (`"""param-decl-validation: a broken declaration fails loudly at load."""`, as `tests/test_params.py` does) — that is the hook for a future spec/test/architecture matrix. **Enforced by `tests/test_architecture.py` (registry `test-headers`)** (`ast`, probe-proven). Files not yet converted are listed in `ALLOWED_HEADERLESS`; the list only shrinks, and its stale-entry test forces a file off once it is converted.
- AAA comments (`# Arrange`, `# Act`, `# Assert`; merged phases `# Arrange / Act`, `# Act / Assert`) mark the phases of a test that has distinct phases. A short test skips them — the header carries the why, and comments that repeat a three-line body are noise. A judgment call, not enforced: statement count does not measure triviality.
- Assert comments required
- Assert order: `actual == expected`
- Assert messages required for non-obvious asserts — state the *intent* (what a failure means), not a restatement of the expression, which pytest already prints. Self-documenting value-checks (e.g. `assert parsed.field == expected` in a parse test whose name conveys the scenario) are exempt. When an assert carries an explanatory comment, make it the message instead (`assert x == 500  # override` → `assert x == 500, "override"`). Suite compliance is ~94% by this standard, not 100% by an absolute one.
- Non-trivial: use `actual`/`expected` variables
- Multiple checks: `actual_x == expected_x` pattern
- Avoid mocking and monkey-patching.  Confirmation required!
- **Port enumeration takes `source=` / `trust_env=`** (`port_control.resolve_port_source`). Three layers, highest first: explicit `source=`, then `TERMAPY_DEMO_FLEET` unless `trust_env=False`, then real hardware. The env layer stays because the party wanting fake ports is usually not the calling code -- a CI job, a screenshot run, a docs build.
- **Hand a fleet in rather than stubbing `_gather_all_chip_facts`.** A `lambda *a, **kw:` stub swallows every signature change silently (it did -- two of them broke the moment `source=` was added), and an env var cannot express "no ports" or a scripted sequence of snapshots, which is what most of these tests actually want. `source=` is a callable invoked once per gather, so both fall out of it.
- **The `source=` surface reaches the whole port stack, `trust_env=` does not.** `list_ports`, `chip_info`, `chip_field`, `port_info`, `cli_flags.run_info` / `run_ports` / `run_watch`, and `serial_engine._classify_serial_error` all take a keyword-only `source=` and forward it down. They deliberately do *not* take `trust_env=`: deciding whether the environment may speak belongs to the discovery layer, and an injected fleet outranks the variable anyway. No production caller sets `source`; it exists so a caller can drive a handler against a known fleet without owning the machine's ports.
- **Two places keep the older levers, on purpose.** A test that drives `main()` (argparse-to-handler wiring) uses `TERMAPY_DEMO_FLEET`, because injection cannot reach through a process entry point -- that is what layer 2 is for. A test about *enumeration itself* (probing, `_facts_from_port_info`) patches `comports`, because a substitute fleet replaces that layer wholesale and would make the test vacuous. Both are commented where they live (`tests/test_cli.py::TestPortsFlag`, `tests/test_port_noninvasive.py`).

## CLI Gold Test

- `tests/cli_gold/cli_test.run` — deterministic script exercising ~100 commands
- `tests/cli_gold/cli_test.expected` — gold standard output (~780 lines; grows as commands are added, so this is approximate)
- Deliberately does NOT exercise `/help proto.crc`: its SUBCOMMANDS block is generated from `crcglot.LANGUAGES`, so a crcglot language addition would redden the gold with no termapy change. Stable proto.crc coverage (`/proto.crc.info`, `.calc`) stays; the help renderer is covered by the other `/help <cmd>` pages.
- Regenerating the gold file requires manual review — the expected output is the source of truth for CLI regression testing
- Only deterministic commands allowed — no timestamps, random values, or hardware-dependent output
- No Unicode in output strings — use ASCII only (hyphens, not em dashes)

## Code Style

- If code manages state, use a class — not functions with closures over shared variables
- Pure functions are fine and preferred when there's no state (e.g. `scripting.py`)
- `scripting.py` — pure functions, no state, no I/O
- `plugins/` — no Textual/pyserial imports
- New commands → plugins in `builtins/` unless they need Textual
- Textual-dependent commands → `register_hook()` in `app.py`
- Google-style docstrings and type hints
- Warn about hacks, threads, delays, magic,monkeypatch, test constants
- OS-independent path handling: use `pathlib.Path` — never split on `/` or `\\`
- **Loop variables name the element, singular: `for singular in plural:`.**
  `for token in tokens:` — never `for tok in tokens:` or `for t in tokens:`.
  When the collection has a plural name the loop variable is its singular form;
  when it doesn't, still name the element for what it is (`for byte in data:`,
  `for char in text:`). Applies to comprehensions. **Exempt:** positional and
  numeric variables (`i`/`j`/`k` indexes, `x`/`y`/`z` coordinates), `_` for an
  unused binding, tuple unpacking where each name describes its own slot
  (`for name, handler in COMMANDS.items():`), and short words that are whole
  words rather than truncations — `key` (to a dict what `i` is to an index),
  plus `app`, `raw`, `bus`, `pyc`, `hex`, `url`, `tag`, `row`, and `cmd`
  (termapy's own vocabulary: `cmd=`, `cmds`, `cmd_delay_ms`). A full word that
  isn't the singular is fine when it says more than the singular would — `for
  spec in params:` over `ParamSpec` objects, `for info in plugins:` over
  `PluginInfo`. The rule targets truncation, not variety.
  Use **folder/folders**, never `dir` (a builtin) or `directory`. Note `file`
  and `byte` are NOT builtins in Python 3 — only `bytes`/`bytearray` are — so
  both are available as element names.
- Watch for large code added to solve small problems. AI can make any spec work regardless of how much code it takes, so volume is not evidence of difficulty. Sprawling helpers, many-branched special cases, or duplicated logic across helpers usually indicate the spec was under-specified or the approach is wrong — pause and re-scope rather than piling on more code.

## Threading

The narrative — the thread diagram, the two hand-off mechanisms and why each rule exists — is ARCHITECTURE.md "Threading model", the one home for it. These are the rules, each with its registry id.

- The serial reader runs on a thread **`SerialEngine` owns** (`start_reader` / `stop_reader`), never one a frontend creates; teardown is a real `Thread.join()` (registry `reader-owned-by-engine`).
- **Every command runs off the main thread, and none of them is marshalled to it** (registry `commands-off-main`). Typed input, buttons and palette entries each get a `@work(thread=True)` worker (`app._dispatch_on_thread*`), script lines run on `_run_script`'s worker, and both call `repl.dispatch_full` inline on that thread. This bullet once claimed the opposite, and that false version was the stated rationale for a live design decision, so treat it as load-bearing.
- **Output helpers marshal themselves** (registry `output-helpers-marshal`): `_status`, `_set_status_bar`, `_write_output_markup` and `_on_main` forward to main when called off it, as do the TUI connection hooks (`_on_connected`, `_on_connect_failed`, `_on_disconnected`), which `/port.connect` reaches from a worker. Nothing reached from a worker touches a widget any other way.
- **A reconnect run owns its cancel token** (registry `reconnect-survives-connect-attempts`): never the reader's `stop_event`, which every connect attempt sets.
- **No `read_loop` callback may block on another thread** (registry `rx-never-blocks`): RX reaches the UI through `app._rx_enqueue`, never a blocking `call_from_thread`, because a blocking callback deadlocks teardown and stalls the reader until the driver drops bytes.
- `_acquire_dispatch_guard` is **non-blocking on the main thread and a bounded wait off it** (registry `dispatch-guard-main-never-parks`): main never parks on a lock a worker holds; a worker waits rather than dropping the line and fails with "Busy" only after `_DISPATCH_WAIT_S`. The guard is an `RLock`, so nested `ctx.dispatch` on the same thread is allowed.
- **One outermost script at a time, enforced in `ReplEngine.start_script` for every frontend** (registry `script-single-owner`): a nested `/run` on the owning thread is allowed, a launch from any other thread is refused. Never add a second guard in a frontend; the engine owns this rule.
- **A plugin must never `clear()` the shared `script_stop_event`** (registry `plugin-never-clears-stop`). It belongs to the script, and clearing it erases a stop the user just requested. `/repeat` and `/proto.run` may clear it only when `ctx.internal.in_script()` is False, where each is itself the outermost cancellable operation.
- `_confirm()` blocks on an event and posts its dialog via `call_from_thread`, so it must be called from a background thread; main-thread misuse fails closed (registry `confirm-off-main`).
- `_BLOCKING_COMMANDS` in `repl.py` is **not** a threading mechanism despite its name (registry `blocking-commands-need-sctx`). Those commands are intercepted because they need the `ScriptCtx` or must set `_script_stop` — see the comment at its definition. The test for adding one is "does it need sctx or `_script_stop`?", never "does it block?".

## Exception Safety

- `call_from_thread` → `try/except RuntimeError` (shutdown)
- `ser.write/dtr/rts/send_break` → catch `(OSError, SerialException)`
- Config load/write → catch `Exception`/`OSError`
- Non-critical file I/O (history, logs) → swallow `OSError`
- Plugin handlers called inside `dispatch()` try/except — no unguarded calls
- Never crash to stack trace — use `_status()` for errors

## Precommit

- **Nothing is committed with a diagnostic -- checked at EVERY commit, not at release.** Before any commit, mine or Chuck's on request, all three must be clean: `uv run ruff check src/termapy/ tests/` = 0, `uv run ty check src/termapy/ tests/` = 0, and the test suite green. Tests are code: "it's only a test" is never a reason to leave one. Clean means no errors or warnings; hints (an unused parameter in a fake) are not diagnostics. Every diagnostic is fixed, or suppressed with its rule code and a reason (`# ty: ignore[rule] -- why`); never left for later. **The touched files' VS Code Problems panel must be EMPTY before the commit** -- a red or yellow underline the gates miss is still a defect, and so is a spelling note. Check it with the editor's diagnostics tool on every file the change touched; the post-edit hook prints the same list. Whatever is there gets fixed in the same change, not reported: a markdownlint warning is rewritten (never silenced in `.markdownlint.json` to make it go away), and "pre-existing" is not an exemption -- a diagnostic in a file you touched is yours. The one thing to flag rather than fix is a diagnostic whose fix would change behavior (a `# type: ignore` the gate wants removed, say); fix the rest and say what you fixed. **Spelling is the exception that needs Chuck's eyes:** a cSpell note is either a typo or a word the project uses, and that call is his, not the model's (a word added to the dictionary unasked is a typo that will never be flagged again). Fix a typo you introduced; for every other flagged word, add it to `cspell.json` (the tracked dictionary, alphabetical) in the branch so the panel is clean, and list those words verbatim in the hand-off under "Dictionary additions to confirm" so he can strike any of them before the merge. Never describe the additions in prose only, and never leave a word flagged without saying so. The release gate is the backstop, not the check: when it was the only place ty ran, and on `src/` alone, 101 problems built up in tests unseen, 12 of them real mistakes (a `-> callable` annotation, `active_flags={}` where a set was meant, handlers that raised on every run). **Enforced by `.githooks/pre-commit`** (ruff + ty, a few seconds; the suite is too slow for a hook and stays a manual step), active once per clone via `git config core.hooksPath .githooks`. Never bypass it with `--no-verify`; fix what it reports.
- **A new dependency gets a `Credit` record in `credits_data.py`.** That table
  is the only place a dependency is described; the Help tooltip, `/credits`,
  and `help/acknowledgments.md` all iterate it, and
  `tests/test_credits_data.py` diffs it against `pyproject.toml` both ways
  (plus the vendored tree), so a dep added without a record fails the suite.
  After editing the table run `python scripts/sync_acknowledgments.py` to
  regenerate the page block and `credits.py`. Same principle everywhere: when
  three surfaces need the same facts, put the facts in one table and iterate
  it — never keep parallel hand-written lists.
- **Format-spec type codes live in `protocol/type_codes.py` (`TYPE_CODES`).**
  The parser takes its code sets from it and the type tables in README.md and
  `help/protocol-testing.md` are generated from it (Output column decoded by
  the real parser). Edit the table, then `uv run python
  scripts/sync_format_tables.py`; `tests/test_format_tables_sync.py` fails on
  a hand-edited table.
- Update README.md and help/*.md
- Update ARCHITECTURE.md if modules, plugins, or structure changed
- **Do NOT hand-update the test/ty counts or badges on feature
  builds.** The test count, ty diagnostic count, and the
  per-module line counts in README.md and ARCHITECTURE.md are refreshed
  ONLY by `release_prep.py` during the release process (see the
  "Auto-refreshed every release" list below). Editing them on every branch
  just churns the diff and causes merge conflicts between parallel
  branches, and the numbers go stale again on the next merge anyway. Leave
  them; release trues them up.
- Do NOT rebuild HTML help in feature commits — it adds noise to diffs
- HTML help rebuild should be a separate commit before release versions
- Run tox, pytest, coverage review
- **The same two commands are the release gate.** `release_prep` refuses to cut a release unless ruff and ty both report zero, and the README badge tracks ty's count (yellow/red on regression). In tests, suppress a deliberate wrong type or a test double with `# ty: ignore[rule]` -- ty does NOT honor mypy-coded `# type: ignore[arg-type]` -- and narrow an optional with `assert x is not None, "..."` before using it.
- Both tools are pinned exactly in `[dependency-groups] dev` and the rule set is pinned in `[tool.ruff.lint]`. Run them through `uv run`, never `uvx` / a global install — otherwise the gate enforces whatever version happens to be around, which is how ruff 0.16's wider defaults turned a clean tree into 636 findings. Bumping either is a deliberate chore branch with the new findings reviewed.
- **Run the gates with all extras synced** (`uv sync --all-extras`; `release_prep` does this itself before checking). ty's count depends on what's installed, not just on the source: without the extras the optional-dependency imports (mcp, jsonschema, textual_serve) report `unresolved-import`, and with them a suppression written for the missing case reads as unused. Both states are non-zero and mutually exclusive, so the gate names one environment — the richer one, which type-checks the optional code paths rather than skipping them. Consequence: do **not** add `# ty: ignore[unresolved-import]` to an optional-dependency import; guard it with `try/except ImportError` and let the extra be present at check time.

## Release

Two-stage automation in `scripts/`, run from clean main: `python scripts/release_prep.py <version>` cuts `release/v<version>`, upgrades the lock, audits it, runs the hw_loopback suite, refreshes the counts, runs `pytest` and `tox`, rebuilds the HTML help and makes two commits; you rewrite the CHANGELOG stub into a user-facing summary and `git commit --amend --no-edit`; `python scripts/release_publish.py --yes` merges `--no-ff`, tags, pushes and creates the GitHub release. The step-by-step mechanics and the recovery procedure are in `scripts/README.md`, the one home for them; what follows here is policy.

Hard gates (`release_prep` aborts):

- ruff and ty must both report zero issues -- checked before the branch is cut and again after the lock upgrade, because the upgraded environment can move ty's count
- `uv.lock` must audit clean (`scripts/audit_lock.py`: pip-audit over the exported lock, every extra and group, nothing installed). A finding reviewed as unreachable is passed explicitly, `--ignore-vuln <ID>`, so the decision sits on the release command line
- the hw_loopback suite must pass on the attached jumpered adapter (`--require-hw-loopback`, so an unplugged bench fails rather than skips). Releasing without it is `--skip-hw-loopback`, on the release command line for the same reason
- `main` must be in sync with `origin/main`
- tag `v<version>` must not already exist

Dependency policy, the whole of it:

- Lock everything; `uv.lock` ships with every release.
- The lock moves at release time only. `release_prep` runs `uv lock --upgrade`; the suite, tox, ruff and ty gate the result. Between releases leave the lock alone -- no Dependabot, no per-package churn under branches in flight. The one exception is a fix worth shipping now (a reachable CVE): `uv lock --upgrade-package <name>` on a chore branch.
- The audit is of the lock, not of an environment. An environment audit sees only what it installed: the CI job `audit_lock.py` replaced ran `uv sync` without extras and pointed pip-audit at the repo root, so it audited zero packages and stayed green. CI now runs the same script as the release.
- Exact pins are deliberate and `--upgrade` respects them: ruff and ty in the dev group, pip-audit in `audit_lock.py` (through `uvx` with `==`, the same guarantee, so the audit tool's own tree never joins the lock it audits), and the tree-sitter caps. Each moves on its own chore branch with the findings reviewed.
- `scripts/check_dep_updates.py` is the complement: it lists which direct-dependency *bounds* are holding back a newer release, the one thing `--upgrade` cannot move.

Auto-refreshed every release (`update_readme_md` / `update_architecture_md`):

- test count (`pytest --collect-only`) — written to **exactly one place**, the
  README's "Test coverage" `<details>` summary line. Everywhere else the count
  is imprecise prose ("extensively tested", "thousands of tests") that never
  needs updating. ARCHITECTURE.md and the README body line carry no count.
- ty diagnostic count + badge color
- NOT coverage: the `coverage NN%` badge in README's first row is the one
  coverage number, live from Codecov (shields.io's codecov endpoint), measured
  by CI on every push (Linux, the coverage config in pyproject). The badge rows
  follow one rule: Project Status badges show a value and its color, Built with
  badges name a tool and its genre -- so coverage.py is `coverage | code
  coverage` there. No line carries a local percentage: a release-day Windows
  run differed from Codecov by a few points and showed two numbers.
- ARCHITECTURE.md per-module line counts (the tree diagram is the one home for
  exact line counts — prose elsewhere names modules without counts)

**General rule: exact numbers live only in release-updated structured spots
(the summary line, badges, the ARCHITECTURE tree). Prose uses general terms
("extensively tested", "the largest UI modules", "heavily covered") so no
hand-maintained figure can drift.** Don't reintroduce a count/percent/line
figure into a sentence. Floor/approximate language is always fine in prose
("100+ algorithms", "more than 2000 tests", "~60%") — it stays true as the
real number grows, so it isn't a maintenance dependency. Every release_prep
substitution targeting one of the exact-count spots must fail loud
(`re.subn` + `die()` on a miss) so a reworded target can't silently drift.

No RC versions, no leading `v` in the version arg, never run from anywhere but main. Scripts are stdlib-only and fail loud. See script docstrings for details.

## Politeness

- Be polite in messaging. Don't praise sycophantically.
- Focus on clear concise and helpful without unnecessary flattery or emotional language.
- Avoid phrases like "Great choice!", "You're doing amazing work!"
- The goal is high quality, production-ready docs and code, not emotional support.
