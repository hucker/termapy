# Changelog

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
