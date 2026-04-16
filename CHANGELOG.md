# Changelog

## Unreleased

### New Features

- **Dynamic help** -- `Command.long_help` (and `PluginInfo.long_help`) now accepts a callable `(PluginContext) -> str` as well as a static string. Callables are invoked at render time so a command's DESCRIPTION section can reflect live runtime state (loaded files, current connection, cached counts). Existing static-string declarations are unchanged. See `writing-plugins.md` for the pattern.
- **Dynamic help on built-in commands** -- `/help` for `/cfg`, `/port` and each port subcommand, `/include`, `/cap.*`, `/ss`, `/run.edit`, `/proto` (+ subcommands), `/edit.run|proto|plugin`, `/var`, `/env`, and `/seq` now opens with a green single-line status reflecting the current state (active cfg name, connected port, counter count, file counts in the relevant folder, etc.). Single-value commands show only the state line; multi-option commands keep their existing prose.
- **Dynamic-help helpers** -- new `termapy.help_dynamic` module supplies small reusable building blocks (`state_line`, `folder_line`, `port_status`, `cfg_status`, `ns_count`, `compose`, `green`) so plugin authors can wire dynamic DESCRIPTION content in one or two lines. See `writing-plugins.md`.

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
  - Typos like `--talbe` get a `did you mean --table?` suggestion instead of being silently ignored
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

Plugin API release. Two new primitives let plugins own their own session state and lifecycle, and five built-ins (seq, target_commands, echo, hex_mode, verbose) migrate onto them as worked examples. `EngineAPI` is now a legible escape hatch containing only Textual/threading/serial handles — anything that could live in a plain dict has been moved out.

### 0.54.0 New Features

- **`ctx.ns(name)` namespace primitive** -- plugins get a sanctioned, session-scoped `dict` for their state instead of monkeypatching `ctx` or using module globals. Lazy creation on first access, same dict returned on every call with the same name, flat visibility (any plugin can read any namespace) so cooperating plugins can share state on purpose. See `ARCHITECTURE.md` for the full pattern.
- **Plugin lifecycle hooks** -- plugins can now declare `on_app_start`, `on_app_stop`, `on_script_start`, and `on_script_stop` as top-level functions in their module. The loader discovers them alongside `COMMAND`/`TRANSFORM`/`DIRECTIVE`. Exceptions are caught per-hook so one bad plugin cannot block others. Script hooks fire only at the outermost script boundary; nested `/run` does not re-fire. No base class, no decorators — a plugin is still a module that exports stuff.
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
