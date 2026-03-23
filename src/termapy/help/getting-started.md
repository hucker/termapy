# Getting Started

## Launching `Termapy`

Run `termapy` from the command line:

```text
termapy                          # auto-detect config
termapy my_device.cfg            # load a specific config file
termapy --cfg-dir /path/to/cfgs  # use a custom config directory
termapy --check my_device.cfg    # validate config (no UI)
```

**Config file argument** — pass the path to a JSON config file directly.
If the file doesn't exist, `termapy` creates it with default settings.

**--cfg-dir** — override the config directory location. By default,
termapy stores everything in a `termapy_cfg/` folder in the current
working directory. Use this flag to point to a different location,
for example a shared network folder or a project-specific directory.

**--check** — validate a config file and print JSON results to stdout
without launching the UI. Checks baud rate, parity, data bits, stop bits,
flow control, encoding, and flags unknown keys. Read-only — never modifies
the file.

**No arguments** — `termapy` looks in `termapy_cfg/` for config files:

- If one config exists, it loads automatically.
- If multiple configs exist, a picker dialog appears.
- If no configs exist, you are prompted to name a new one and an editor opens with defaults.

## Folder Layout

All data for each config (logs, screenshots, scripts, command history,
plugins) is stored alongside its JSON file in a subfolder:

```text
termapy_cfg/
├── iot_device/
│   ├── iot_device.cfg         # config file
│   ├── iot_device.log         # session log
│   ├── .cmd_history.txt       # command history
│   ├── ss/                    # screenshots
│   ├── scripts/               # script files
│   ├── proto/                 # protocol test scripts (.pro)
│   ├── cap/                   # data capture output files
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

---

|       |                   |                                     |
| :---: | :---------------: | :---------------------------------: |
|       | [Index](index.md) | [Toolbar & Shortcuts →](toolbar.md) |
