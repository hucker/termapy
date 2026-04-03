# Termapy UI Guide

## Starting Termapy

Run termapy from the command line:

```text
termapy                          # auto-detect config
termapy my_device.cfg            # load a specific config file
termapy --cfg-dir /path/to/cfgs  # use a custom config directory
termapy --check my_device.cfg    # validate config (no UI)
```

**Config file argument** — pass the path to a JSON config file directly.
If the file doesn't exist, termapy creates it with default settings.

**--cfg-dir** — override the config directory location. By default,
termapy stores everything in a `termapy_cfg/` folder in the current
working directory. Use this flag to point to a different location,
for example a shared network folder or a project-specific directory.

**--check** — validate a config file and print JSON results to stdout
without launching the UI. Checks baud rate, parity, data bits, stop bits,
flow control, encoding, and flags unknown keys. Read-only — never modifies
the file.

**No arguments** — termapy looks in `termapy_cfg/` for config files:

- If one config exists, it loads automatically.
- If multiple configs exist, a picker dialog appears.
- If no configs exist, you are prompted to name a new one and an editor opens with defaults.

All data for each config (logs, screenshots, scripts, command history,
plugins) is stored alongside its JSON file in a subfolder:

```text
termapy_cfg/
├── iot_device/
│   ├── iot_device.cfg         # config file
│   ├── iot_device.log         # session log
│   ├── .cmd_history.txt       # command history
│   ├── ss/                    # screenshots
│   ├── run/                   # script files
│   ├── proto/                 # protocol test scripts (.pro)
│   ├── viz/                   # per-config packet visualizers
│   └── plugin/                # per-config plugins
└── plugin/                    # global plugins (all configs)
```

## Title Bar

The title bar buttons (left to right):

- **?** — opens this help guide.
- **#** — toggle line numbers on new output lines (button turns green when active).
- **Cfg** — opens the config picker (New / Edit / Load / Cancel).
- **Title** — shows the config name (or custom title). Click to open the config picker.
- **Port** — shows the port name and baud rate. Click to pick a different serial port.
- **Status** — shows connection status: green **Connected** or red **Disconnected**. Click to toggle the connection.

The title bar color can be set per config with `border_color` to visually distinguish multiple sessions.

## Terminal Output

The main area displays serial data with full ANSI color support. Incoming escape sequences are rendered as colored text, and clear-screen sequences are handled automatically.

The scrollback buffer holds up to `max_lines` lines (default 10,000).

## Command Input

The bottom bar contains a text input for sending commands to the serial device.

- Type a command and press **Enter** to send it over serial.
- **Up/Down** arrows cycle through previous commands (last 30 are kept per config).
- **Escape** clears the input and exits history browsing.
- As you type, ghost-text suggestions appear from REPL commands and device history — press **Right** to accept.
- Prefix a command with `/` to run a local REPL command instead of sending it to the device.

Type `/help` to see all available REPL commands.

## Toolbar Buttons

The bottom bar also has buttons. Some appear based on context:

| Button      | When Visible                 | Action                              |
| ----------- | ---------------------------- | ----------------------------------- |
| **/**       | Always                       | Show REPL command picker            |
| **DTR:0/1** | `flow_control` is `"manual"` | Toggle the DTR hardware line        |
| **RTS:0/1** | `flow_control` is `"manual"` | Toggle the RTS hardware line        |
| **Break**   | `flow_control` is `"manual"` | Send a 250ms serial break signal    |
| **Log**     | Always                       | Open the session log in your editor |
| **SS**      | Always                       | Open the screenshot folder          |
| **Scripts** | Always                       | Pick, run, create, or edit a script |
| **Custom**  | `custom_buttons` enabled     | User-defined command buttons        |
| **Exit**    | Always                       | Close the connection and quit       |

## Keyboard Shortcuts

| Key         | Action                              |
| ----------- | ----------------------------------- |
| **Ctrl+Q**  | Quit (also closes any open dialog)  |
| **Ctrl+L**  | Clear screen                        |
| **Ctrl+P**  | Open command palette                |
| **F5**      | Save SVG screenshot                 |
| **F6**      | Open screenshot folder              |
| **F7**      | Save text screenshot                |
| **Up/Down** | Cycle through command history       |
| **Escape**  | Clear input / exit history browsing |
| **Right**   | Accept type-ahead suggestion        |

## Command Palette

Press **Ctrl+P** to open the command palette, which provides quick access to:

- Select Port
- Connect / Disconnect
- Edit, Load, or Create a Config
- Open Log File
- Delete Log File
- Clear Screen
- Save Screenshots
- Open Screenshot Folder

## REPL Commands

Commands prefixed with `/` (configurable via `cmd_prefix`) run locally instead of being sent to the serial device.

| Command                        | Description                                                                 |
| ------------------------------ | --------------------------------------------------------------------------- |
| `/help [cmd]`                  | List commands or show extended help for one                                 |
| `/help.dev <cmd>`              | Show a command handler's Python docstring                                   |
| `/port [name]`                 | Open a port by name, or show subcommands                                    |
| `/port.list`                   | List available serial ports                                                 |
| `/port.open {name}`            | Connect (optional port override)                                            |
| `/port.close`                  | Disconnect from the serial port                                             |
| `/port.info`                   | Show port status, serial parameters, and hardware lines                     |
| `/port.baud_rate {value}`      | Show or set baud rate (hardware only)                                       |
| `/port.byte_size {value}`      | Show or set data bits (hardware only)                                       |
| `/port.parity {value}`         | Show or set parity (hardware only)                                          |
| `/port.stop_bits {value}`      | Show or set stop bits (hardware only)                                       |
| `/port.flow_control {m}`       | Show or set flow control: none, rtscts, xonxoff, manual                     |
| `/port.dtr {0\|1}`             | Show or set DTR line                                                        |
| `/port.rts {0\|1}`             | Show or set RTS line                                                        |
| `/port.cts`                    | Show CTS state (read-only)                                                  |
| `/port.dsr`                    | Show DSR state (read-only)                                                  |
| `/port.ri`                     | Show RI state (read-only)                                                   |
| `/port.cd`                     | Show CD state (read-only)                                                   |
| `/port.break {ms}`             | Send break signal (default 250ms)                                           |
| `/cfg [key [value]]`           | View or change config values                                                |
| `/cfg.auto <key> <val>`        | Set a config key without confirmation                                       |
| `/cfg.configs`                 | List all config files                                                       |
| `/cfg.load <name>`             | Switch to a different config by name                                        |
| `/ss.svg [name]`               | Save an SVG screenshot                                                      |
| `/ss.txt [name]`               | Save a text screenshot                                                      |
| `/ss.dir`                      | Show the screenshot folder                                                  |
| `/cls`                         | Clear the terminal                                                          |
| `/run <file> {-v}`             | Run a script file (-v/--verbose for per-line timing)                        |
| `/run.list`                    | List .run files in the run/ directory                                       |
| `/run.load <file>`             | Run a script file (same as /run)                                            |
| `/delay <duration>`            | Pause for a duration (e.g. `500ms`, `1.5s`)                                 |
| `/confirm {message}`           | Show Yes/Cancel dialog; Cancel stops a running script (see `at_demo.run`)   |
| `/stop`                        | Abort a running script                                                      |
| `/seq`                         | Show sequence counters                                                      |
| `/seq.reset`                   | Reset all sequence counters to zero                                         |
| `/print <text>`                | Print a message to the terminal                                             |
| `/print.r <text>`              | Print Rich markup text (e.g. `[bold red]Warning![/]`)                       |
| `/show <name>`                 | Show a file                                                                 |
| `/show.cfg`                    | Show the current config file                                                |
| `/echo [on\|off]`              | Toggle command echo                                                         |
| `/echo.quiet <on\|off>`        | Set echo on/off silently (for scripts and on_connect_cmd)                   |
| `/os <cmd>`                    | Run a shell command (requires `os_cmd_enabled`)                             |
| `/grep <pattern>`              | Search scrollback for regex matches (case-insensitive, skips own output)    |
| `/show_line_endings {on\|off}` | Toggle visible `\r` `\n` markers in serial output for line-ending debugging |
| `/edit <file>`                 | Edit a project file (`run/`/`proto/` path)                                  |
| `/edit.cfg`                    | Edit the current config file                                                |
| `/edit.log`                    | Open the session log in the system viewer                                   |
| `/edit.info`                   | Open the info report in the system viewer                                   |
| `/cfg.info {--display}`        | Show project summary; `--display` opens full report in system viewer        |
| `/cfg.files`                   | Show project directory tree                                                 |
| `/proto.send <hex>`            | Send raw hex bytes and display response                                     |
| `/proto.run <file>`            | Run a binary protocol test script (.pro)                                    |
| `/proto.list`                  | List .pro files in the proto/ directory                                     |
| `/proto.load <file>`           | Run a protocol test script (same as /proto.run)                             |
| `/proto.debug <file>`          | Open interactive protocol debug screen for a .pro script                    |
| `/proto.hex [on\|off]`         | Toggle hex display mode for serial I/O                                      |
| `/proto.crc.list {pat}`        | List CRC algorithms (optional glob filter, e.g. `*modbus*`)                 |
| `/proto.crc.help <name>`       | Show CRC algorithm parameters, description, and format spec usage           |
| `/proto.crc.calc <n> {d}`      | Compute CRC over hex bytes, text, or file; omit data to verify check string |
| `/proto.status`                | Show current protocol mode state                                            |
| `/var {name}`                  | List user variables, or show one by name                                    |
| `/var.set <NAME> <value>`      | Set a user variable                                                         |
| `/var.clear`                   | Clear all user variables                                                    |
| `/env.list {pattern}`          | List environment variables (all, by name, or glob)                          |
| `/env.set <name> <value>`      | Set a session-scoped environment variable                                   |
| `/env.reload`                  | Re-snapshot variables from the OS environment                               |
| `/cap.text <f> ...`            | Capture serial text to file for a timed duration                            |
| `/cap.bin <f> ...`             | Capture raw binary bytes to a file                                          |
| `/cap.struct <f> ...`          | Capture binary data, decode with format spec to CSV                         |
| `/cap.hex <f> ...`             | Capture hex text lines, decode with format spec to CSV                      |
| `/cap.stop`                    | Stop an active capture                                                      |
| `/raw <text>`                  | Send text to serial with no variable expansion or transforms                |
| `/exit`                        | Exit termapy                                                                |

## JSON Config File

Each configuration is stored as a JSON file at `termapy_cfg/<name>/<name>.cfg`.
On first run, termapy creates a default config for you. You can edit it
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

### Config Field Reference

| Field                | Default              | Description                                                                                                     |
| -------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------- |
| `port`               | `""`                 | Serial port name (e.g. COM4, /dev/ttyUSB0) -- auto-detected when only one port is available                     |
| `baud_rate`          | `115200`             | Serial baud rate                                                                                                |
| `byte_size`          | `8`                  | Number of data bits per byte (5, 6, 7, or 8)                                                                    |
| `parity`             | `N`                  | Parity checking: None, Even, Odd, Mark, or Space                                                                |
| `stop_bits`          | `1`                  | Number of stop bits (1, 1.5, or 2)                                                                              |
| `flow_control`       | `none`               | Flow control mode: `none`, `rtscts` (hardware), `xonxoff` (software), or `manual` (shows DTR/RTS/Break buttons) |
| `encoding`           | `utf-8`              | Character encoding for serial data (utf-8, latin-1, ascii, cp437)                                               |
| `cmd_delay_ms`       | `0`                  | Milliseconds to wait between commands in autoconnect sequences and multi-command input                          |
| `line_ending`        | `\r`                 | String appended to each sent command: `\r` (CR), `\r\n` (CRLF), or `\n` (LF)                                    |
| `send_bare_enter`    | `false`              | Send the line ending when Enter is pressed with no input (for "press enter to continue" prompts)                |
| `auto_connect`       | `false`              | Automatically connect to the port when the app starts                                                           |
| `auto_reconnect`     | `false`              | Retry connection every 2.5s if the port drops or fails to open (does not control startup)                       |
| `on_connect_cmd`     | ` `                  | Commands to send after connecting, separated by `\n` (waits for idle between each)                              |
| `echo_input`         | `false`              | Show sent commands in the terminal output                                                                       |
| `echo_input_fmt`     | `[purple]> {cmd}[/]` | Rich markup format string for echoed commands (`{cmd}` is replaced with the command text)                       |
| `log_file`           | ` `                  | Path to the session log file (if empty, defaults to `<name>.log` in the config subfolder)                       |
| `show_timestamps`    | `false`              | Prefix each line in the terminal display with `[HH:MM:SS.mmm]`                                                  |
| `max_grep_lines`     | `100`                | Maximum number of matching lines shown by `/grep`                                                               |
| `proto_frame_gap_ms` | `50`                 | Silence gap (ms) to detect end of a binary protocol frame                                                       |
| `title`              | ` `                  | Text shown in the center of the title bar (defaults to the config filename)                                     |
| `border_color`       | ` `                  | Color for the title bar and output border (any CSS color name or hex value like `#ff6600`)                      |
| `max_lines`          | `10000`              | Maximum number of lines kept in the scrollback buffer                                                           |
| `cmd_prefix`         | `/`                  | Prefix that identifies local REPL commands (e.g. `/help`)                                                       |
| `os_cmd_enabled`     | `false`              | Allow the `/os` command to run shell commands (disabled by default for safety)                                  |
| `show_traceback`     | `false`              | Include full stack trace in serial exception output (for debugging)                                             |
| `custom_buttons`     | `[]`                 | Array of custom button objects (see Custom Buttons below)                                                       |

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

| Field     | Description                                   |
| --------- | --------------------------------------------- |
| `enabled` | `true` to show the button, `false` to hide it |
| `name`    | Label displayed on the button                 |
| `command` | Command to execute when clicked               |
| `tooltip` | Hover text for the button                     |

**Command format:**

- Plain text is sent to the serial device (e.g. `"ATZ"`)
- Commands starting with `/` run as REPL commands (e.g. `"/run test.run"`)
- Use `\n` to chain multiple commands (e.g. `"ATZ\nAT+INFO"`)
- Mixed serial and REPL commands work: `"ATZ\n/sleep 500ms\nAT+INFO"`

Custom buttons appear in the toolbar between the hardware buttons and the
system buttons (Log, SS, Scripts, Exit), with a small gap separating them.

## Scripting

Click the **Scripts** button or use `/run <filename>` to work with scripts.
The script picker has four actions:

- **New** — create a new script (opens the editor with a template)
- **Edit** — open the highlighted script in the editor
- **Run** — execute the highlighted script
- **Cancel** — close the picker

The script editor provides syntax highlighting (bash-style) for comments
and a name field. Scripts are saved with a `.run` extension in the per-config
`run/` folder.

Script files support:

- Serial commands (sent to the device)
- `/` prefixed REPL commands (delays, screenshots, print, etc.)
- Nested `/run` calls (up to 5 levels deep — scripts can call other scripts)
- Comments (lines starting with `#`)
- Blank lines (ignored)
- Sequence counters with `{+counter}` for auto-incrementing values

Use `/run script.run -v` (or `--verbose`) for per-line timing output.
Press **Escape** or click the **Stop** button to abort a running script.

## Binary Protocol Testing

The `/proto` command provides binary protocol testing for request-response serial protocols.

### Interactive Send

Send raw hex bytes and see the response:

```text
/proto.send 01 03 00 00 00 0A C5 CD
/proto.send "HELLO\r"
/proto.send 02 "DATA" 03
```

### Protocol Test Scripts

Create `.pro` files in the per-config `proto/` folder with send/expect sequences:

```text
# example.pro
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

Run with `/proto.run example.pro`. Each step reports PASS/FAIL.

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

Toggle hex display for all serial I/O with `/proto.hex on` / `/proto.hex off`.

### Packet Visualizers

The proto debug screen uses pluggable visualizers to decode packet bytes into
named columns. Built-in visualizers (Hex, Text, Modbus) ship with termapy. Add
your own by dropping a `.py` file into `termapy_cfg/<config>/viz/`.

Multiple visualizers can be active at once via the checklist. Enable "Show viz
string" to display the raw format spec above each table. Test results scroll
into view as they run, and visualizer column data (format specs and decoded
values) is written to the debug log file alongside raw hex.

**Selecting visualizers in .pro files:**

Use `viz` in the script header to limit which visualizers appear in the dropdown
(Hex and Text are always available). Use `viz` in a `[[test]]` section to force
that visualizer for the test:

```toml
viz = ["Modbus"]          # header: only show Hex, Text, Modbus in dropdown

[[test]]
name = "Read registers"
viz = "Modbus"            # force Modbus view for this test
send = "01 03 00 00 00 01 84 0A"
expect = "01 03 02 00 07 F9 86"
```

Visualizers use a format spec language to map bytes to columns:

```text
Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le
```

Type codes: `H` (hex), `U` (unsigned decimal), `I` (signed, always +/-),
`S` (string), `F` (float), `B` (bit/bit field), `_` (padding, not displayed),
`crc*` (CRC verify). Byte indices are 1-based; byte order determines
endianness (`U3-4` = big-endian, `U4-3` = little-endian). Use `H7-*` for
variable-length fields. Bit fields: `B1.3` (single bit), `B1-2.7-9` (multi-byte
bit range, LSB-0).

62 named CRC algorithms are built in (from the reveng catalogue): `crc16-modbus`,
`crc16-xmodem`, `crc16-ccitt-false`, `crc8`, `crc32`, `crc32-iscsi`, and many more.
Use `/proto.crc.list` to browse all algorithms with descriptions, `/proto.crc.help`
to see parameters, and `/proto.crc.calc` to compute CRCs interactively. `calc`
auto-detects hex bytes vs plain text, accepts a file path to CRC file contents,
and with no data runs the standard check string "123456789" with pass/fail verification.
Aliases: `crc16m` = `crc16-modbus`, `crc16x` = `crc16-xmodem`. Checksum plugins
(`sum8`, `sum16`) and custom plugins in `termapy_cfg/<name>/crc/`. Endianness
suffix: `_le` or `_be`.

See README.md for full format spec reference and visualizer examples.

## Data Capture

Capture serial output to files without interrupting normal display or logging.

### Text Capture (timed)

``` text
/cap.text <file> timeout=<dur> {mode=new|append} {echo=on|off} {cmd=... (must be last)}
/cap.stop
```

File is always the first argument. All keywords can appear in any order
except `cmd=` which must be last (it consumes everything after it).
Mode defaults to `new`. Duration: e.g. `2s`, `500ms`.
Data is written as ANSI-stripped text, one line at a time.

``` text
/cap.text log.txt timeout=3s cmd=AT+INFO
/cap.text session.txt timeout=10s mode=append
```

### Binary Capture (raw bytes)

``` text
/cap.bin <file> bytes=<N> {mode=new|append} {timeout=<dur>} {cmd=... (must be last)}
/cap.stop
```

Captures raw binary bytes straight to a file.

``` text
/cap.bin raw.bin bytes=256 cmd=read_all
```

### Structured Capture (format spec → CSV)

``` text
/cap.struct <file> fmt=<spec> records=<N> {mode=new|append} {sep=comma|tab|space} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}
/cap.hex   <file> fmt=<spec> records=<N> {mode=new|append} {sep=comma|tab|space} {echo=on|off} {timeout=<dur>} {cmd=... (must be last)}
/cap.stop
```

Use `fmt=` with the protocol format spec language to define the record structure.
`/cap.struct` reads raw bytes; `/cap.hex` reads hex-encoded text lines.

- `records=N`: number of records (record size derived from format spec)
- `bytes=N`: alternative — total byte count (must be multiple of record size)
- `sep=comma|tab|space`: column separator (default comma)
- `echo=on|off`: print formatted values to terminal (default off)
- `mode=new|append`: file mode (default new)
- Header row written when columns have names (e.g. `Temp:U1-2`)

``` text
/cap.struct data.csv fmt=Val:U1-2 records=50 cmd=AT+BINDUMP u16 50
/cap.struct sensors.csv fmt=Temp:U1-2 Volts:F3-6 Status:H7 records=100 cmd=read
/cap.struct log.tsv fmt=A:U1-2 B:U3-4 records=50 sep=tab echo=on
```

Bare filenames are saved to the per-config `cap/` directory.
A progress bar and Stop button overlay the toolbar during capture.

### Auto-Numbered Filenames

Use `$(n000)` in filenames for auto-incrementing sequence numbers.
The number of zeros sets the digit width (max 3). A counter file in `cap/`
tracks the last-used number across sessions, with rollover.

| Pattern   | Range   |
| --------- | ------- |
| `$(n0)`   | 0–9     |
| `$(n00)`  | 00–99   |
| `$(n000)` | 000–999 |

``` text
/cap.text log_$(n000).txt timeout=3s cmd=AT+INFO
# → log_000.txt, log_001.txt, log_002.txt, ...
```

## Demo Mode

Try termapy without hardware using the built-in simulated device:

```sh
termapy --demo
```

This creates a `termapy_cfg/demo/` config that auto-connects to a simulated serial device. You can also set `"port": "DEMO"` in any config file.

### Available Commands

| Command                   | Response                                         |
| ------------------------- | ------------------------------------------------ |
| `AT`                      | `OK`                                             |
| `AT+INFO`                 | Device info, uptime, free memory                 |
| `AT+TEMP`                 | Simulated temperature reading                    |
| `AT+LED on\|off`          | Toggle LED state                                 |
| `AT+STATUS`               | LED state, uptime, connections                   |
| `AT+NAME` / `AT+NAME=val` | Query or set device name                         |
| `AT+BAUD` / `AT+BAUD=val` | Query or set baud rate                           |
| `AT+PROD-ID`              | Returns product ID (`BASSOMATIC-77`)             |
| `AT+RESET`                | Simulated reboot sequence                        |
| `mem <addr> [len]`        | Hex memory dump                                  |
| `AT+TEXTDUMP <n>`         | Emit n lines of text readings                    |
| `AT+BINDUMP <n>`          | Emit n mixed 21-byte records (S10+U8+U16+U32+F4) |
| `AT+BINDUMP <type> <n>`   | Emit n typed binary values                       |
| `help`                    | List available commands                          |

### Bundled Files

The demo config includes example scripts and protocol tests:

- **Scripts:** `at_demo.run`, `smoke_test.run`, `status_check.run`
- **Proto:** `at_test.pro` (AT command tests), `bitfield_inline.pro`, `modbus_inline.pro` (Modbus RTU tests)
- **Plugin:** `probe.py` — demo plugin showing serial I/O (drain → write → read → parse). Try `/probe` to run a device survey, or `/help.dev probe` to see the annotated source as a plugin-writing guide.

The simulated device also responds to binary Modbus RTU frames (function codes 0x03 read registers, 0x06 write register) for proto debug testing.

### Try These Commands

```sh
AT                              # connection test → OK
AT+INFO                         # device info
AT+LED on                       # turn LED on
AT+STATUS                       # check LED state, uptime
mem 0x1000 32                   # hex memory dump
```

For Modbus binary commands, use `/proto.send` with hex bytes (CRC included):

```sh
/proto.send 01 03 00 00 00 01 84 0A       # read 1 register from addr 0
/proto.send 01 06 00 05 04 D2 1B 56       # write register 5 = 1234
/proto.send 01 03 00 05 00 01 94 0B       # read back register 5
```
