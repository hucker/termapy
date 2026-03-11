# termapy

![tests](https://img.shields.io/badge/tests-487%20passed-brightgreen) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![3.11](https://img.shields.io/badge/3.11-pass-brightgreen) ![3.12](https://img.shields.io/badge/3.12-pass-brightgreen) ![3.13](https://img.shields.io/badge/3.13-pass-brightgreen) ![3.14](https://img.shields.io/badge/3.14-pass-brightgreen)

*Pronounced "ter-map-ee"*

A full-featured serial terminal that runs anywhere Python does. ANSI color rendering, session logging, screenshots, scripting, binary protocol testing, and a plugin system — all in a terminal UI that installs in seconds.

Built for embedded systems development and manufacturing test, where you need a reliable serial terminal that works the same on Windows, macOS, and Linux without installing a GUI application.

## Why termapy?

- **Instant setup** -- one command to install and run, no GUI installer or system dependencies. If you have [uv](https://docs.astral.sh/uv/) it takes seconds; without it, minutes.
- **Portable** -- pure Python, runs in any terminal. Same tool on your dev machine, CI server, and factory floor.
- **Full color terminal** -- renders ANSI escape sequences, not just plain text. See what your device actually outputs.
- **Built-in test tools** -- send raw hex, run scripted send/expect sequences with pass/fail, decode protocol fields with pluggable visualizers.
- **Everything in one folder** -- each config gets its own subfolder with logs, screenshots, scripts, and plugins. Copy the folder to share a complete setup.
- **Developer-centric** -- the tooling is all files in folders, files are standard formats like JSON and TOML.

## Quick Start

If you have `uv`, one command to try it:

```sh
uvx --from git+https://github.com/hucker/termapy termapy
```

Press `Cfg` to edit your COM port parameters:

![termapy screenshot](img/screenshot_cfg.png)

Press the connection button at the top and type commands at the bottom input box.
![termapy screenshot](img/screenshot_iot_dev.svg)

## Demo Mode

No hardware? Try termapy with a built-in simulated serial device:

```sh
termapy --demo
```

This creates a demo config at `termapy_cfg/demo/` that auto-connects to a simulated device. Bundled scripts, proto test files, a demo plugin (`!probe`), and a demo visualizer (`AT`) are included to exercise all features. You can also switch to demo mode at runtime with `!demo`, or set `"port": "DEMO"` in any config file.

![Demo project tree](img/demo_tree.svg)

**AT commands:**

| Command | Description |
| --- | --- |
| `AT` | Connection test (returns `OK`) |
| `AT+PROD-ID` | Product identifier (returns `BASSOMATIC-77`) |
| `AT+INFO` | Device info (version, uptime, free memory) |
| `AT+TEMP` | Read temperature sensor |
| `AT+LED on\|off` | Control LED |
| `AT+NAME?` | Query device name |
| `AT+NAME=val` | Set device name (max 32 chars) |
| `AT+BAUD?` | Query baud rate |
| `AT+BAUD=val` | Set baud rate (9600, 19200, 38400, 57600, 115200) |
| `AT+STATUS` | Device status (LED, uptime, connections) |
| `AT+RESET` | Reset device (simulates boot sequence) |
| `mem <addr> [len]` | Hex memory dump (deterministic, max 256 bytes) |
| `help` | List all commands |

**Modbus RTU:**

| Function | Description |
| --- | --- |
| `0x03` | Read holding registers (up to 125 registers) |
| `0x06` | Write single register (echo-back) |

CRC16 validation is enforced on all frames. Invalid CRC or unsupported function codes return Modbus exception responses.

## Install

Requires Python 3.11+, newer is better for performance reasons but tests have run on 3.11+. The recommended way to run `termapy` is with [uv](https://docs.astral.sh/uv/), which handles dependencies automatically:

```sh
uv run termapy
```

Or run directly from GitHub without installing anything locally:

```sh
uvx --from git+https://github.com/hucker/termapy termapy
```

You can also install with pip:

```sh
pip install termapy
termapy
```

To specify a config file:

```sh
termapy my_device.json
```

You can also override the config directory:

```sh
termapy --cfg-dir /path/to/configs
```

On first run with no config files, `termapy` prompts for a config name and opens an editor with defaults. If one config exists it loads automatically. If multiple exist, a picker dialog appears. Any missing fields are added from defaults and saved back.

Config files are organized in `termapy_cfg/<name>/<name>.json`, with logs, screenshots, scripts, and command history stored alongside each config in its subfolder:

```text
termapy_cfg/
├── plugins/                            # global plugins (all configs)
│   └── hello.py
├── iot_dev/
│   ├── iot_dev.json                    # config file
│   ├── iot_dev.log                     # session log
│   ├── .cmd_history.txt                # command history
│   ├── ss/                             # screenshots
│   │   ├── screenshot_20260306_141523.svg
│   │   └── screenshot_20260306_141530.txt
│   ├── scripts/                        # script files for !run
│   │   └── init_sequence.txt
│   ├── plugins/                        # per-config plugins
│   │   └── custom_init.py
│   └── viz/                            # per-config packet visualizers
│       └── modbus_view.py
└── sensor_b/
    ├── sensor_b.json
    ├── sensor_b.log
    ├── .cmd_history.txt
    ├── ss/
    ├── scripts/
    ├── plugins/
    └── viz/
```

## Portability

`termapy` has been developed and tested 100% on **Windows**. Basic usage has been verified on **macOS** — connecting to serial ports, ANSI terminal rendering, and screenshots all work — but macOS support should be considered **alpha** until there is more testing. Linux has not been tested yet.

## Features

- **ANSI terminal emulation** -- renders color escape sequences and handles clear-screen
- **Interactive title bar** -- clickable buttons for port selection, config switching, connect/disconnect with red/green status, and `#` toggle for line numbers
- **Auto-connect and auto-reconnect** -- reconnects on port drop with retry
- **Auto-login commands** -- send a sequence of commands on connect (separated by `\n` in config)
- **Hardware line control** -- toggle DTR/RTS and send Break when `flow_control` is `"manual"` (see example below)
- **Command history** -- press Up to recall recent commands (default 30, configurable via `command_history_items`), persisted per-config; Enter executes, F2 edits
- **Local echo** -- optionally echo sent commands with configurable Rich markup formatting
- **Custom buttons** -- add up to 4 configurable toolbar buttons that send serial commands, run REPL commands, or execute multi-command sequences
- **JSON config files** -- create, load, edit, and switch configs from within the app; each config gets its own subfolder
- **Color-coded sessions** -- set `app_border_color` per config to visually distinguish multiple serial connections
- **Session logging** -- timestamped plain-text log stored per-config, with optional date-stamped commands
- **Screenshots** -- save the terminal view as SVG (Ctrl+S) or plain text (Ctrl+T)
- **Scripting** -- create, edit, and run script files from the UI; supports serial commands, delays, REPL commands, and sequence counters with auto-increment; scripts are stored in the per-config `scripts/` folder
- **REPL commands** -- type `!help` for local commands: screenshots, clear screen, run shell commands, inline config editing
- **Binary protocol testing** -- send raw hex bytes, run scripted send/expect test sequences with pass/fail reporting, wildcard pattern matching, and hex display mode; supports both hex and quoted text in `.pro` script files; interactive debug screen with repeat, delay, stop-on-error, scrolling results log, and per-test visualizer column data in log files
- **Plugins** -- drop `.py` files into `plugins/` folders to add custom REPL commands; all built-in commands use the same plugin architecture
- **Pluggable packet visualizers** -- hex and text views are built-in; drop a `.py` file into `viz/` to add custom packet visualizers (e.g. Modbus field decoding, bit-level views) without modifying core code; selectable via checklist in the debug screen with optional format spec string display

## Keyboard Shortcuts

| Key       | Action                               |
| --------- | ------------------------------------ |
| Ctrl+Q    | Quit (also closes any open dialog)   |
| Ctrl+S    | Save SVG screenshot                  |
| Ctrl+T    | Save text screenshot                 |
| Ctrl+P    | Command palette                      |
| Up        | Command history                      |
| F2        | Edit selected history command        |

## Title Bar Buttons

| Button | Action                                                              |
| ------ | ------------------------------------------------------------------- |
| `?`    | Open the help guide                                                 |
| `Cfg`  | Open the config picker                                              |
| `Run`  | Open the script picker                                              |
| Center | Click to edit the current config                                    |
| Port   | Click to select a serial port                                       |
| Status | Click to connect/disconnect (red = disconnected, green = connected) |

## REPL Commands

Type commands prefixed with `!` (configurable via `repl_prefix`) to run local actions instead of sending to the serial device.

| Command                   | Description                                                                      |
| ------------------------- | -------------------------------------------------------------------------------- |
| `!help [cmd]`             | List commands or show extended help for one                                       |
| `!help.dev <cmd>`         | Show a command handler's Python docstring                                         |
| `!port [name]`            | Open a port by name, or show subcommands                                         |
| `!port.list`              | List available serial ports                                                      |
| `!port.open {name}`       | Connect to the serial port (optional port override)                              |
| `!port.close`             | Disconnect from the serial port                                                  |
| `!cfg [key [value]]`      | Show config, show a key, or change a value (with confirmation)                   |
| `!cfg.auto <key> <value>` | Set a config key immediately (no confirmation)                                   |
| `!ss.svg [name]`          | Save SVG screenshot                                                              |
| `!ss.txt [name]`          | Save text screenshot                                                             |
| `!ss.dir`                 | Show the screenshot folder                                                       |
| `!cls`                    | Clear the terminal screen                                                        |
| `!run <filename>`         | Run a script file (checks `scripts/` folder then cwd); or use the Scripts button |
| `!delay <duration>`       | Wait for a duration (e.g. `500ms`, `1.5s`)                                       |
| `!confirm {message}`      | Show Yes/Cancel dialog; Cancel stops a running script (see `at_demo.run`)        |
| `!stop`                   | Abort a running script                                                           |
| `!seq [reset]`            | Show or reset sequence counters                                                  |
| `!print <text>`           | Print a message to the terminal                                                  |
| `!print.r <text>`         | Print Rich markup text (e.g. `[bold red]Warning![/]`)                            |
| `!show <name>`            | Show a file (`$cfg` for current config)                                          |
| `!echo [on \| off]`       | Toggle REPL command echo                                                         |
| `!show_eol [on \| off]`   | Toggle visible `\r` `\n` markers for line-ending troubleshooting                 |
| `!os <cmd>`               | Run a shell command (10s timeout, requires `os_cmd_enabled`)                     |
| `!grep <pattern>`         | Search scrollback for regex matches (case-insensitive, skips own output)         |
| `!info {--display}`       | Show project summary; `--display` opens full report in system viewer             |
| `!proto send <hex>`       | Send raw hex bytes and/or quoted text, display response as hex (see below)       |
| `!proto run <file>`       | Run a binary protocol test script (.pro) with pass/fail                          |
| `!proto hex [on \| off]`  | Toggle hex display mode for serial I/O                                           |
| `!proto crc list {pat}`   | List available CRC algorithms (optional glob filter)                             |
| `!proto crc help <name>`  | Show CRC algorithm parameters and description                                    |
| `!proto crc calc <n> {d}` | Compute CRC over hex bytes, text, or file; omit data to verify check string      |
| `!proto status`           | Show current protocol mode state                                                 |
| `!exit`                   | Exit termapy                                                                     |

Screenshots and logs are saved in the config's subfolder (`termapy_cfg/<name>/`).

### Sending and Receiving Binary Data

Use `!proto send` to send raw bytes and see the response. Mix hex bytes and quoted strings:

```text
!proto send 01 03 00 00 00 0A
  TX: 01 03 00 00 00 0A
  RX: 01 03 14 00 64 00 C8 01 2C ...
  (24 bytes, 12ms)

!proto send "AT+RST\r\n"
  TX: 41 54 2B 52 53 54 0D 0A
  RX: 4F 4B 0D 0A
  (4 bytes, 85ms)

!proto send FF 00 "hello" 0D 0A
  TX: FF 00 68 65 6C 6C 6F 0D 0A
  RX: 41 43 4B
  (3 bytes, 7ms)
```

No line ending is appended — you send exactly the bytes you specify. Responses are collected using timeout-based framing (configurable via `proto_frame_gap_ms`). For longer packets (>16 bytes), output switches to a hex dump with offsets and ASCII sidebar.

Toggle `!proto hex` to show all normal serial I/O as hex bytes instead of decoded text — useful for understanding line endings, looking at binary protocols and knowing what is happening on the wire.

## Config Reference

```json
{
    "config_version": 3,
    "port": "COM4",
    "baudrate": 115200,
    "bytesize": 8,
    "parity": "N",
    "stopbits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "inter_cmd_delay_ms": 0,
    "line_ending": "\r",
    "autoconnect": false,
    "autoreconnect": false,
    "autoconnect_cmd": "",
    "echo_cmd": false,
    "echo_cmd_fmt": "[purple]> {cmd}[/]",
    "log_file": "",
    "show_timestamps": false,
    "show_eol": false,
    "max_grep_lines": 100,
    "command_history_items": 30,
    "title": "",
    "app_border_color": "",
    "max_lines": 10000,
    "repl_prefix": "!",
    "os_cmd_enabled": false,
    "exception_traceback": false,
    "custom_buttons": [
        {"enabled": false, "name": "Btn1", "command": "", "tooltip": "Custom button 1"},
        {"enabled": false, "name": "Btn2", "command": "", "tooltip": "Custom button 2"},
        {"enabled": false, "name": "Btn3", "command": "", "tooltip": "Custom button 3"},
        {"enabled": false, "name": "Btn4", "command": "", "tooltip": "Custom button 4"}
    ]
}
```

### Config Fields

| Field                   | Default                | Description                                                                                              |
| ----------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `config_version`        | `3`                    | Schema version — managed automatically by the migration system, do not edit                              |
| `port`                  | `"COM4"`               | Serial port name                                                                                         |
| `baudrate`              | `115200`               | Baud rate                                                                                                |
| `bytesize`              | `8`                    | Data bits (5, 6, 7, 8)                                                                                   |
| `parity`                | `"N"`                  | Parity: `"N"`, `"E"`, `"O"`, `"M"`, `"S"`                                                                |
| `stopbits`              | `1`                    | Stop bits (1, 1.5, 2)                                                                                    |
| `flow_control`          | `"none"`               | `"none"`, `"rtscts"` (hardware), `"xonxoff"` (software), or `"manual"` (shows DTR/RTS/Break buttons)     |
| `encoding`              | `"utf-8"`              | Character encoding for serial data. Common values: `"utf-8"`, `"latin-1"`, `"ascii"`, `"cp437"`          |
| `inter_cmd_delay_ms`    | `0`                    | Delay in milliseconds between commands in autoconnect sequences and multi-command input (`cmd1 \n cmd2`) |
| `line_ending`           | `"\r"`                 | Appended to each command. `"\r"` CR, `"\r\n"` CRLF, `"\n"` LF                                            |
| `autoconnect`           | `false`                | Connect to the port on startup                                                                           |
| `autoreconnect`         | `false`                | Retry every second if the port drops or fails to open                                                    |
| `autoconnect_cmd`       | `""`                   | Commands to send after connecting, separated by `\n`. Waits for idle between each                        |
| `echo_cmd`              | `false`                | Echo sent commands locally                                                                               |
| `echo_cmd_fmt`          | `"[purple]> {cmd}[/]"` | Rich markup format for echoed commands. `{cmd}` is replaced with the command text                        |
| `log_file`              | `""`                   | Session log path. If empty, uses `<name>.log` in the config's subfolder                                  |
| `show_timestamps`       | `false`                | Prefix each line in the terminal display with `[HH:MM:SS.mmm]`                                           |
| `show_eol`              | `false`                | Show dim `\r` and `\n` markers in serial output for line-ending debugging (see note below)               |
| `max_grep_lines`        | `100`                  | Maximum number of matching lines shown by `!grep`                                                        |
| `command_history_items` | `30`                   | Number of commands to keep in the per-config command history                                             |
| `proto_frame_gap_ms`    | `50`                   | Silence gap (ms) to detect end of a binary protocol frame                                                |
| `title`                 | `""`                   | Title bar center text. Defaults to the config filename                                                   |
| `app_border_color`      | `""`                   | Title bar and output border color. Any CSS color name or hex value                                       |
| `max_lines`             | `10000`                | Maximum lines in the scrollback buffer                                                                   |
| `repl_prefix`           | `"!"`                  | Prefix for local REPL commands (e.g. `!help`, `!cls`)                                                    |
| `os_cmd_enabled`        | `false`                | Enable the `!os` REPL command to run shell commands                                                      |
| `exception_traceback`   | `false`                | Include full stack trace in serial exception output (for debugging)                                      |
| `custom_buttons`        | `[]`                   | Array of custom button objects (see Custom Buttons below)                                                |

**Note on `show_eol`:** This is a debug mode for troubleshooting line-ending mismatches (`\r` vs `\n` vs `\r\n`). When enabled, dim `\r` and `\n` markers appear inline in serial output before the characters are consumed by line splitting. Sent commands also show the configured line ending. Since the markers use ANSI escape sequences, they may interfere with device ANSI color output — turn `show_eol` off when not actively debugging.

### Config Examples

Minimal config for a quick connection:

```json
{
    "port": "COM4",
    "baudrate": 115200,
    "autoconnect": true
}
```

Two devices on different ports, color-coded so you can tell them apart at a glance:

```json
{
    "port": "COM4",
    "baudrate": 115200,
    "title": "Sensor A",
    "app_border_color": "blue",
    "autoconnect": true,
    "autoreconnect": true,
    "autoconnect_cmd": "rev \n help dev"
}
```

```json
{
    "port": "COM7",
    "baudrate": 9600,
    "title": "Sensor B",
    "app_border_color": "green",
    "autoconnect": true
}
```

Manual hardware line control, with DTR/RTS toggle buttons and Break:

```json
{
    "port": "COM4",
    "baudrate": 115200,
    "flow_control": "manual",
    "title": "Hardware Debug"
}
```

With `flow_control` set to `"manual"`, three extra buttons appear in the toolbar: DTR and RTS (showing current state as DTR:0/DTR:1) and Break (sends a 250ms break signal). This is useful for devices that use DTR or RTS for reset, bootloader entry, or other hardware signaling.

### Custom Buttons

Add custom buttons to the toolbar. The default config includes 4 disabled placeholders — enable them and fill in the fields, or add more entries. Each button has `enabled`, `name`, `command`, and `tooltip` fields. Commands starting with `!` run as REPL commands; everything else is sent to the serial device. Use `\n` to chain multiple commands or use `!run` to run a script file.

```json
{
    "custom_buttons": [
        {"enabled": true, "name": "Reset", "command": "ATZ", "tooltip": "Reset device"},
        {"enabled": true, "name": "Init", "command": "ATZ\\nAT+BAUD=115200\\n!sleep 500ms\\nAT+INFO", "tooltip": "Full init sequence"},
        {"enabled": true, "name": "Status", "command": "!run status_check.run", "tooltip": "Run status script"}
    ]
}
```

Custom buttons appear in the toolbar between the hardware buttons and the system buttons (Log, SS, Scripts, Exit). They update dynamically when you switch or edit configs.

---

## Extending termapy

## Plugins

Extend `termapy` by dropping Python files into plugin folders. Every REPL command — built-in and custom — uses the same plugin interface.

**Plugin locations** (loaded in order, later can override earlier):

1. **Built-in** -- shipped with `termapy` in `src/termapy/builtins/`, always available
2. **Global** -- `termapy_cfg/plugins/*.py`, shared across all configs
3. **Per-config** -- `termapy_cfg/<name>/plugins/*.py`, specific to one config
4. **App hooks** -- commands that need Textual access (screenshots, connect, etc.)

Later plugins can override earlier ones by using the same name.

### Writing a Plugin

Create a `.py` file with a `COMMAND` instance at the end:

```python
# hello.py — drop into termapy_cfg/plugins/ or termapy_cfg/<config>/plugins/
from termapy.plugins import Command, PluginContext

def _handler(ctx: PluginContext, args: str):
    name = args.strip() or "world"
    ctx.write(f"Hello, {name}!")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="hello",
    args="{name}",        # {braces} = optional, <angle> = required, "" = no args
    help="Say hello.",
    handler=_handler,
)
```

No classes to subclass, no registration — the file is discovered automatically when `termapy` starts. The `Command` dataclass gives IDE autocomplete and catches typos at import time.

### Subcommands

Use `sub_commands` for related operations. Users invoke them with dot notation (`!tool.run`):

```python
from termapy.plugins import Command

def _run(ctx, args):
    ctx.write(f"Running {args}...")

def _status(ctx, args):
    ctx.write("All good.")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="tool",
    help="A tool with subcommands.",
    sub_commands={
        "run":    Command(args="<file>", help="Run a file.", handler=_run),
        "status": Command(help="Show status.", handler=_status),
    },
)
```

The user types `!tool.run myfile` or `!tool.status`. Interior nodes without a handler get a synthetic handler that lists their subcommands.

### PluginContext API

The `ctx` object passed to every handler. This is the stable public API for external plugins:

| Method / Attribute          | Description                                           |
| --------------------------- | ----------------------------------------------------- |
| `ctx.write(text, color)`    | Print to the terminal (color is optional)             |
| `ctx.write_markup(text)`    | Print Rich markup text (e.g. `[bold red]Warning![/]`) |
| `ctx.cfg`                   | Current config dict (read-only access)                |
| `ctx.config_path`           | Path to the current `.json` config file               |
| `ctx.is_connected()`        | Check if the serial port is open                      |
| `ctx.serial_write(data)`    | Send bytes to the serial port                         |
| `ctx.serial_wait_idle()`    | Wait until serial output settles                      |
| `ctx.serial_read_raw()`     | Read raw bytes with timeout framing (returns `bytes`) |
| `ctx.serial_io()`           | Context manager for exclusive serial I/O (`with ctx.serial_io():`) |
| `ctx.ss_dir`                | Screenshot directory (`Path`)                         |
| `ctx.scripts_dir`           | Scripts directory (`Path`)                            |
| `ctx.confirm(message)`      | Show Yes/Cancel dialog, return `bool` (scripts only)  |
| `ctx.notify(text)`          | Show a toast notification                             |
| `ctx.clear_screen()`        | Clear the terminal output                             |
| `ctx.save_screenshot(path)` | Save an SVG screenshot to a file                      |
| `ctx.get_screen_text()`     | Get terminal content as plain text                    |

Plugins can use anything from the Python standard library or third-party packages. They interact with `termapy` only through `ctx`.

There is also `ctx.engine` which exposes internal engine state (sequence counters, echo, config save, etc.). This is used by built-in commands and may change between versions — external plugins should avoid it.

### Examples

See `examples/plugins/` for working examples:

- **hello.py** -- minimal greeting command
- **at_test.py** -- send AT commands over serial
- **timestamp.py** -- print the current date/time
- **ping.py** -- send a command and measure response time

A more complete example ships with `--demo`: the `probe.py` plugin in `termapy_cfg/demo/plugins/` demonstrates the drain → write → read → parse cycle for device interaction. Run `!help probe` or `!help.dev probe` to see its documentation.

## Packet Visualizers

The proto debug screen displays packet data using pluggable visualizers. Three are built-in (Hex, Text, Modbus), and you can add your own by dropping a `.py` file into a `viz/` folder.

**Visualizer locations** (loaded in order, later overrides earlier by name):

1. **Built-in** -- shipped with `termapy` in `src/termapy/builtins/viz/`
2. **Per-config** -- `termapy_cfg/<name>/viz/*.py`, specific to one config

A checklist in the proto debug screen selects the active visualizers (multiple can be active at once). A "Show viz string" checkbox displays the raw format spec string above each table. Proto scripts can control which visualizers appear via the `viz` header field (Hex and Text are always included), and individual tests can force a specific visualizer with a per-test `viz` field. Test results scroll into view as they run, and visualizer column data is written to the debug log file alongside raw hex.

### Format Spec Language

Visualizers use a compact format spec language to map packet bytes to named columns. Each column spec is space-separated:

```text
Name:TypeStart-End
```

**Type codes:**

| Code | Meaning | Example | Output |
|------|---------|---------|--------|
| `H`  | Hex (unsigned) | `H1` (1 byte), `H3-4` (2 bytes) | `0A`, `01FF` |
| `D`  | Decimal unsigned | `D1` (1 byte), `D3-4` (2 bytes BE) | `10`, `256` |
| `+D` | Decimal signed | `+D1` (signed byte) | `-1`, `+127` |
| `S`  | ASCII string | `S5-12` (bytes 5-12 as chars) | `Hello...` |
| `F`  | IEEE 754 float | `F1-4` (4-byte float BE) | `3.14` |
| `B`  | Single bit | `B1.0` (bit 0 of byte 1) | `0` or `1` |
| `crc*` | CRC verify | `crc16m_le` | `C5CD` (green/red) |

**Byte indexing** is 1-based. Endianness is expressed by byte order:

- `D3-4` or `D3D4` -- big-endian (byte 3 is MSB)
- `D4-3` or `D4D3` -- little-endian (byte 4 is MSB)
- `H7-*` -- wildcard, byte 7 to end of packet

**CRC columns** use plugin-based algorithms with `_le`/`_be` endianness suffix. CRCs are always at the end of the packet; width comes from the plugin's `WIDTH`:

- `CRC:crc16m_le` -- Modbus CRC-16, little-endian, all preceding bytes
- `CRC:crc16m_le(1-6)` -- explicit data range (bytes 1-6 only)
- `CRC:crc8` -- single-byte CRC (no endianness suffix needed)

### Writing a Visualizer

Create a `.py` file that exports `format_columns` and `diff_columns`. Here's an example for a sensor protocol: 8-char serial number, 16-bit counter (big-endian), three sensor values, and a CRC-16:

```python
# sensor_view.py — drop into termapy_cfg/<config>/viz/
from termapy.protocol import apply_format, parse_format_spec
from termapy.protocol import diff_columns as proto_diff_columns

NAME = "Sensor"
DESCRIPTION = "Sensor protocol — serial, counter, sensors, CRC"
SORT_ORDER = 30                           # optional, default 50

_SPEC = "Serial:S1-8 Counter:D9-10 Temp:D11 Humid:D12 Press:D13 CRC:crc16x_be"

def format_columns(data: bytes) -> tuple[list[str], list[str]]:
    """Return (headers, values) for display."""
    if not data:
        return ["Sensor"], [""]
    return apply_format(data, parse_format_spec(_SPEC))

def diff_columns(
    actual: bytes, expected: bytes, mask: bytes
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (headers, expected_values, actual_values, statuses)."""
    if not expected and not actual:
        return ["Sensor"], [""], [""], ["match"]
    return proto_diff_columns(actual, expected, mask, parse_format_spec(_SPEC))
```

This produces a multi-column table with per-column diff coloring:

```text
           Sensor
 ┌──────────┬──────────┬─────────┬──────┬───────┬───────┬──────┐
 │          │ Serial   │ Counter │ Temp │ Humid │ Press │ CRC  │
 ├──────────┼──────────┼─────────┼──────┼───────┼───────┼──────┤
 │ TX       │ SN001234 │ 42      │ 31   │ 72    │ 101   │ A3B7 │
 │ Expected │ SN001234 │ 42      │ 31   │ 72    │ 101   │ A3B7 │
 │ Actual   │ SN001234 │ 42      │ 32   │ 72    │ 101   │ A3B7 │
 └──────────┴──────────┴─────────┴──────┴───────┴───────┴──────┘
```

The Temp column (32 vs 31) appears in red while matching columns show green. For variable-length protocols, Python logic can build the format spec dynamically:

```python
def _read_resp_spec(data: bytes) -> str:
    """Generate one column per register in a Modbus read response."""
    n_regs = data[2] // 2 if len(data) > 2 else 0
    cols = "Slave:H1 Func:H2 Bytes:D3"
    for i in range(n_regs):
        s = 4 + i * 2
        cols += f" R{i}:D{s}-{s + 1}"
    cols += " CRC:crc16m_le"
    return cols
```

No classes, no Textual dependency. The `parse_format_spec()` and `apply_format()` utilities from `termapy.protocol` handle all the byte extraction and formatting.

### CRC Algorithms

Termapy ships with a built-in catalogue of 62 named CRC algorithms from the [reveng CRC catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm), covering CRC-8 (20), CRC-16 (30), and CRC-32 (12) variants. A generic engine computes any CRC from standard Rocksoft/Williams parameters (poly, init, refin, refout, xorout). Each algorithm includes a description of its typical usage (e.g. "Modbus RTU serial protocol", "iSCSI, SCTP, Castagnoli"). Use `!proto crc list` to browse algorithms, `!proto crc help <name>` for full parameters, and `!proto crc calc` to compute CRCs interactively. `calc` auto-detects hex bytes vs plain text, accepts a file path to CRC file contents, and with no data runs the standard check string "123456789" with pass/fail verification.

Use readable names directly in format specs:

```text
CRC:crc16-modbus_le          # Modbus CRC-16, little-endian
CRC:crc16-xmodem_be          # XMODEM CRC-16, big-endian
CRC:crc16-ccitt-false_be     # CCITT-FALSE — same poly as XMODEM, different init
CRC:crc32-iscsi_be           # CRC-32C / Castagnoli
CRC:crc8-maxim               # 1-byte CRC, no endianness suffix needed
```

Backward-compatible aliases: `crc16m` = `crc16-modbus`, `crc16x` = `crc16-xmodem`.

**Custom plugins** for non-standard checksums (sum8, sum16, or user-custom algorithms) are `.py` files in `builtins/crc/` or `termapy_cfg/<name>/crc/`:

```python
NAME = "sum8"             # algorithm identifier
WIDTH = 1                 # width in bytes (1, 2, or 4)

def compute(data: bytes) -> int:
    """Compute checksum over data bytes."""
    return sum(data) & 0xFF
```

Plugins override catalogue entries of the same name.

### Visualizer API Reference

| Export                                          | Required | Default | Description                                                 |
| ----------------------------------------------- | -------- | ------- | ----------------------------------------------------------- |
| `NAME`                                          | yes      | —       | Checkbox label and table header                             |
| `format_columns(data)`                          | yes      | —       | Return `(headers, values)` lists for TX/Expected rows       |
| `diff_columns(actual, expected, mask)`          | yes      | —       | Return `(headers, exp_vals, act_vals, statuses)` for diffs  |
| `format_spec(data)`                             | no       | `""`    | Return the raw format spec string for display               |
| `DESCRIPTION`                                   | no       | `""`    | Tooltip text for the checkbox                               |
| `SORT_ORDER`                                    | no       | `50`    | Checkbox ordering (lower = first, built-ins use 10/20)      |

**Utilities from `termapy.protocol`:**

| Function | Description |
| -------- | ----------- |
| `parse_format_spec(spec)` | Parse a format spec string into `list[ColumnSpec]` |
| `apply_format(data, columns)` | Apply column specs to data, return `(headers, values)` |
| `diff_columns(actual, expected, mask, columns)` | Compare with per-column status: `match`, `mismatch`, `extra`, `missing` |
| `diff_bytes(expected, actual, mask)` | Per-byte comparison returning status list |
| `get_crc_registry()` | Get loaded CRC algorithms dict |

## Threading Model

Textual runs on a single async event loop — any blocking call on that loop freezes the UI. Termapy uses Textual's `@work(thread=True)` decorator to run blocking operations in background OS threads. Workers post UI updates back to the main thread via `call_from_thread()`.

| Worker              | Lifetime    | Purpose                                                 |
| ------------------- | ----------- | ------------------------------------------------------- |
| `read_serial()`     | Long-lived  | Reads serial data in a loop, posts lines to the RichLog |
| `_auto_reconnect()` | Short-lived | Retries serial connection every second until success    |
| `_run_lines()`      | Short-lived | Sends multiple commands with inter-command delay        |
| `_run_script()`     | Short-lived | Executes a `.run` script file line by line              |
| `_send_test()`      | Short-lived | Runs a single protocol test case (send/receive/match)   |
| `_run_cmds()`       | Short-lived | Sends setup/teardown commands for protocol tests        |

Only `read_serial()` is long-lived. The others start, do their blocking work, and exit. At most two workers run concurrently: the serial reader plus one command/script/test worker. The proto debug workers use `set_proto_active(True)` to suppress normal serial display while they control the port directly.

Thread-safe communication uses `call_from_thread()` for UI updates and `queue.Queue` for raw RX bytes. `threading.Event` objects (`stop_event`, `reader_stopped`, `_script_stop`) handle inter-thread signaling.

## Test Coverage

![coverage](https://img.shields.io/badge/coverage-96%25-brightgreen) *of testable library code — see note below*

487 tests across 9 test files. Run with `uv run pytest`.

| Module         | Coverage | Test file                            |
| -------------- | -------- | ------------------------------------ |
| `scripting.py` | 100%     | `test_scripting.py`                  |
| `migration.py` | 100%     | `test_migration.py`                  |
| `hex_view.py`  | 100%     | `test_protocol.py`                   |
| `text_view.py` | 97%      | `test_protocol.py`                   |
| `plugins.py`   | 99%      | `test_plugins.py`                    |
| `repl.py`      | 96%      | `test_engine.py`, `test_repl_cfg.py` |
| `protocol.py`  | 88%      | `test_protocol.py`                   |
| `config.py`    | 78%      | `test_app_config.py`                 |

### What's excluded from coverage and why

The modules below are **excluded from coverage metrics** because they cannot be meaningfully unit-tested without a running Textual application or a live import loader:

| Excluded module  | Lines | Why excluded                                                                                       | How tested                    |
| ---------------- | ----- | -------------------------------------------------------------------------------------------------- | ----------------------------- |
| `app.py`         | ~1500 | Textual UI layer — widgets, serial I/O, button handlers, async workers. Requires a running TUI app | Manual testing                |
| `proto_debug.py` | ~1080 | Modal debug screen with Textual widgets. Requires a running TUI app                                | Manual testing                |
| `builtins/*.py`  | ~200  | Loaded dynamically via `importlib`; coverage cannot map them back to source files                  | `test_builtins.py` (indirect) |

This separation is deliberate: pure logic lives in testable modules (`protocol.py`, `config.py`, `repl.py`, `plugins.py`, `scripting.py`, `migration.py`) with high coverage, while UI code lives in `app.py` and `proto_debug.py` where it is tested manually.

## How Does Termapy Compare?

See [COMPARISON.md](COMPARISON.md) for an honest feature comparison against RealTerm, CoolTerm, Tera Term, Docklight, and HTerm.
