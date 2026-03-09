# Termapy UI Guide

## Starting Termapy

Run termapy from the command line:

```
termapy                          # auto-detect config
termapy my_device.json           # load a specific config file
termapy --cfg-dir /path/to/cfgs  # use a custom config directory
```

**Config file argument** — pass the path to a JSON config file directly.
If the file doesn't exist, termapy creates it with default settings.

**--cfg-dir** — override the config directory location. By default,
termapy stores everything in a `termapy_cfg/` folder in the current
working directory. Use this flag to point to a different location,
for example a shared network folder or a project-specific directory.

**No arguments** — termapy looks in `termapy_cfg/` for config files:

- If one config exists, it loads automatically.
- If multiple configs exist, a picker dialog appears.
- If no configs exist, you are prompted to name a new one and an editor opens with defaults.

All data for each config (logs, screenshots, scripts, command history,
plugins) is stored alongside its JSON file in a subfolder:

```
termapy_cfg/
├── iot_device/
│   ├── iot_device.json        # config file
│   ├── iot_device.log         # session log
│   ├── .cmd_history.txt       # command history
│   ├── ss/                    # screenshots
│   ├── scripts/               # script files
│   ├── proto/                 # protocol test scripts (.pro)
│   ├── viz/                   # per-config packet visualizers
│   └── plugins/               # per-config plugins
└── plugins/                   # global plugins (all configs)
```

## Title Bar

The title bar buttons (left to right):

- **?** — opens this help guide.
- **#** — toggle line numbers on new output lines (button turns green when active).
- **Cfg** — opens the config picker (New / Edit / Load / Cancel).
- **Title** — shows the config name (or custom title). Click to open the config picker.
- **Port** — shows the port name and baud rate. Click to pick a different serial port.
- **Status** — shows connection status: green **Connected** or red **Disconnected**. Click to toggle the connection.

The title bar color can be set per config with `app_border_color` to visually distinguish multiple sessions.

## Terminal Output

The main area displays serial data with full ANSI color support. Incoming escape sequences are rendered as colored text, and clear-screen sequences are handled automatically.

The scrollback buffer holds up to `max_lines` lines (default 10,000).

## Command Input

The bottom bar contains a text input for sending commands to the serial device.

- Type a command and press **Enter** to send it over serial.
- Press **Up** to recall previous commands (default 30 per config, configurable via `command_history_items`).
- Prefix a command with `!` to run a local REPL command instead of sending it to the device.

Type `!help` to see all available REPL commands.

## Toolbar Buttons

The bottom bar also has buttons. Some appear based on context:

| Button | When Visible | Action |
|--------|-------------|--------|
| **!** | Always | Show REPL command picker |
| **DTR:0/1** | `flow_control` is `"manual"` | Toggle the DTR hardware line |
| **RTS:0/1** | `flow_control` is `"manual"` | Toggle the RTS hardware line |
| **Break** | `flow_control` is `"manual"` | Send a 250ms serial break signal |
| **Log** | Always | View the current session log |
| **SS** | Always | Open the screenshot folder |
| **Scripts** | Always | Pick, run, create, or edit a script |
| **Custom** | `custom_buttons` enabled | User-defined command buttons |
| **Exit** | Always | Close the connection and quit |

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| **Ctrl+C** | Quit |
| **Ctrl+L** | Clear screen |
| **Ctrl+P** | Open command palette |
| **F5** | Save SVG screenshot |
| **F6** | Open screenshot folder |
| **F7** | Save text screenshot |
| **Up** | Recall previous command |

## Command Palette

Press **Ctrl+P** to open the command palette, which provides quick access to:

- Select Port
- Connect / Disconnect
- Edit, Load, or Create a Config
- View Log File
- Delete Log File
- Clear Screen
- Save Screenshots
- Open Screenshot Folder

## REPL Commands

Commands prefixed with `!` (configurable via `repl_prefix`) run locally instead of being sent to the serial device.

| Command | Description |
|---------|-------------|
| `!help [cmd]` | List commands or get help for one |
| `!connect` | Connect to the serial port |
| `!disconnect` | Disconnect from the serial port |
| `!port [name\|list]` | Switch port or list available ports |
| `!cfg [key [value]]` | View or change config values |
| `!cfg_auto <key> <val>` | Set a config key without confirmation |
| `!ss_svg [name]` | Save an SVG screenshot |
| `!ss_txt [name]` | Save a text screenshot |
| `!ss_dir [path]` | Show or set the screenshot folder |
| `!cls` | Clear the terminal |
| `!run <file>` | Run a script file |
| `!delay <duration>` | Pause for a duration (e.g. `500ms`, `1.5s`) |
| `!confirm {message}` | Show Yes/Cancel dialog; Cancel stops a running script |
| `!stop` | Abort a running script |
| `!seq [reset]` | Show or reset sequence counters |
| `!print <text>` | Print a message to the terminal |
| `!rprint <text>` | Print Rich markup text (e.g. `[bold red]Warning![/]`) |
| `!show <name>` | Show a file (`$cfg` for current config) |
| `!echo [on\|off]` | Toggle command echo |
| `!os <cmd>` | Run a shell command (requires `os_cmd_enabled`) |
| `!grep <pattern>` | Search scrollback for regex matches (case-insensitive, skips own output) |
| `!show_eol {on\|off}` | Toggle visible `\r` `\n` markers in serial output for line-ending debugging |
| `!info {--display}` | Show project summary; `--display` opens full report in system viewer |
| `!proto send <hex>` | Send raw hex bytes and display response |
| `!proto run <file>` | Run a binary protocol test script (.pro) |
| `!proto debug <file>` | Open interactive protocol debug screen for a .pro script |
| `!proto hex [on\|off]` | Toggle hex display mode for serial I/O |
| `!proto status` | Show current protocol mode state |

## JSON Config File

Each configuration is stored as a JSON file at `termapy_cfg/<name>/<name>.json`.
On first run, termapy creates a default config for you. You can edit it
from within the app by clicking the center title bar button or using `!cfg`.

Here is an example config for a device called `iot_device`:

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
    "autoconnect": true,
    "autoreconnect": true,
    "autoconnect_cmd": "status\nhelp",
    "echo_cmd": true,
    "echo_cmd_fmt": "[purple]> {cmd}[/]",
    "log_file": "",
    "show_timestamps": false,
    "max_grep_lines": 100,
    "command_history_items": 30,
    "title": "IoT Device",
    "app_border_color": "blue",
    "max_lines": 10000,
    "repl_prefix": "!",
    "os_cmd_enabled": false,
    "exception_traceback": false,
    "custom_buttons": [
        {"enabled": true, "name": "Reset", "command": "ATZ", "tooltip": "Reset device"},
        {"enabled": true, "name": "Init", "command": "ATZ\\nAT+BAUD=115200", "tooltip": "Reset and set baud"}
    ]
}
```

This file would be saved at `termapy_cfg/iot_device/iot_device.json`.

### Config Field Reference

| Field | Default | Description |
|-------|---------|-------------|
| `port` | `COM4` | Serial port name (e.g. COM4, /dev/ttyUSB0) |
| `baudrate` | `115200` | Serial baud rate |
| `bytesize` | `8` | Number of data bits per byte (5, 6, 7, or 8) |
| `parity` | `N` | Parity checking: None, Even, Odd, Mark, or Space |
| `stopbits` | `1` | Number of stop bits (1, 1.5, or 2) |
| `flow_control` | `none` | Flow control mode: `none`, `rtscts` (hardware), `xonxoff` (software), or `manual` (shows DTR/RTS/Break buttons) |
| `encoding` | `utf-8` | Character encoding for serial data (utf-8, latin-1, ascii, cp437) |
| `inter_cmd_delay_ms` | `0` | Milliseconds to wait between commands in autoconnect sequences and multi-command input |
| `line_ending` | `\r` | String appended to each sent command: `\r` (CR), `\r\n` (CRLF), or `\n` (LF) |
| `autoconnect` | `false` | Automatically connect to the port when the app starts |
| `autoreconnect` | `false` | Automatically retry the connection every second if the port drops |
| `autoconnect_cmd` | ` ` | Commands to send after connecting, separated by `\n` (waits for idle between each) |
| `echo_cmd` | `false` | Show sent commands in the terminal output |
| `echo_cmd_fmt` | `[purple]> {cmd}[/]` | Rich markup format string for echoed commands (`{cmd}` is replaced with the command text) |
| `log_file` | ` ` | Path to the session log file (if empty, defaults to `<name>.log` in the config subfolder) |
| `show_timestamps` | `false` | Prefix each line in the terminal display with `[HH:MM:SS.mmm]` |
| `max_grep_lines` | `100` | Maximum number of matching lines shown by `!grep` |
| `command_history_items` | `30` | Number of command history entries saved per config |
| `proto_frame_gap_ms` | `50` | Silence gap (ms) to detect end of a binary protocol frame |
| `title` | ` ` | Text shown in the center of the title bar (defaults to the config filename) |
| `app_border_color` | ` ` | Color for the title bar and output border (any CSS color name or hex value like `#ff6600`) |
| `max_lines` | `10000` | Maximum number of lines kept in the scrollback buffer |
| `repl_prefix` | `!` | Prefix that identifies local REPL commands (e.g. `!help`) |
| `os_cmd_enabled` | `false` | Allow the `!os` command to run shell commands (disabled by default for safety) |
| `exception_traceback` | `false` | Include full stack trace in serial exception output (for debugging) |
| `custom_buttons` | `[]` | Array of custom button objects (see Custom Buttons below) |

## Config Management

Click the **Cfg** button in the title bar, click the config name, or use the
command palette to open the config picker. The picker has four actions:

- **New** — create a new config from defaults (prompts for a name, then opens the editor)
- **Edit** — open the highlighted config in the JSON editor
- **Load** — switch to the highlighted config
- **Cancel** — close the picker

The JSON editor provides:

- **Save** — write changes to the current config file
- **Save As** — save as a new config (creates a new subfolder)
- **Cancel** — discard changes

Invalid JSON is caught before saving, with the error shown inline.

## Custom Buttons

Add custom buttons to the toolbar by configuring `custom_buttons`
in your JSON config. Each button can send serial commands, run REPL commands,
or execute scripts. The default config includes 4 disabled placeholders —
enable them and fill in the fields, or add more entries.

Each button object has these fields:

| Field | Description |
| ----- | ----------- |
| `enabled` | `true` to show the button, `false` to hide it |
| `name` | Label displayed on the button |
| `command` | Command to execute when clicked |
| `tooltip` | Hover text for the button |

**Command format:**

- Plain text is sent to the serial device (e.g. `"ATZ"`)
- Commands starting with `!` run as REPL commands (e.g. `"!run test.run"`)
- Use `\n` to chain multiple commands (e.g. `"ATZ\nAT+INFO"`)
- Mixed serial and REPL commands work: `"ATZ\n!sleep 500ms\nAT+INFO"`

Custom buttons appear in the toolbar between the hardware buttons and the
system buttons (Log, SS, Scripts, Exit), with a small gap separating them.

## Scripting

Click the **Scripts** button or use `!run <filename>` to work with scripts.
The script picker has four actions:

- **New** — create a new script (opens the editor with a template)
- **Edit** — open the highlighted script in the editor
- **Run** — execute the highlighted script
- **Cancel** — close the picker

The script editor provides syntax highlighting (bash-style) for comments
and a name field. Scripts are saved with a `.run` extension in the per-config
`scripts/` folder.

Script files support:

- Serial commands (sent to the device)
- `!` prefixed REPL commands (delays, screenshots, print, etc.)
- Comments (lines starting with `#`)
- Blank lines (ignored)
- Sequence counters with `{+counter}` for auto-incrementing values

## Binary Protocol Testing

The `!proto` command provides binary protocol testing for request-response serial protocols.

### Interactive Send

Send raw hex bytes and see the response:

```
!proto send 01 03 00 00 00 0A C5 CD
!proto send "HELLO\r"
!proto send 02 "DATA" 03
```

### Protocol Test Scripts

Create `.pro` files in the per-config `proto/` folder with send/expect sequences:

```
# modbus_test.pro
@timeout 1000ms
@frame_gap 50ms

label: Read registers
send: 01 03 00 00 00 0A C5 CD
expect: 01 03 14 ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** ** **

label: Write register
send: 01 06 00 01 00 03 98 0B
expect: 01 06 00 01 00 03 98 0B
timeout: 500ms

# Text protocols work too
label: AT query
send: "AT+VERSION?\r"
expect: "V1." ** ** "\r"
```

Run with `!proto run modbus_test.pro`. Each step reports PASS/FAIL.

**Script directives:**

- `@timeout <duration>` — default expect timeout (default 1000ms)
- `@frame_gap <duration>` — silence gap to detect frame end (default 50ms)
- `@strip_ansi` — strip ANSI escape sequences from responses before matching
- `label: <text>` — name for the next step
- `send: <hex or "text">` — transmit raw bytes (no line ending appended)
- `expect: <pattern>` — wait for response and match (`**` = any byte)
- `timeout: <duration>` — per-step timeout override
- `delay: <duration>` — fixed sleep
- `flush: <duration>` — wait for serial silence (resets on each byte), then discard received bytes
- `cmd: <text>` — send a plain text command with config line ending (like typing in terminal)

### Hex Display Mode

Toggle hex display for all serial I/O with `!proto hex on` / `!proto hex off`.

## Demo Mode

Try termapy without hardware using the built-in simulated device:

```sh
termapy --demo
```

This creates a `termapy_cfg/demo/` config that auto-connects to a simulated serial device. You can also set `"port": "DEMO"` in any config file.

### Available Commands

| Command | Response |
| ------- | -------- |
| `AT` | `OK` |
| `AT+INFO` | Device info, uptime, free memory |
| `AT+TEMP` | Simulated temperature reading |
| `AT+LED on\|off` | Toggle LED state |
| `AT+STATUS` | LED state, uptime, connections |
| `AT+NAME` / `AT+NAME=val` | Query or set device name |
| `AT+BAUD` / `AT+BAUD=val` | Query or set baud rate |
| `AT+PROD-ID` | Returns product ID (`BASSOMATIC-77`) |
| `AT+RESET` | Simulated reboot sequence |
| `mem <addr> [len]` | Hex memory dump |
| `help` | List available commands |

### Bundled Files

The demo config includes example scripts and protocol tests:

- **Scripts:** `at_demo.run`, `smoke_test.run`, `status_check.run`
- **Proto:** `at_test.pro` (AT command tests), `modbus_test.pro` (Modbus RTU tests)

The simulated device also responds to binary Modbus RTU frames (function codes 0x03 read registers, 0x06 write register) for proto debug testing.
