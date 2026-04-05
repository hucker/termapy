# Changelog

## 0.44.0 (2026-04-03)

### New Features

- **Silent screenshots** -- `/ss.svg.quiet` for doc automation; `.quiet` echo suppression works with any command
- **Documentation screenshots** -- `doc_screenshots.run` script generates 10 documentation SVGs from demo mode

### Improvements

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

### New Features

- **CRC code generation** -- `/proto.crc.c`, `/proto.crc.python`, `/proto.crc.rust` generate standalone CRC functions from any of the 62 catalogue algorithms. Use `--table` for table-driven implementation. Both bit-by-bit and table-driven Python output verified against all catalogue check values.

## 0.42.0 (2026-04-02)

### New Features

- **Inline delays in `/proto.send`** -- `~duration` syntax inserts timing gaps between data segments (e.g. `/proto.send 00 ~25ms "AT\r"`). Supports `us`, `ms`, `s` units. Delays under 1ms use spin-wait for precision.
- **Microsecond durations** -- `parse_duration()` now supports `us` unit throughout the app.

### Improvements

- **TX/RX display** -- `/proto.send` now shows both hex and smart text for all packets. Inline delays shown as cyan hex + dim text markers.
- **Help docs split** -- "Serial Tools" (interactive send, CRC, hex mode) and "Protocol Testing" (scripts, visualizers, format specs) are now separate help pages with cross-links.
- **`\n` command separator fix** -- literal `\n` in user input no longer splits commands. Only custom button commands support `\n` as a separator. Fixes `/proto.send "text\n"` being split into two commands.
- **Input clears before execution** -- command input box clears immediately and shows "running..." during long-running commands.
- **Help formatting** -- fixed 25/25 column widths, script-only commands section, deduplicated target device rendering, button dispatch fix.
- **AT+HELP.JSON** -- demo device now lists the help command in its own JSON descriptor.

## 0.41.0 (2026-04-01)

### New Features

- **Quick setup dialog** -- new config creation uses a single dialog with port picker and baud rate selector instead of multi-step flow. Auto-connects after setup.
- **CFG.\* path variables** -- 15 new context variables (`$(CFG.DIR)`, `$(CFG.FILE)`, `$(CFG.PORT)`, `$(CFG.BAUD)`, `$(CFG.PORT_FULL)`, etc.) for use in scripts and commands
- **Config directory precedence** -- `--cfg-dir` flag > `TERMAPY_CFG_DIR` env var > `./termapy_cfg` (if present, never auto-created) > OS default (`%APPDATA%\termapy`, `~/.config/termapy`, `~/Library/Application Support/termapy`)
- **Dot syntax in variables** -- `$(NAME.SUB)` supported (e.g. `$(CFG.PORT_FULL)`)

### Improvements

- **Resolved paths everywhere** -- all error messages and config info show fully resolved absolute paths
- **Config info verbose-only** -- config dir/file/log paths shown only with `/verbose on`
- **Clear screen on config switch** -- clean slate when loading a new config
- **Focus command input on startup** -- input field gets focus automatically
- **Dotfiles filtered from pickers** -- config/script/proto pickers hide files starting with `.`
- **Clickable paths** -- documented in getting-started help

### Bug Fixes

- **Plugin module duplication** -- builtin plugins loaded via `importlib` now share module state with package imports, fixing `$(CFG)` not expanding and `FRONT_END` showing as `unknown`

## 0.40.0 (2026-03-31)

### New Features

- **Reconnect spinner** -- auto-reconnect now shows an animated spinner with amber title bar; click to cancel
- **Connection tooltip** -- title bar shows auto-connect/auto-reconnect status on hover

### Improvements

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

### Bug Fixes

- **`/include` (was `/import`)** -- renamed to avoid Python keyword collision causing import issues

## 0.39.3 (2026-03-31)

### Bug Fixes

- **Circular import on Python 3.11-3.13** -- lazy import of `var` in `app.py`, add `from __future__ import annotations` to `plugins.py`
- **CI audit job** -- fix `pip-audit` flag syntax, remove `continue-on-error` so security vulnerabilities fail the build

### Security

- **Pygments >= 2.20.0** -- pin to fix ReDoS vulnerability (CVE in GUID lexer regex)

## 0.39.0 (2026-03-31)

### New Features

- **`/import` command** -- fetch device command help from JSON over serial; auto-import on connect when `device_json_cmd` is configured
- **`/help.target`** -- show only imported target device commands
- **`/import.reload`** -- force re-import from device, ignoring cache
- **`/import.dump`** -- pretty-print imported commands as JSON
- **`/import.list`** -- list imported commands with args
- **`/import.clear`** -- remove imported commands and delete cache
- **Device help integration** -- new help page documenting how to add a JSON help command to your firmware
- **Disk caching** -- imported commands saved to `.target_menu.json` for instant reload on restart

### Improvements

- **Rewrite using-git.md** -- simplified help page focused on env vars and .gitignore
- **CLI: no bare print()** -- all output routed through `_raw()` / `_err()` methods for consistent handling
- **`/proto.send` verbose control** -- CRC info, TX bytes, and timing are now verbose-only; quiet mode shows only RX
- **Better error messages** -- friendly serial open errors, config editor live JSON validation, edit-distance command suggestions
- **`repl.cmd()` helper** -- avoids hardcoded command prefix in code
- **Demo device** -- `AT+HELP.JSON` replaces text `HELP` command; GPS commands included in JSON export

### Config

- **`device_json_cmd`** -- new config key for the serial command that returns device help JSON

## 0.38.1 (2026-03-30)

### Bug Fixes

- **Circular import on CI** -- CmdResult re-export from scripting.py caused import cycle on clean Python 3.11 installs
- **Toolbar buttons table** -- missing separator row broke table rendering in help docs

## 0.38.0 (2026-03-30)

### New Features

- **`/log.clear`** -- delete the session log file (TUI + CLI)
- **Variables help page** -- new dedicated docs page for `$(NAME)` syntax, built-ins, env vars, sequences
- **`/edit.run`, `/edit.proto`, `/edit.plugin` without args** -- lists available files instead of showing usage error

### Improvements

- **`CmdResult` moved to `plugins.py`** -- plugins import from a single module (`from termapy.plugins import CmdResult, Command`)
- **`CmdResult.err_prefix`** -- `ClassVar` for global error prefix customization
- **CLI context dirs fixed** -- `ctx.scripts_dir`, `ctx.proto_dir`, etc. were defaulting to cwd instead of config dir
- **CLI hint ordering** -- "Type commands..." appears before `on_connect_cmd` output
- **Docs coverage** -- custom button JSON example, UI modes, script profiling, `/demo` commands, missing config keys

## 0.37.0 (2026-03-29)

### New Features

- **Mode switching** -- `/tui` and `/cli` commands switch between TUI and CLI modes
- **`$(CFG)` variable** -- context variable resolves to current config name, usable in prompts
- **`default_ui` config** -- choose default launch mode (`tui` or `cli`), `--cli` flag overrides
- **Unified echo** -- single `_echo_cmd` function for both REPL and serial command echo
- **CLI `on_connect_cmd`** -- CLI now runs startup commands after connecting (was TUI-only)

### Improvements

- **`CmdResult.err_msg`** -- consistent "Error: " prefix on all error messages
- **run_script refactored** -- `ScriptCtx`, `BLOCKING_COMMANDS` dispatch table, context manager
- **`start_script` returns `(Path, CmdResult)`** -- no more double error messages
- **TUI title tooltip** -- full connection info, features, config path on hover
- **`echo_input_fmt` supports `$(CFG)`** -- prompt shows config name in both modes
- **Config migration v9->v10** -- adds `default_ui`
- **`/ss` and `/grep` CLI errors** -- proper `CmdResult.fail()` instead of yellow warnings
- **Sub-millisecond timing** -- 6-digit precision when `< 0.001s`

### Bug Fixes

- **No more double error on missing script** -- `start_script` owns error display
- **`/tui` in TUI and `/cli` in CLI** -- no-op instead of unknown command error
- **Demo table in help** -- missing separator row and column alignment fixed
- **Unused `json` import** -- removed from repl.py

## 0.36.0 (2026-03-29)

### New Features

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

### Improvements

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

### Bug Fixes

- **CLI `serial_send`/`serial_claim` not wired** -- now available
- **CLI `/cls` was no-op** -- now clears terminal
- **`/port.info` RI/CD alignment** -- fixed column spacing
- **Null handler guard** -- parent commands with no root handler no longer crash

## 0.35.0 (2026-03-29)

### New Features

- **`CmdResult` dataclass** -- all plugin/hook handlers return structured success/failure with error messages and elapsed time
- **`/ping` built-in command** -- measure serial response time with `serial_io` for accurate first-byte timing
- **`/ping.quiet`** -- suppresses device response output
- **`/run.profile` in CLI mode** -- script profiling now works in both TUI and CLI
- **`/expect` keyword syntax** -- `match=`, `timeout=`, `quiet=on` keywords; `parse_keywords()` shared utility
- **`/expect.regex`** -- regex pattern matching in scripts
- **`ctx.serial_send()`** -- send text with configured line ending and encoding
- **`ctx.serial_wait_for_data()`** -- wait for first byte from device
- **`parse_keywords()`** -- shared keyword argument parser in `scripting.py` with space normalization

### Improvements

- **Centralized error display** -- `dispatch()` handles all error messages in red; handlers just return `CmdResult.fail()`
- **`dispatch()` returns `CmdResult`** -- callers can detect command success/failure and read elapsed time
- **Profiler uses `CmdResult.elapsed_s`** -- timing from dispatch when available, local fallback for TUI
- **`call_from_thread` returns results** -- TUI dispatch now propagates `CmdResult` back to script thread
- **`serial_claim`/`serial_release` wired in CLI** -- `serial_io()` works in CLI mode
- **REPL command echo in cyan** -- was red, now cyan; red reserved for errors
- **Script `wait_for_idle`** -- replaces fixed 100ms sleep between commands, adapts to device response time
- **CLI lambda param names** -- consistent with `PluginContext` signatures

### Bug Fixes

- **CLI gold test flaky TEXTDUMP** -- `wait_for_idle` fixes race condition with multi-line responses

## 0.34.0 (2026-03-28)

### New Features

- **`/expect` command** -- wait for serial output containing a pattern in scripts. `/expect {timeout} <pattern>` blocks until matched or aborts on timeout.
- **`/expect.quiet`** -- silent on success, red on timeout
- **`ctx.wait_for_match(predicate, timeout)`** -- engine primitive for plugins to build custom matching (regex, exact, numeric, etc.)
- **`ctx.serial_send(text)`** -- send text with configured line ending and encoding. Plugins no longer need to manually assemble line endings.

### Improvements

- **DEMO port recognized** -- config editor shows green with "(simulated port)" hint, port picker skipped on load
- **Script abort message** -- scripts that fail on expect timeout show "Script aborted" instead of "Script finished"
- **Demo expect_test.run** -- test script exercising expect match and timeout

### Bug Fixes

- **cmd.py hardcoded line ending** -- crcsend plugin now uses `ctx.serial_send()` instead of hardcoded `\n`

## 0.33.0 (2026-03-27)

### Improvements

- **Port auto-detection** -- new configs auto-select the port when only one is available; prompts with a port picker when multiple ports exist
- **Port validation on load** -- loading a config whose port is missing prompts the port picker instead of silently failing
- **Default port changed** -- default port is now empty (`""`) instead of `COM4`, making configs portable across platforms
- **Modal key handling** -- up/down/escape keys no longer leak through to the REPL when a modal dialog is open

### Documentation

- **README badge rows** -- Project Status, Powered by, and Built with badge sections
- **Docs badge** -- links to GitHub Pages site
- **GitHub Actions docs workflow** -- automatic docs deployment on push to main
- **Config help** -- fixed missing table separator, removed empty table, documented port auto-detection

## 0.32.0 (2026-03-26)

### Improvements

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

### Documentation

- Installation page (uv-only, no pip)
- CLI Mode section in README
- Reordered help nav: Install -> Demo -> Getting Started -> Config
- Removed manual prev/next nav tables
- CRC catalog note (62 algorithms)
- CONTRIBUTING.md, CHANGELOG.md, LICENSE (MIT)

### Testing

- 854 tests across 19 test files
- CLI gold-standard integration test (476 lines)
- 16 tests for config resolution chain

## 0.31.0 (2026-03-25)

Initial public release.

### Features

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

### Architecture

- `app.py` - Textual TUI frontend
- `cli.py` - plain-text CLI frontend
- `repl.py` - REPL engine with plugin dispatch, scripting, transforms, directives
- `plugins.py` - plugin system with `PluginContext` stable API and `EngineAPI` for builtins
- `serial_engine.py` / `serial_port.py` - serial I/O layer (no Textual dependency)
- `capture.py` - capture state machine
- `protocol.py` - binary protocol engine, CRC, format specs, visualizers
- `port_control.py` - pure functions for serial port control

### Testing

- 861 tests across 18 test files
- CLI gold-standard integration test (476 lines of expected output)
- Passes on Python 3.11, 3.12, 3.13, 3.14
