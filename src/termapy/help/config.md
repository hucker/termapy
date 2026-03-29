# Configuration

## JSON Config File

Each configuration is stored as a JSON file at `termapy_cfg/<name>/<name>.cfg`.
On first run, `termapy` creates a default config for you. You can edit it
from within the app by clicking the center title bar button or using `/cfg`.

Here is an example config for a device called `iot_device`:

```json
{
    "config_version": 3,
    "port": "COM4",
    "baud_rate": 115200,
    "byte_size": 8,
    "parity": "N",
    "stop_bits": 1,
    "flow_control": "none",
    "encoding": "utf-8",
    "cmd_delay_ms": 0,
    "line_ending": "\r",
    "send_bare_enter": false,
    "auto_connect": true,
    "auto_reconnect": true,
    "on_connect_cmd": "status\nhelp",
    "echo_input": true,
    "echo_input_fmt": "[purple]> {cmd}[/]",
    "log_file": "",
    "show_timestamps": false,
    "max_grep_lines": 100,
    "title": "IoT Device",
    "border_color": "blue",
    "max_lines": 10000,
    "cmd_prefix": "/",
    "os_cmd_enabled": false,
    "show_traceback": false,
    "custom_buttons": [
        {"enabled": true, "name": "Reset", "command": "ATZ", "tooltip": "Reset device"},
        {"enabled": true, "name": "Init", "command": "ATZ\\nAT+BAUD=115200", "tooltip": "Reset and set baud"}
    ]
}
```

This file would be saved at `termapy_cfg/iot_device/iot_device.cfg`.

## Config Field Reference

| Field                    | Default               | Description                                                                                 |
| ------------------------ | --------------------- | ------------------------------------------------------------------------------------------- |
| `port`                   | `""`                  | Serial port name (e.g. COM4, /dev/ttyUSB0) -- auto-detected when only one port is available |
| `baud_rate`              | `115200`              | Serial baud rate                                                                            |
| `byte_size`              | `8`                   | Data bits per byte (5, 6, 7, or 8)                                                          |
| `parity`                 | `N`                   | Parity: None, Even, Odd, Mark, or Space                                                     |
| `stop_bits`              | `1`                   | Stop bits (1, 1.5, or 2)                                                                    |
| `flow_control`           | `none`                | `none`, `rtscts`, `xonxoff`, or `manual` (shows DTR/RTS/Break buttons)                      |
| `encoding`               | `utf-8`               | Character encoding (utf-8, latin-1, ascii, cp437)                                           |
| `cmd_delay_ms`           | `0`                   | Milliseconds between commands in autoconnect and multi-command input                        |
| `line_ending`            | `\r`                  | Appended to each sent command: `\r`, `\r\n`, or `\n`                                        |
| `send_bare_enter`        | `false`               | Send line ending on empty Enter (for "press enter to continue" prompts)                     |
| `auto_connect`           | `false`               | Connect automatically when the app starts                                                   |
| `auto_reconnect`         | `false`               | Retry connection every second if the port drops                                             |
| `on_connect_cmd`         | ` `                   | Commands to send after connecting, separated by `\n`                                        |
| `echo_input`             | `false`               | Show sent commands in the terminal output                                                   |
| `echo_input_fmt`         | `[purple]> {cmd}[/]`  | Rich markup format for echoed commands                                                      |
| `log_file`               | ` `                   | Session log path (defaults to `<name>.log` in config subfolder)                             |
| `show_timestamps`        | `false`               | Prefix lines with `[HH:MM:SS.mmm]`                                                          |
| `show_line_endings`      | `false`               | Show dim `\r` `\n` markers in serial output for debugging                                   |
| `max_grep_lines`         | `100`                 | Maximum lines shown by `/grep`                                                              |
| `proto_frame_gap_ms`     | `50`                  | Silence gap (ms) to detect end of a binary frame                                            |
| `proto_results_template` | `{name}_results.json` | Filename template for protocol test JSON results                                            |
| `title`                  | ` `                   | Title bar text (defaults to config filename)                                                |
| `border_color`           | ` `                   | Title bar color (CSS name or hex like `#ff6600`)                                            |
| `max_lines`              | `10000`               | Scrollback buffer size                                                                      |
| `cmd_prefix`             | `/`                   | Prefix for local REPL commands                                                              |
| `config_read_only`       | `false`               | Disable Edit button in pickers (`/cfg` still changes in-memory values)                      |
| `os_cmd_enabled`         | `false`               | Allow `/os` to run shell commands                                                           |
| `show_traceback`         | `false`               | Show full stack trace on serial errors                                                      |
| `custom_buttons`         | `[]`                  | Custom button objects (see [Custom Buttons](custom-buttons.md))                             |

## Config Management

Click the **Cfg** button in the title bar, click the config name, or use the
command palette to open the config picker. The picker has four actions:

- **New** — create a new config from defaults. If one serial port is detected it is used automatically; if multiple ports are found a picker is shown before opening the editor.
- **Edit** — open the highlighted config in the JSON editor
- **Load** — switch to the highlighted config. If the configured port is not available, a port picker is shown.
- **Cancel** — close the picker

The JSON editor provides:

- **Save** — write changes to the current config file
- **Save As** — save as a new config (creates a new subfolder)
- **Cancel** — discard changes

Invalid JSON is caught before saving, with the error shown inline.

---
