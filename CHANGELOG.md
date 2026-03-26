# Changelog

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
