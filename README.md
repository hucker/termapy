# termapy

*Pronounced "terapee" — as in, therapy for your serial port problems.*

A TUI serial terminal with ANSI color support, built on [Textual](https://textual.textualize.io/) and [pyserial](https://pyserial.readthedocs.io/).

## Install

Requires Python 3.11+.

The recommended way to run termapy is with [uv](https://docs.astral.sh/uv/), which handles dependencies automatically:

```sh
uv run termapy
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

On first run with no config files, termapy prompts for a config name and opens an editor with defaults. If one config exists it loads automatically. If multiple exist, a picker dialog appears. Any missing fields are added from defaults and saved back.

Config files are organized in `termapy_cfg/<name>/<name>.json`, with logs, screenshots, scripts, and command history stored alongside each config in its subfolder:

```text
termapy_cfg/
├── plugins/                            # global plugins (all configs)
│   └── hello.py
├── iot_dev/
│   ├── iot_dev.json                    # config file
│   ├── iot_dev.txt                     # session log
│   ├── .cmd_history.txt                # command history
│   ├── ss/                             # screenshots
│   │   ├── screenshot_20260306_141523.svg
│   │   └── screenshot_20260306_141530.txt
│   ├── scripts/                        # script files for !!run
│   │   └── init_sequence.txt
│   └── plugins/                        # per-config plugins
│       └── custom_init.py
└── sensor_b/
    ├── sensor_b.json
    ├── sensor_b.txt
    ├── .cmd_history.txt
    ├── ss/
    ├── scripts/
    └── plugins/
```

## Features

- **ANSI terminal emulation** -- renders color escape sequences and handles clear-screen
- **JSON config files** -- create, load, edit, and switch configs from within the app; each config gets its own subfolder
- **Interactive title bar** -- clickable buttons for port selection, config switching, and connect/disconnect with red/green status
- **Auto-connect and auto-reconnect** -- reconnects on port drop with retry
- **Auto-login commands** -- send a sequence of commands on connect (separated by `\n` in config)
- **Session logging** -- timestamped plain-text log stored per-config, with optional date-stamped commands
- **Screenshots** -- save the terminal view as SVG (F5) or plain text (F7), open the containing folder (F6); an SS button appears when screenshots exist
- **Hardware line control** -- toggle DTR/RTS and send Break when `flow_control` is `"manual"` (see example below)
- **REPL commands** -- type `!!help` for local commands: screenshots, clear screen, run shell commands, inline config editing
- **Scripting** -- run script files with serial commands, delays, and REPL commands; scripts are resolved from the per-config `scripts/` folder; sequence counters with auto-increment; a Scripts button appears automatically when scripts exist
- **Command history** -- press Up to recall the last 10 commands, persisted per-config
- **Local echo** -- optionally echo sent commands with configurable Rich markup formatting
- **Color-coded sessions** -- set `app_border_color` per config to visually distinguish multiple serial connections
- **Plugins** -- drop `.py` files into `plugins/` folders to add custom REPL commands; all built-in commands use the same plugin architecture

## Plugins

Extend termapy by dropping Python files into plugin folders. Every REPL command — built-in and custom — uses the same plugin interface.

**Plugin locations** (loaded in order, later can override earlier):

1. **Built-in** -- shipped with termapy in `src/termapy/builtins/`, always available
2. **Global** -- `termapy_cfg/plugins/*.py`, shared across all configs
3. **Per-config** -- `termapy_cfg/<name>/plugins/*.py`, specific to one config
4. **App hooks** -- commands that need Textual access (screenshots, connect, etc.)

Later plugins can override earlier ones by using the same `NAME`.

### Writing a Plugin

Create a `.py` file with four things:

```python
# hello.py — drop into termapy_cfg/plugins/ or termapy_cfg/<config>/plugins/
NAME = "hello"
ARGS = "{name}"        # {braces} = optional, <angle> = required, "" = no args
HELP = "Say hello."

def handler(ctx, args):
    name = args.strip() or "world"
    ctx.write(f"Hello, {name}!")
```

That's it. No imports from termapy, no classes to subclass, no registration. The file is discovered automatically when termapy starts.

### Namespacing with PACKAGE

To avoid name collisions, add an optional `PACKAGE` field. The command becomes `!!package.name`:

```python
# flash.py
PACKAGE = "acme"
NAME = "flash"
ARGS = "<firmware>"
HELP = "Flash firmware to the device."

def handler(ctx, args):
    ctx.write(f"Flashing {args}...")
    ctx.serial_write(b"FLASH\r\n")
    ctx.serial_wait_idle()
```

The user types `!!acme.flash firmware.bin`, and `!!help` groups it under the "acme" package.

### PluginContext API

The `ctx` object passed to every handler. This is the stable public API for external plugins:

| Method / Attribute | Description |
| ------------------ | ----------- |
| `ctx.write(text, color)` | Print to the terminal (color is optional) |
| `ctx.cfg` | Current config dict (read-only access) |
| `ctx.config_path` | Path to the current `.json` config file |
| `ctx.is_connected()` | Check if the serial port is open |
| `ctx.serial_write(data)` | Send bytes to the serial port |
| `ctx.serial_wait_idle()` | Wait until serial output settles |
| `ctx.ss_dir` | Screenshot directory (`Path`) |
| `ctx.scripts_dir` | Scripts directory (`Path`) |
| `ctx.notify(text)` | Show a toast notification |
| `ctx.clear_screen()` | Clear the terminal output |
| `ctx.save_screenshot(path)` | Save an SVG screenshot to a file |
| `ctx.get_screen_text()` | Get terminal content as plain text |

Plugins can use anything from the Python standard library or third-party packages. They interact with termapy only through `ctx`.

There is also `ctx.engine` which exposes internal engine state (sequence counters, echo, config save, etc.). This is used by built-in commands and may change between versions — external plugins should avoid it.

### Examples

See `examples/plugins/` for working examples:

- **hello.py** -- minimal greeting command
- **at_test.py** -- send AT commands over serial
- **timestamp.py** -- print the current date/time

## Keyboard Shortcuts

| Key    | Action                 |
| ------ | ---------------------- |
| Ctrl+C | Quit                   |
| Ctrl+L | Clear screen           |
| F5     | Save SVG screenshot    |
| F6     | Open screenshot folder |
| F7     | Save text screenshot   |
| Up     | Command history        |
| Ctrl+P | Command palette        |

## REPL Commands

Type commands prefixed with `!!` (configurable via `repl_prefix`) to run local actions instead of sending to the serial device.

| Command | Description |
| ------- | ----------- |
| `!!help [cmd]` | List all REPL commands, or show help for one |
| `!!connect` | Connect to the serial port |
| `!!disconnect` | Disconnect from the serial port |
| `!!port [name \| list]` | Open a port by name, or list available ports |
| `!!cfg [key [value]]` | Show config, show a key, or change a value (with confirmation) |
| `!!cfg_auto <key> <value>` | Set a config key immediately (no confirmation) |
| `!!ss_svg [name]` | Save SVG screenshot |
| `!!ss_txt [name]` | Save text screenshot |
| `!!ss_dir [path]` | Set or show the screenshot folder |
| `!!clr` | Clear the terminal screen |
| `!!run <filename>` | Run a script file (checks `scripts/` folder then cwd); or use the Scripts button |
| `!!delay <duration>` | Wait for a duration (e.g. `500ms`, `1.5s`) |
| `!!stop` | Abort a running script |
| `!!seq [reset]` | Show or reset sequence counters |
| `!!print <text>` | Print a message to the terminal |
| `!!show <name>` | Show a file (`$cfg` for current config) |
| `!!echo [on \| off]` | Toggle REPL command echo |
| `!!os <cmd>` | Run a shell command (10s timeout, requires `os_cmd_enabled`) |

Screenshots and logs are saved in the config's subfolder (`termapy_cfg/<name>/`).

## Config Reference

```json
{
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
    "add_date_to_cmd": false,
    "title": "",
    "app_border_color": "",
    "max_lines": 10000,
    "repl_prefix": "!!",
    "os_cmd_enabled": false
}
```

### Config Fields

| Field | Default | Description |
| ----- | ------- | ----------- |
| `port` | `"COM4"` | Serial port name |
| `baudrate` | `115200` | Baud rate |
| `bytesize` | `8` | Data bits (5, 6, 7, 8) |
| `parity` | `"N"` | Parity: `"N"`, `"E"`, `"O"`, `"M"`, `"S"` |
| `stopbits` | `1` | Stop bits (1, 1.5, 2) |
| `flow_control` | `"none"` | `"none"`, `"rtscts"` (hardware), `"xonxoff"` (software), or `"manual"` (shows DTR/RTS/Break buttons) |
| `encoding` | `"utf-8"` | Character encoding for serial data. Common values: `"utf-8"`, `"latin-1"`, `"ascii"`, `"cp437"` |
| `inter_cmd_delay_ms` | `0` | Delay in milliseconds between commands in autoconnect sequences and multi-command input (`cmd1 \n cmd2`) |
| `line_ending` | `"\r"` | Appended to each command. `"\r"` CR, `"\r\n"` CRLF, `"\n"` LF |
| `autoconnect` | `false` | Connect to the port on startup |
| `autoreconnect` | `false` | Retry every second if the port drops or fails to open |
| `autoconnect_cmd` | `""` | Commands to send after connecting, separated by `\n`. Waits for idle between each |
| `echo_cmd` | `false` | Echo sent commands locally |
| `echo_cmd_fmt` | `"[purple]> {cmd}[/]"` | Rich markup format for echoed commands. `{cmd}` is replaced with the command text |
| `log_file` | `""` | Session log path. If empty, uses `<name>.txt` in the config's subfolder |
| `add_date_to_cmd` | `false` | Prepend full datetime to command log entries |
| `title` | `""` | Title bar center text. Defaults to the config filename |
| `app_border_color` | `""` | Title bar and output border color. Any CSS color name or hex value |
| `max_lines` | `10000` | Maximum lines in the scrollback buffer |
| `repl_prefix` | `"!!"` | Prefix for local REPL commands (e.g. `!!help`, `!!clr`) |
| `os_cmd_enabled` | `false` | Enable the `!!os` REPL command to run shell commands |

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
