# termapy

**Project Status:** [![CI](https://github.com/hucker/termapy/actions/workflows/tests.yml/badge.svg)](https://github.com/hucker/termapy/actions/workflows/tests.yml) [![codecov](https://codecov.io/gh/hucker/termapy/graph/badge.svg)](https://codecov.io/gh/hucker/termapy) [![ty](https://img.shields.io/badge/ty-0%20issues-brightgreen)](https://github.com/astral-sh/ty) ![license](https://img.shields.io/badge/license-MIT-green) [![docs](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://hucker.github.io/termapy/)

**Powered by:** [![Textual](https://img.shields.io/badge/Textual-TUI-blue?logo=python)](https://textual.textualize.io/) [![pySerial](https://img.shields.io/badge/pySerial-serial%20I%2FO-orange?logo=python)](https://pyserial.readthedocs.io/) [![zensical](https://img.shields.io/badge/zensical-docs-green)](https://github.com/hucker/zensical)

**Built with:** ![python](https://img.shields.io/badge/python-3.11--3.14-blue) [![uv](https://img.shields.io/badge/uv-package%20manager-blueviolet?logo=astral)](https://docs.astral.sh/uv/) [![pytest](https://img.shields.io/badge/pytest-testing-yellow?logo=pytest)](https://pytest.org/) [![coverage](https://img.shields.io/badge/coverage-71%25-yellow)](https://coverage.readthedocs.io/)

Pronounced "ter-map-ee"

Runs on Windows, macOS, and Linux. A serial interface terminal like PuTTY or Tera Term, but it runs in your terminal, installs in seconds, works well with git and teams, and comes with scripting, protocol testing, and a plugin system built in.

Low time commitment: about 1 minute from scratch, or under 10 seconds if you already have [uv](https://docs.astral.sh/uv/) installed.

![termapy screenshot](img/main.png)

## Install and connect

[uv](https://docs.astral.sh/uv/) is the preferred package manager: a clean install takes under 10 seconds, and subsequent updates are well under a second.

1) Install Python package manager uv (skip if already installed) - **< 1 minute**:

    ```sh
    # Windows (PowerShell)
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

    # macOS / Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2) Install termapy - **< 10 seconds**:

    ```sh
    uv tool install -q "termapy[all]"
    ```

    `[all]` adds the optional MCP server (drive termapy from an LLM) and web mode (HTTP-served TUI). Lazy imports mean unused deps cost nothing at runtime — TUI/CLI users don't pay for MCP, MCP users don't pay for Textual. If you specifically need a slim install (embedded, container), drop the `[all]` and add `[mcp]` or `[web]` only when needed.

3) Run termapy - starts a simulated device, no hardware needed. You're typing commands in seconds:

    ```sh
    termapy --demo
    ```

4) Remove termapy if you don't like it:

    ```sh
    uv tool uninstall termapy
    ```

For a plain-text terminal (no TUI), use CLI mode:

```sh
termapy --cli --demo
```

There's a lot more: scripting, binary protocol testing, every CRC algorithm in the [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) (60+, each verified against its canonical check value in the test suite), custom buttons, plugins, and packet visualizers. Expand any section below.

---

<details>
<summary><strong>First 60 seconds</strong> - connect, type, change settings</summary>

1. **Connect:** click the port button in the title bar, pick your COM port, click the status button to connect (it turns green)
2. **Type:** enter commands in the input box at the bottom and press Enter
3. **Change settings:** click `Cfg` to edit port, baud rate, and other settings through the UI

Everything works through the UI. No config files to edit unless you want to.

</details>

<details>

<summary><strong>Why not just use PuTTY?</strong> - what termapy adds</summary>

PuTTY works. So does minicom, screen, and CoolTerm. Use them if they do what you need. Here's where termapy goes further:

- **Runs anywhere Python does.** Same tool on Windows, macOS, Linux. No GUI installer, no system dependencies.
- **Session logging and screenshots.** Every session is logged. Ctrl+S saves an SVG screenshot you can paste into a report or email.
- **Scripting.** Record a sequence of commands in a text file and replay it with one click. Add delays, prompts, and REPL commands.
- **Data capture.** Capture serial text (timed) or binary data (by byte/record count) to files. Binary captures use the same format spec language as protocol testing to decode mixed-type records into CSV/TSV.
- **Binary protocol testing.** Send raw hex, run scripted send/expect tests with pass/fail, decode Modbus and custom protocols with pluggable visualizers.
- **Plugin system.** Add custom commands with a simple Python API. Drop a file in a folder, define a handler, done. Includes examples to get started.
- **Everything in one folder.** Each device config gets its own subfolder with logs, screenshots, scripts, and plugins. Check it into git so the whole team has the same config.

See the [detailed feature comparison](docs/comparison.md) against RealTerm, CoolTerm, Tera Term, Docklight, and HTerm.

</details>

<details>

<summary><strong>Who this is not for</strong> - save yourself some time</summary>

- **You just need a simple serial terminal.** If you open PuTTY, type `AT`, see `OK`, and you're done, keep using PuTTY (or `screen /dev/ttyUSB0 115200` on Linux/macOS). Termapy is built for people who hit the limits of simple terminals and need scripting, protocol testing, data capture, or a plugin system.
- **You don't want Python on your machine.** Termapy is a Python app. [uv](https://docs.astral.sh/uv/) makes installation isolated and fast (it manages its own Python, won't touch your system), but if "install Python" is a deal-breaker, a native app like CoolTerm or RealTerm is a better fit.
- **You need a GUI with menus and mouse-driven workflows.** Termapy runs in your terminal. It has a TUI with buttons and dialogs, but it's keyboard-first and text-based. If you want drag-and-drop or a Windows-native look, try Tera Term or Docklight.

</details>

## Security

Termapy runs with your privileges and does whatever you — or a connected MCP client — tell it, so point it at devices, profiles, and MCP clients you trust. The MCP server is **sandboxed by default**: an automated agent is confined to the serial device and config directory (at worst, a hostile device or profile hangs the session), and each class of host access below stays off until you opt in.

| Host access (off by default) | Opt in with |
| --- | --- |
| Files outside the config directory | `TERMAPY_MCP_FS_UNCONFINED=1` |
| Network sockets (`socket://`, `rfc2217://`) | `TERMAPY_MCP_NET_EGRESS=1` |
| Environment variables (`/env`, `$(env.X)`) | `TERMAPY_MCP_ENV_ENABLED=1` |

<details>
<summary><strong>The basics</strong> - keyboard shortcuts, title bar, REPL commands</summary>

### Keyboard shortcuts

| Key     | Action                              |
| ------- | ----------------------------------- |
| Ctrl+Q  | Quit (also closes any open dialog)  |
| Ctrl+S  | Save SVG screenshot                 |
| Ctrl+T  | Save text screenshot                |
| Ctrl+P  | Command palette                     |
| Up/Down | Cycle through command history       |
| Escape  | Clear input / exit history browsing |
| Right   | Accept type-ahead suggestion        |

See [Environment and compatibility](src/termapy/help/environment.md)
for OS / terminal quirks (VS Code integrated terminal key capture,
macOS Option-as-Meta, KVM cross-platform keyboards, etc.).

### Title bar

| Button | Action                                                              |
| ------ | ------------------------------------------------------------------- |
| `?`    | Open the help guide                                                 |
| `#`    | Toggle line numbers (green when active)                             |
| `Cfg`  | Open the config picker                                              |
| `Run`  | Open the script picker                                              |
| Center | Click to edit the current config                                    |
| Port   | Click to select a serial port                                       |
| Status | Click to connect/disconnect (red = disconnected, green = connected) |

### REPL commands

Type `/` to access built-in commands (the prefix is configurable). Type `/help` to list them all.

The most common ones:

| Command                              | Description                                         |
| ------------------------------------ | --------------------------------------------------- |
| `/help [cmd]`                        | List commands or show help for one                  |
| `/port.list`                         | List available serial ports                         |
| `/port.connect {name} {baud} {mode}` | Connect with optional baud rate and mode (e.g. N81) |
| `/port.info`                         | Show port status and parameters                     |
| `/cfg [key [value]]`                 | Open Cfg picker (bare TUI), or get/set values       |
| `/ss.svg [name]`                     | Save SVG screenshot                                 |
| `/cls`                               | Clear the terminal                                  |
| `/run {filename}`                    | Open Run picker (bare TUI), or run a named script   |
| `/term.echo [on \| off]`             | Toggle command echo                                 |
| `/grep <pattern>`                    | Search scrollback                                   |
| `/exit`                              | Exit termapy                                        |

<details>
<summary>Full command list</summary>

| Command                              | Description                                                                   |
| ------------------------------------ | ----------------------------------------------------------------------------- |
| `/help [cmd]`                        | List commands or show extended help for one                                   |
| `/help.dev <cmd>`                    | Show a command handler's Python docstring                                     |
| `/port {name}`                       | Open Port picker (bare TUI), list subcommands (bare CLI), or open by name     |
| `/port.help`                         | Same as `/help port`                                                          |
| `/port.list`                         | List available serial ports                                                   |
| `/port.connect {name} {baud} {mode}` | Connect with optional baud and mode (e.g. /port.connect COM3 9600 N81)        |
| `/port.mode {baud} {mode}`           | Show or set serial mode (e.g. /port.mode 9600 N81)                            |
| `/port.disconnect`                   | Disconnect from the serial port                                               |
| `/port.info`                         | Show port status, serial parameters, and hardware lines                       |
| `/port.baud_rate {value}`            | Show or set baud rate (hardware only)                                         |
| `/port.byte_size {value}`            | Show or set data bits (hardware only)                                         |
| `/port.parity {value}`               | Show or set parity (hardware only)                                            |
| `/port.stop_bits {value}`            | Show or set stop bits (hardware only)                                         |
| `/port.flow_control {m}`             | Show or set flow control: none, rtscts, xonxoff, manual                       |
| `/port.dtr {0\|1}`                   | Show or set DTR line                                                          |
| `/port.rts {0\|1}`                   | Show or set RTS line                                                          |
| `/port.cts`                          | Show CTS state (read-only)                                                    |
| `/port.dsr`                          | Show DSR state (read-only)                                                    |
| `/port.ri`                           | Show RI state (read-only)                                                     |
| `/port.cd`                           | Show CD state (read-only)                                                     |
| `/port.break {ms}`                   | Send break signal (default 250ms)                                             |
| `/cfg [key [value]]`                 | Open Cfg picker (bare TUI), dump JSON (bare CLI), or get/set with args        |
| `/cfg.auto <key> <value>`            | Set an in-memory config key immediately (no confirmation)                     |
| `/cfg.configs`                       | List all config files                                                         |
| `/cfg.load <name>`                   | Switch to a different config by name                                          |
| `/cfg.show`                          | Open the current config file in the system viewer                             |
| `/cfg.help`                          | Same as `/help cfg`                                                           |
| `/ss.svg [name]`                     | Save SVG screenshot                                                           |
| `/ss.txt [name]`                     | Save text screenshot                                                          |
| `/ss.dir`                            | Show the screenshot folder                                                    |
| `/cls`                               | Clear the terminal screen                                                     |
| `/run {filename} {-v}`               | Open Run picker (bare TUI), list scripts (bare CLI), or run; nests 5 deep     |
| `/run.list`                          | List .run files in the run/ directory                                         |
| `/run.load <filename>`               | Run a script file (same as /run)                                              |
| `/run.help`                          | Same as `/help run`                                                           |
| `/delay <duration>`                  | Wait for a duration (e.g. `500ms`, `1.5s`)                                    |
| `/confirm {message}`                 | Show Yes/Cancel dialog; Cancel stops a running script (see `at_demo.run`)     |
| `/stop`                              | Abort a running script                                                        |
| `/seq`                               | Show sequence counters                                                        |
| `/seq.reset`                         | Reset all sequence counters to zero                                           |
| `/print <text>`                      | Print a message to the terminal                                               |
| `/print.r <text>`                    | Print Rich markup text (e.g. `[bold red]Warning![/]`)                         |
| `/show <name>`                       | Show a file                                                                   |
| `/show.cfg`                          | Show the current config file                                                  |
| `/term`                              | Terminal display / session toggles (echo, line_no, timestamps, ...)           |
| `/term.info`                         | Snapshot the state of every `/term.*` toggle                                  |
| `/term.echo [on \| off]`             | Toggle REPL command echo                                                      |
| `/term.echo.silent <on \| off>`      | Set echo on/off without echoing the change (for scripts and on_connect_cmd)   |
| `/term.line_no [on \| off]`          | Toggle line numbers in serial output (TUI only)                               |
| `/term.eol.markers [on \| off]`      | Toggle visible `\r` `\n` markers for line-ending troubleshooting              |
| `/term.output {level}`               | Show or set output level (silent/quiet/normal/verbose)                        |
| `/term.timestamps [on \| off]`       | Toggle `[HH:MM:SS.mmm]` timestamp prefix                                      |
| `/term.hex [on \| off]`              | Toggle hex display of incoming bytes                                          |
| `/term.encoding {name}`              | Show or set byte-decoding encoding (utf-8, latin-1, ...)                      |
| `/term.send_bare_enter [on \| off]`  | Send line ending on empty Enter                                               |
| `/edit <file>`                       | Edit a project file (`run/`/`proto/` path)                                    |
| `/edit.cfg`                          | Edit the current config file                                                  |
| `/edit.log`                          | Open the session log in the system viewer                                     |
| `/edit.info`                         | Open the info report in the system viewer                                     |
| `/os <cmd>`                          | Run a shell command (10s timeout, requires `TERMAPY_OS_CMD_ENABLED=1` in env) |
| `/grep <pattern>`                    | Search scrollback for regex matches (case-insensitive, skips own output)      |
| `/cfg.info {--display}`              | Show project summary; `--display` opens full report in system viewer          |
| `/cfg.files`                         | Show project directory tree                                                   |
| `/proto`                             | Open Proto picker (bare TUI) or show long-help (bare CLI)                     |
| `/proto.help`                        | Same as `/help proto`                                                         |
| `/proto.send <hex>`                  | Send raw hex bytes and/or quoted text, display response as hex (see below)    |
| `/proto.run <file>`                  | Run a binary protocol test script (.pro) with pass/fail                       |
| `/proto.list`                        | List .pro files in the proto/ directory                                       |
| `/proto.load <file>`                 | Run a protocol test script (same as /proto.run)                               |
| `/proto.hex [on \| off]`             | Toggle hex display mode for serial I/O                                        |
| `/proto.crc.list {pat}`              | List available CRC algorithms (optional glob filter)                          |
| `/proto.crc.info <name>`             | Show CRC algorithm parameters and description                                 |
| `/proto.crc.calc <n> {d}`            | Compute CRC over hex bytes, text, or file; omit data to verify check string   |
| `/proto.crc.find <pkt>`              | Identify CRC algorithm from a captured packet (bin= hex or asc= text)         |
| `/proto.status`                      | Show current protocol mode state                                              |
| `/var {name}`                        | List user variables, or show one by name                                      |
| `/var.set <NAME> <value>`            | Set a user variable                                                           |
| `/var.clear`                         | Clear all user variables                                                      |
| `/env.list {pattern}`                | List environment variables (all, by name, or glob)                            |
| `/env.set <name> <value>`            | Set a session-scoped environment variable                                     |
| `/env.reload`                        | Re-snapshot variables from the OS environment                                 |
| `/cap.text <f> ...`                  | Capture serial text to file for a timed duration                              |
| `/cap.bin <f> ...`                   | Capture raw binary bytes to a file                                            |
| `/cap.struct <f> ...`                | Capture binary data, decode with format spec to CSV                           |
| `/cap.hex <f> ...`                   | Capture hex text lines, decode with format spec to CSV                        |
| `/cap.stop`                          | Stop an active capture                                                        |
| `/raw <text>`                        | Send text to serial with no variable expansion or transforms                  |
| `/exit`                              | Exit termapy                                                                  |

</details>

Screenshots and logs are saved in the config's subfolder (`termapy_cfg/<name>/`).

</details>

<details>
<summary><strong>Project files</strong> - config layout, version control, env vars, examples</summary>

On first run, termapy prompts for a config name and creates one with defaults. If one config exists it loads automatically; if multiple exist, a picker appears. You can edit the config file through the UI (`Cfg` button), or change in-memory settings for the current session with `/cfg baud_rate 9600`.

Everything termapy creates (configs, scripts, test files, plugins, logs) lives in one folder. Run `termapy --demo` and you'll see this structure:

```text
termapy_cfg/
├── plugin/                             # global plugins (all configs)
└── demo/
    ├── demo.cfg                        # config file
    ├── demo.log                        # session log
    ├── .cmd_history.txt                # command history
    ├── ss/                             # screenshots
    ├── run/                            # script files for /run
    │   ├── at_demo.run
    │   ├── smoke_test.run
    │   └── status_check.run
    ├── plugin/                         # per-config plugins
    │   ├── cmd.py
    │   ├── probe.py
    │   ├── temp_plot.py
    │   └── traffic.py
    ├── cap/                            # data capture output files
    └── proto/                          # protocol test scripts
        ├── at_test.pro
        ├── bitfield_inline.pro
        └── modbus_inline.pro
```

Your own configs follow the same layout. Create one with `Cfg` → `New` and termapy builds the folder structure automatically.

### Version control

Because everything is in one folder, you can commit it to git alongside your firmware source. Point `--cfg-dir` at a folder in your repo:

```sh
termapy --cfg-dir ./termapy_cfg
```

Clone on another machine, run the same command, and all configs, scripts, and test files are ready to go.

Since COM port names differ between machines, use `$(env.NAME)` placeholders in your config so the same file works everywhere. Set a `COMPORT` environment variable on each machine, and reference it with a fallback:

```json
{
    "port": "$(env.COMPORT|COM4)",
    "baud_rate": 115200,
    "auto_connect": true
}
```

On a machine with `COMPORT=COM7`, termapy connects to COM7. On a machine without `COMPORT` set, it falls back to COM4. The config file on disk keeps the raw `$(env.COMPORT|COM4)` template. It's expanded in memory at load time, so your checked-in config stays portable.

Environment variables work in any string config value, not just `port`:

```json
{
    "port": "$(env.COMPORT|COM4)",
    "title": "$(env.DEVICE_NAME|Dev Board)",
    "log_file": "$(env.LOG_DIR|logs)/session.log"
}
```

You can also manage environment variables at runtime with REPL commands:

| Command                 | Description                                        |
| ----------------------- | -------------------------------------------------- |
| `/env.list {pattern}`   | List variables (all, by name, or glob like `COM*`) |
| `/env.set <name> <val>` | Set a session-scoped variable (in-memory only)     |
| `/env.reload`           | Re-snapshot variables from the OS environment      |

Variables set with `/env.set` are available immediately for `$(env.NAME)` expansion in REPL commands but do not modify the OS environment or the config file.

#### User variables (`$(NAME)`)

User variables let you define values once and reuse them across commands and scripts. This is especially useful when a test references the same address, register, or port in multiple places. Change it once at the top instead of everywhere.

Assign a variable by typing `$(name) = value` (no `/` prefix needed):

```text
$(slave) = 01
$(reg) = 0064
$(count) = 05
```

Use variables in any command, REPL or serial:

```text
/proto.send $(slave) 03 00 $(reg) 00 $(count)
/print Reading $(count) registers from $(slave) at $(reg)
AT+ADDR=$(slave)
```

A typical workflow is a setup script that configures a test, then a test script that uses the variables:

```text
# setup_modbus.run - run this first to configure the test
$(SLAVE) = 01
$(BASE_REG) = 0064
$(NUM_REGS) = 05
/print Configured: slave=$(SLAVE) base=$(BASE_REG) count=$(NUM_REGS)
```

```text
# test_registers.run - uses variables from setup
/proto.send $(SLAVE) 03 00 $(BASE_REG) 00 $(NUM_REGS)
/delay 500ms
/proto.send $(SLAVE) 06 00 $(BASE_REG) 04 D2
```

Run `/run setup_modbus.run` then `/run test_registers.run`. The variables persist across interactive `/run` calls.

| Command             | Description                            |
| ------------------- | -------------------------------------- |
| `$(NAME) = value`   | Set a variable (no `/` prefix needed)  |
| `/var`              | List all defined variables             |
| `/var NAME`         | Show one variable's value (or $(NAME)) |
| `/var.set NAME val` | Set a variable (explicit command form) |
| `/var.clear`        | Clear all variables                    |

**Scope:** Variables persist for the interactive session. They are automatically cleared when a script is launched from the Scripts button or Run menu, but *not* when `/run` is typed interactively or called within a script. This lets you run a setup script to define variables, then run a test script that uses them. Use `/var.clear` to reset manually.

**Naming:** Variable names are case-sensitive (`$(PORT)` and `$(port)` are different variables). Names must start with a letter or underscore and contain only letters, digits, and underscores.

**Built-in time variables:**

| Variable              | Set when                                    | Updates?             |
| --------------------- | ------------------------------------------- | -------------------- |
| `$(LAUNCH_DATETIME)`  | App starts                                  | Never - frozen       |
| `$(SESSION_DATETIME)` | Script launched (Scripts button / Run menu) | Per script launch    |
| `$(DATETIME)`         | Every expansion                             | Always current clock |

Each group also has `_DATE` and `_TIME` variants (e.g. `$(LAUNCH_DATE)`, `$(SESSION_TIME)`).

Any datetime variable takes an optional `strftime` format after a colon — `$(DATETIME:%Y%m%d_%H%M%S)` yields a filename-safe, colon-free stamp (the default `$(DATETIME)` has colons). This replaces the retired `{datetime}` / `{clock}` template placeholders; old scripts and configs are migrated automatically.

**vs. environment variables:** `$(env.NAME)` pulls from the OS environment and works in config files. `$(NAME)` is for user-defined session variables in commands and scripts. Both use the `$(...)` syntax. The `env.` prefix is required to access environment variables explicitly.

**Escaping:** Use `\$` to prevent expansion of a single reference, or `/raw` to skip expansion for an entire line.

Add a `.gitignore` for session files you don't need to track:

```gitignore
# termapy_cfg - keep configs and scripts, ignore session files
termapy_cfg/*/*.log
termapy_cfg/*/.cmd_history.txt
termapy_cfg/*/ss/
```

To specify a config file directly:

```sh
termapy my_device.cfg
```

To override the config directory:

```sh
termapy --cfg-dir /path/to/configs
```

### Config validation

Termapy validates config files on load and when saving from the editor. Invalid serial settings (baud rate, parity, data bits, stop bits, flow control, encoding) and unknown keys (typos) produce yellow warnings in the log window. Non-standard baud rates are flagged but allowed, since some hardware uses custom rates.

To validate a config from the command line without launching the UI:

```sh
termapy --check my_device.cfg
```

This prints a JSON result to stdout and exits:

```json
{"status": "ok"}
```

```json
{"status": "warn", "warnings": ["baud_rate: 115201 is not a standard rate (110, 300, ...)"]}
```

The `--check` flag is read-only. It never modifies the config file.

### Config examples

When you create a new config, termapy writes a complete `.cfg` file with all defaults (~30 lines). Here are some of the settings you can change:

```json
{
    "port": "COM4",
    "baud_rate": 115200,
    "auto_connect": true,
    "auto_reconnect": true,
    "title": "Sensor A",
    "border_color": "blue",
    "on_connect_cmd": "rev \n help dev"
}
```

### Custom buttons

The demo project's "Info" button runs the `/cfg.info` command via a custom button:

```json
{"enabled": true, "name": "Info", "command": "/cfg.info", "tooltip": "Project info"}
```

![Custom Info button in the toolbar](img/custom_info_button.png)

Add toolbar buttons that send commands, run scripts, or chain multiple actions. Use `\n` to separate multiple commands:

```json
{
    "custom_buttons": [
        {"enabled": true, "name": "Reset", "command": "ATZ", "tooltip": "Reset device"},
        {"enabled": true, "name": "Init", "command": "ATZ\\nAT+BAUD=115200\\n/delay 500ms\\nAT+INFO", "tooltip": "Full init sequence"},
        {"enabled": true, "name": "Status", "command": "/run status_check.run", "tooltip": "Run status script"}
    ]
}
```

### Hardware line control

Set `flow_control` to `"manual"` to get DTR, RTS, and Break buttons in the toolbar. This is useful for devices that use these lines for reset or bootloader entry:

```json
{
    "port": "COM4",
    "baud_rate": 115200,
    "flow_control": "manual",
    "title": "Hardware Debug"
}
```

<details>
<summary>Full config reference</summary>

<!-- validate-config-keys -->
```json
{
    "config_version": 29,
    "title": "",
    "border_color": "",
    "max_lines": 10000,
    "default_ui": "tui",
    "vt100_hint": true,
    "cmd_prefix": "/",
    "cli_prompt": "$(CFG)> ",
    "cli_completion": true,
    "config_read_only": false,
    "profile_path": "",
    "validate_typed_args": false,
    "serial": {
        "port": "COM4",
        "baud_rate": 115200,
        "custom_baud": false,
        "byte_size": 8,
        "parity": "N",
        "stop_bits": 1,
        "flow_control": "none"
    },
    "encoding": "utf-8",
    "cmd_delay_ms": 0,
    "protocol": "text",
    "ndjson_field_routing": {
        "response_id": "id",
        "error_field": "error",
        "event_field": "event"
    },
    "default_response_timeout_ms": 1000,
    "auto_connect": false,
    "auto_reconnect": false,
    "on_connect_cmd": "",
    "tui_on_connect_cmd": "",
    "cli_on_connect_cmd": "",
    "mcp_on_connect_cmd": "",
    "eol": "\r",
    "eol_rx": "auto",
    "send_bare_enter": false,
    "echo": false,
    "echo_fmt": "[purple]$(CFG)> {cmd}[/]",
    "log_file": "",
    "show_traceback": false,
    "proto_frame_gap_ms": 50,
    "proto_results_template": "{name}_results.json",
    "timestamps": false,
    "eol_markers": false,
    "line_no": false,
    "hex": false,
    "request_mode": false,
    "request_err_pattern": "(?i)^(ERROR|ERR|FAULT)\\b",
    "strip_device_echo": false,
    "max_grep_lines": 100,
    "file_xfer_root": "",
    "cfg_enabled": true,
    "run_enabled": true,
    "proto_enabled": true,
    "record_enabled": true,
    "custom_buttons": []
}
```

See the **[config field reference](src/termapy/help/config.md#config-field-reference)** for every key, its default, and a one-line description (also in-app under **Help -> Configuration**, and per-key in the config editor).

**Note on `eol_markers`:** This is a debug mode for troubleshooting line-ending mismatches (`\r` vs `\n` vs `\r\n`). When enabled, dim `\r` and `\n` markers appear inline in serial output before the characters are consumed by line splitting. Sent commands also show the configured line ending. Since the markers use ANSI escape sequences, they may interfere with device ANSI color output, so turn `eol_markers` off when not actively debugging.

**Note on device colour (ANSI):** The TUI and CLI render ANSI colour (SGR) from device output inline, so coloured log lines appear coloured. termapy is line-oriented by design and does **not** emulate cursor addressing or full-screen redraws in the TUI - for devices that drive the terminal that way (menus, `top`/`vi` on an embedded console, bootloader UIs), use `--vt100` (see the VT100 mode section below), which hands raw bytes to your own terminal to emulate. In the CLI, `--no-color` (or `/term.color off`) strips colour for clean piping.

</details>

</details>

<details>
<summary><strong>Scripting</strong> - automate command sequences with text files</summary>

![Run menu / script picker dialog](img/run.png)

Create text files with one command per line and run them from the Run button or with the `/run` or the Scripts button. IN the file ines starting with `/` are REPL commands, lines starting with `#` are comments and everything else is sent to the device.

```text
# Quick status check
AT+STATUS
/delay 300ms
AT+TEMP
/delay 300ms
```

Scripts support delays (`/delay 500ms`), screen clearing (`/cls`), confirmation prompts (`/confirm Reset device?`), screenshots, and sequence counters with auto-increment for batch testing. See the demo scripts (`at_demo.run`, `smoke_test.run`) for examples.

</details>

<details>
<summary><strong>Data capture</strong> - timed text capture, structured binary capture to CSV</summary>

Capture serial data to files without interrupting normal terminal display.

**Text capture** (timed, writes decoded text lines):

```sh
/cap.text log.txt timeout=3s cmd=AT+INFO              # capture 3 seconds of text
/cap.text session.txt timeout=10s mode=append          # append, just listen (no command)
```

**Binary capture** (raw bytes to file):

```sh
/cap.bin raw.bin bytes=256 cmd=read_all
```

**Structured capture** (binary data decoded via format spec to CSV):

```sh
# Single-type column - 50 big-endian unsigned 16-bit values
/cap.struct data.csv fmt=Val:U1-2 records=50 cmd=AT+BINDUMP u16 50

# Mixed-type record - string + u8 + u16 + u32 + float (little-endian)
/cap.struct mixed.csv fmt=Label:S1-10 Counter:U11 Val16:U13-12 Val32:U17-14 Temp:F21-18 records=20 cmd=AT+BINDUMP 20

# Tab-separated output with echo to terminal
/cap.struct log.tsv fmt=A:U1-2 B:F3-6 records=100 sep=tab echo=on cmd=read
```

The `fmt=` parameter uses the same format spec language as `/proto`, with type codes `H` (hex), `U` (unsigned), `I` (signed), `S` (string), `F` (float), `B` (bit) and 1-based byte ranges. Byte range order determines endianness: `U1-2` = big-endian, `U2-1` = little-endian. Named columns (`Temp:U1-2`) produce a CSV header row; unnamed columns (`U1-2`) omit it.

| Format spec | C type     | Meaning                          |
| ----------- | ---------- | -------------------------------- |
| `U1`        | `uint8_t`  | 1 unsigned byte                  |
| `U1-2`      | `uint16_t` | 2-byte unsigned, big-endian      |
| `U2-1`      | `uint16_t` | 2-byte unsigned, little-endian   |
| `U1-4`      | `uint32_t` | 4-byte unsigned, big-endian      |
| `U1-8`      | `uint64_t` | 8-byte unsigned, big-endian      |
| `I1`        | `int8_t`   | 1 signed byte                    |
| `I1-2`      | `int16_t`  | 2-byte signed, big-endian        |
| `I1-4`      | `int32_t`  | 4-byte signed, big-endian        |
| `I1-8`      | `int64_t`  | 8-byte signed, big-endian        |
| `F1-4`      | `float`    | 4-byte IEEE 754 float            |
| `F1-8`      | `double`   | 8-byte IEEE 754 double           |
| `S1-10`     | `char[10]` | 10-byte ASCII string             |
| `H1-4`      |            | 4 bytes as hex (e.g. `0A1BFF03`) |

Auto-numbered filenames: use `$(n000)` for a 3-digit rotating sequence (000-999), tracked across sessions in a counter file.

```sh
/cap.text log_$(n000).txt timeout=3s cmd=AT+INFO          # log_000.txt, log_001.txt, ...
/cap.struct data_$(n00).csv fmt=V:U1-2 records=100 cmd=read
```

A progress bar and Stop button overlay the toolbar during capture. The `Cap` button opens the cap/ folder.

</details>

<details>
<summary><strong>Binary protocol testing</strong> - hex send/receive, .pro test scripts, CRC</summary>

Send raw hex bytes and see the response:

```sh
/proto.send 01 03 00 00 00 01 84 0A
  TX: 01 03 00 00 00 01 84 0A
  RX: 01 03 02 00 07 F9 86
  (7 bytes, 12ms)
```

Mix hex and quoted text:

```text
/proto.send "AT+RST\r\n"
/proto.send FF 00 "hello" 0D 0A
```

No line ending is appended; you send exactly the bytes you specify. Toggle `/proto.hex` to show all normal serial I/O as hex bytes.

### Proto test scripts

Write `.pro` files (TOML format) for repeatable send/expect testing with pass/fail:

```toml
name = "Modbus Register Test"
frame_gap = "20ms"

[[test]]
name = "Read 1 register"
send = "01 03 00 00 00 01 84 0A"
expect = "01 03 02 00 07 F9 86"

[[test]]
name = "Write register 5 = 1234"
send = "01 06 00 05 04 D2 1B 56"
expect = "01 06 00 05 04 D2 1B 56"
```

Run with `/proto.run <file>` or from the proto debug screen, which adds repeat count, delay between runs, stop-on-error, scrolling results, and visualizer column data.

<!-- TODO: screenshot - proto debug screen showing test results with pass/fail coloring and visualizer columns -->

### Inline format specs

Add `send_fmt` and `expect_fmt` to any test step to decode raw bytes into named columns. The proto debug screen displays the decoded values side by side with pass/fail highlighting, turning opaque hex into readable fields.

```toml
[[test]]
name = "Read 2 registers"
send = "01 03 00 00 00 02 C4 0B"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 04 00 07 00 14 4B FD"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 R1:U6-7 CRC:crc16-modbus_le"
```

Each column is `Name:TypeBytes` where the type controls how bytes are displayed:

| Type   | Description               | Example               | Display            |
| ------ | ------------------------- | --------------------- | ------------------ |
| `H`    | Hex (uppercase)           | `H1` / `H3-4`         | `0A` / `0A 2B`     |
| `h`    | Hex (lowercase)           | `h1`                  | `0a`               |
| `U`    | Unsigned int (big-endian) | `U3-4`                | `7`                |
| `I`    | Signed int (big-endian)   | `I3-4`                | `-1`               |
| `S`    | ASCII string              | `S3-10`               | `HELLO`            |
| `B`    | Bit field (integer)       | `B4-5.0-2`            | `3`                |
| `b`    | Bit field (binary string) | `b4-5.0-15`           | `0000101000101011` |
| `F`    | IEEE 754 float            | `F3-6`                | `3.14`             |
| `_`    | Padding (skip bytes)      | `_3-4`                | *(hidden)*         |
| `crc*` | CRC auto-check            | `CRC:crc16-modbus_le` | `OK` / `FAIL`      |

Byte indices are 1-based. Ranges use `-` (e.g. `U3-4` = bytes 3-4). **Byte order is controlled by the index direction.** This is how you handle big-endian vs little-endian protocols:

- `U3-4`: big-endian (byte 3 is MSB, byte 4 is LSB)
- `U4-3`: little-endian (byte 4 is LSB, byte 3 is MSB)
- `U5-8`: 32-bit big-endian (4 bytes, MSB first)
- `U8-5`: 32-bit little-endian (4 bytes, LSB first)

This works for all multi-byte types (`U`, `I`, `H`, `F`, `B`). CRC columns auto-compute and verify the checksum over the preceding bytes. Append `_le` or `_be` to the CRC algorithm name for the byte order of the checksum itself:

- `CRC:crc16-modbus_le`: CRC-16/Modbus stored little-endian (low byte first, as Modbus RTU requires)
- `CRC:crc16-modbus_be`: same algorithm but stored big-endian (high byte first)

The demo project includes two `.pro` files that exercise inline format specs: `modbus_inline.pro` (register reads/writes with Modbus decoding) and `bitfield_inline.pro` (bit field extraction and binary display). Run them from the Proto button in `--demo` mode.

Here is the test that generates the Modbus response shown below:

```toml
[[test]]
name = "Read 5 registers from addr 100"
send = "01 03 00 64 00 05 C4 16"
send_fmt = "Title:Modbus_TX Slave:H1 Func:H2 Addr:U3-4 Count:U5-6 CRC:crc16-modbus_le"
expect = "01 03 0A 05 1B 05 28 05 35 05 42 05 4F 8C 46"
expect_fmt = "Title:Modbus_Response Slave:H1 Func:H2 Bytes:U3 R0:U4-5 R1:U6-7 R2:U8-9 R3:U10-11 R4:U12-13 CRC:crc16-modbus_le"
```

![Inline format spec - decoded Modbus columns in proto debug screen](img/proto_inline_fmt.png)

Finally here is the serial log output of a protocol test:

```text
================================================================================
[2026-03-12 22:28:14] Script: Demo AT Command Test | Tests: 4 | Repeat: 1
================================================================================
  [PASS]  AT basic
         TX:  41 54 0D
         EXP: 4F 4B 0D 0A
         RX:  4F 4B 0D 0A
         Time: 77ms
         [Hex] TX spec: Hex:h1-*
         [Hex] TX: Hex=41 54 0D
         [Hex] RX spec: Hex:h1-*
         [Hex] RX: Hex=4F 4B 0D 0A
  [PASS]  LED on
         TX:  41 54 2B 4C 45 44 20 6F 6E 0D
         EXP: 4F 4B 0D 0A
         RX:  4F 4B 0D 0A
         Time: 128ms
         [Hex] TX spec: Hex:h1-*
         [Hex] TX: Hex=41 54 2B 4C 45 44 20 6F 6E 0D
         [Hex] RX spec: Hex:h1-*
         [Hex] RX: Hex=4F 4B 0D 0A
  [PASS]  LED off
         TX:  41 54 2B 4C 45 44 20 6F 66 66 0D
         EXP: 4F 4B 0D 0A
         RX:  4F 4B 0D 0A
         Time: 99ms
         [Hex] TX spec: Hex:h1-*
         [Hex] TX: Hex=41 54 2B 4C 45 44 20 6F 66 66 0D
         [Hex] RX spec: Hex:h1-*
         [Hex] RX: Hex=4F 4B 0D 0A
  [PASS]  Unknown command
         TX:  49 4E 56 41 4C 49 44 0D
         EXP: 45 52 52 4F 52 3A 20 55 6E 6B 6E 6F 77 6E 20 63 6F 6D 6D 61 6E 64 20 27 49 4E 56 41 4C 49 44 27 0D 0A
         RX:  45 52 52 4F 52 3A 20 55 6E 6B 6E 6F 77 6E 20 63 6F 6D 6D 61 6E 64 20 27 49 4E 56 41 4C 49 44 27 0D 0A
         Time: 72ms
         [Hex] TX spec: Hex:h1-*
         [Hex] TX: Hex=49 4E 56 41 4C 49 44 0D
         [Hex] RX spec: Hex:h1-*
         [Hex] RX: Hex=45 52 52 4F 52 3A 20 55 6E 6B 6E 6F 77 6E 20 63 6F 6D 6D 61 6E 64 20 27 49 4E 56 41 4C 49 44 27 0D 0A
Summary: 4/4 PASS (4 tests)
```

### CRC algorithms

Every CRC algorithm in the [reveng catalogue](https://reveng.sourceforge.io/crc-catalogue/all.htm) (maintained by Greg Cook -- see [ACKNOWLEDGMENTS](src/termapy/help/acknowledgments.md)) is built in: 64 of them, with full parameterization (poly, init, refin, refout, xorout) and each one verified against its catalogue check value in the test suite. If you need a CRC and it has a name, termapy already has it, correctly. Browse with `/proto.crc.list`, inspect with `/proto.crc.info <name>`, compute with `/proto.crc.calc`, identify an unknown one from a captured packet with `/proto.crc.find`. You can also generate standalone C, Python, or Rust source for any of them with `/proto.crc.python`, `/proto.crc.c`, `/proto.crc.rust` so you never have to port one by hand again.

</details>

<details>
<summary><strong>Demo mode</strong> - simulated device for trying everything without hardware</summary>

`termapy --demo` launches a completely simulated COM port, no hardware needed. The simulated device ([`BASSOMATIC-77`](https://en.wikipedia.org/wiki/Bass-O-Matic), the natural successor to Dan Aykroyd's '76) responds to AT commands, NMEA/GPS sentences, and binary Modbus RTU frames, so you can exercise every termapy feature: serial I/O, scripting, protocol testing, and plugins.

The demo exists for two reasons: people can evaluate termapy without owning hardware, and the test suite has a deterministic target it can drive end-to-end through the same code paths a real device would.

> **Note:** The demo command sets (AT, NMEA, Modbus) are not validated protocol implementations. They simulate familiar interfaces so you can explore termapy's features without hardware.

<!-- TODO: screenshot - demo mode showing AT command output with the device responding -->

#### ASCII commands

The device supports a full AT command set. Type commands and get responses just like a real device:

| Command            | Description                                       |
| ------------------ | ------------------------------------------------- |
| `AT`               | Connection test (returns `OK`)                    |
| `AT+PROD-ID`       | Product identifier (returns `BASSOMATIC-77`)      |
| `AT+INFO`          | Device info (version, uptime, free memory)        |
| `AT+TEMP`          | Read temperature sensor                           |
| `AT+LED on\|off`   | Control LED                                       |
| `AT+NAME?`         | Query device name                                 |
| `AT+NAME=val`      | Set device name (max 32 chars)                    |
| `AT+BAUD?`         | Query baud rate                                   |
| `AT+BAUD=val`      | Set baud rate (9600, 19200, 38400, 57600, 115200) |
| `AT+STATUS`        | Device status (LED, uptime, connections)          |
| `AT+RESET`         | Reset device (simulates boot sequence)            |
| `mem <addr> [len]` | Hex memory dump (deterministic, max 256 bytes)    |
| `help`             | List all commands                                 |

#### GPS / NMEA commands

The device responds to standard NMEA queries and PMTK configuration commands. Position is fixed at the 50-yard line of Lumen Field, Seattle.

| Command         | Description                                   |
| --------------- | --------------------------------------------- |
| `$GPGGA`        | Position fix (lat, lon, altitude, satellites) |
| `$GPRMC`        | Recommended minimum nav (pos, speed, date)    |
| `$GPGSA`        | DOP and active satellites                     |
| `$GPGSV`        | Satellites in view (elevation, azimuth, SNR)  |
| `$PMTK220,1000` | Set update rate (acknowledged, no effect)     |
| `$PMTK314,...`  | Configure sentence output (acknowledged)      |

#### Binary protocol testing

The device also speaks Modbus RTU (binary), so you can try protocol test files and visualizers. Use `/proto.send` with hex bytes (CRC included):

```sh
/proto.send 01 03 00 00 00 01 84 0A       # read 1 register from addr 0
/proto.send 01 06 00 05 04 D2 1B 56       # write register 5 = 1234
/proto.send 01 03 00 05 00 01 94 0B       # read back register 5
```

Modbus RTU supports function 0x03 (read holding registers) and 0x06 (write single register) with CRC16 enforced.

#### Bundled scripts, tests, and plugins

The demo comes with everything wired up so you can try each feature:

- **Scripts:** `at_demo.run`, `smoke_test.run`, `status_check.run`. Run via the Scripts button or `/run`.
- **Proto test files:** `at_test.pro`, `bitfield_inline.pro`, `modbus_inline.pro`. Run via the Proto button for pass/fail results.
- **Plugins:** `/probe` sends a command sequence and reports results; `/cmd` adds a custom shortcut.

</details>

<details>
<summary><strong>CLI mode</strong> - plain-text terminal, no TUI</summary>

`termapy --cli` runs a plain-text serial terminal in your existing terminal window. No Textual UI, no mouse, just keyboard input and text output. The TUI is the intended way to use termapy, but the CLI exists for two cases: people who don't want a TUI (you know who you are), and pipelines that want to feed termapy's output into a file or another tool. The engine doesn't care which frontend it's running under; the TUI and CLI share the same `ReplEngine`, `SerialEngine`, plugins, scripts, and configs.

```sh
termapy --cli my_device              # interactive REPL
termapy --cli --demo                 # demo device, no hardware needed
termapy --cli smoke_test.run         # run a .run script and exit
termapy --cli my_device -e "AT+VER"  # one-shot: run command, print stdout, exit
termapy --cli my_device --no-color   # strip ANSI color codes (REPL piping)
```

`--cli` covers two shapes of non-TUI use:

- **Interactive REPL** (the default): blocks on stdin, dispatches each line, exits on `/exit` or EOF.
- **One-shot**: pair `--cli` with `--run <script>` (run a `.run` file and exit) or `-e "<command>"` (run one command and exit). Exit status reflects success (`0`) or failure (`1`); cfg-driven connect-time autorun (`on_connect_cmd`) is suppressed so piped/captured stdout contains only the user's output.

Passing a `.run` file to `--cli` automatically infers the config from the file's location and runs it. Passing a config name or path opens an interactive session.

**Features:**

- Rich colored output (toggle with `/color on|off` or `--no-color`)
- Command history shared with TUI (up/down arrows, persisted across sessions)
- Tab completion for REPL commands
- Script execution with `/run` (same scripts work in both TUI and CLI)
- `/delay` with progress bar for waits over 3 seconds (Ctrl+C to cancel)
- All `/port`, `/cfg`, `/var`, `/env`, `/proto.crc`, `/edit` commands work

**TUI-only features** (not available in CLI mode):

- `/ss.svg`, `/ss.txt` - screenshots (prints "not supported" message)
- `/grep` - scrollback search (no scrollback buffer in CLI)
- `/edit.cfg` - opens in system editor instead of built-in config editor
- Mouse interaction, modal dialogs, custom buttons

**Exit:** `/exit`, `/quit`, or Ctrl+C.

</details>

<details>
<summary><strong>VT100 mode</strong> - raw ANSI passthrough for cursor-addressed devices</summary>

`termapy --vt100 <config>` drops the TUI entirely and pipes the device straight to your terminal, so output that uses terminal control beyond plain lines - cursor addressing, full-screen menus, `vi`/`top` on an embedded Linux console, bootloader UIs - renders and responds correctly. termapy doesn't emulate the terminal here; your terminal already is one, so it does the work (this is why it stays a passthrough, not a Textual widget).

```sh
termapy --vt100 my_device      # raw ANSI terminal on the configured port
termapy --vt100 --demo         # no hardware: an interactive ANSI menu/dashboard
```

`--vt100 --demo` connects to a simulated VT100 device that draws a colored, cursor-addressed menu (arrow keys to move, Enter to open) and a live status dashboard - the quickest way to see what passthrough buys you over plain line output. Quit with Ctrl-].

It's a peer of CLI and TUI mode: set `"default_ui": "vt100"` in a config to make it the default, and `--vt100` overrides the config the same way `--cli` does. The byte pump is the vendored pyserial `miniterm`, which enables VT processing on Windows 10+ automatically.

It's also a reversible toggle from inside the TUI, like `/cli` <-> `/tui`: `/vt100` hands the current device to a passthrough terminal, and `/demo.vt100` jumps to the widget tour. In both cases Ctrl-] returns you to the TUI (when launched from the shell with `--vt100`, Ctrl-] quits instead).

**Exit:** Ctrl+] (or back to the TUI, if entered via `/vt100`). Every other key, including Ctrl+T, goes straight to the device - miniterm's settings menu is disabled so the session reads as a native termapy view.

**Caveats:** needs a VT-capable host terminal (universal on macOS/Linux; Windows Terminal / VS Code / Win10+ on Windows; legacy `conhost` is iffy). Run it in a standalone terminal tab for zero extra key interception; VS Code's integrated terminal adds one layer (the same one any serial terminal faces there). For LLM-driven menu navigation, a headless emulator + MCP is the intended path, not passthrough.

</details>

<details>
<summary><strong>Extending termapy</strong> - plugins, subcommands, visualizers</summary>

### Plugins

Every built-in command (`/help`, `/cfg`, `/grep`, all of them) is itself a plugin loaded from the same folder you'd drop your own into. If something was hard to build as a plugin, the API was wrong. [Dogfooding](https://en.wikipedia.org/wiki/Eating_your_own_dog_food) all the way down.

Add custom REPL commands by dropping a `.py` file in a plugin folder. No classes to subclass, no registration:

```python
# hello.py - drop into termapy_cfg/plugin/ or termapy_cfg/<config>/plugin/
from termapy.plugins import Command, PluginContext

def _handler(ctx: PluginContext, args: str):
    name = args.strip() or "world"
    ctx.io.result(f"Hello, {name}!")

# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="hello",
    args="{name}",        # {braces} = optional, <angle> = required, "" = no args
    help="Say hello.",
    handler=_handler,
)
```

**Plugin locations** (loaded in order, later overrides earlier):

1. **Built-in:** shipped with termapy, always available
2. **Global:** `termapy_cfg/plugin/*.py`, shared across all configs
3. **Per-config:** `termapy_cfg/<name>/plugin/*.py`, specific to one config
4. **App hooks:** frontend-specific commands (`/ss`, `/delay`, `/run`, etc.)

<details>
<summary>Subcommands</summary>

Use `sub_commands` for related operations. Users invoke them with dot notation (`/tool.run`):

```python
from termapy.plugins import Command

def _run(ctx, args):
    ctx.io.status(f"Running {args}...")

def _status(ctx, args):
    ctx.io.result("All good.")

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

The user types `/tool.run myfile` or `/tool.status`.

</details>

<details>
<summary>PluginContext API</summary>

The `ctx` object passed to every handler is a thin shell over **five capability handles**, each owning one domain:

| Member                            | Description                                                       |
| --------------------------------- | ----------------------------------------------------------------- |
| `ctx.cfg`                         | Current config (read-only mapping)                                |
| `ctx.config_path`                 | Path to the current `.cfg` config file                            |
| `ctx.io.result(text, color)`      | The command's answer; shown at quiet+ (suppressed at silent)      |
| `ctx.io.output(text, color)`      | Bulk data: listings, dumps; shown at normal+                      |
| `ctx.io.status(text)`             | Progress chatter; shown only at verbose                           |
| `ctx.io.result_markup(text)`      | Like `result` but text is Rich markup (`[red]X[/]`)               |
| `ctx.io.output_markup(text)`      | Like `output` but text is Rich markup                             |
| `ctx.io.status_markup(text)`      | Like `status` but text is Rich markup                             |
| `ctx.io.notify(text)`             | Always-works fallback notification (toast in TUI, plain in CLI)   |
| `ctx.io.log(prefix, text)`        | Write to session log: `">"` TX, `"<"` RX, `"#"` status            |
| `ctx.serial.is_connected`         | Bool: serial port is open                                         |
| `ctx.serial.port`                 | The raw pyserial object, or `None` when disconnected              |
| `ctx.serial.write(data)`          | Send bytes to the serial port (auto-logged as TX)                 |
| `ctx.serial.read_raw(timeout_ms)` | Read raw bytes with timeout framing (returns `bytes`)             |
| `ctx.serial.drain()`              | Drain pending RX data                                             |
| `ctx.serial.wait_idle()`          | Wait until serial output settles                                  |
| `ctx.serial.io()`                 | Context manager for exclusive serial I/O                          |
| `ctx.serial.rx_observer()`        | Context manager: passive RX byte tap                              |
| `ctx.serial.tx_observer()`        | Context manager: passive TX byte tap                              |
| `ctx.fs.ss_dir`                   | Screenshot directory (`Path`)                                     |
| `ctx.fs.scripts_dir`              | Scripts directory (`Path`)                                        |
| `ctx.fs.proto_dir`                | Proto-test directory (`Path`)                                     |
| `ctx.fs.cap_dir`                  | Capture-output directory (`Path`)                                 |
| `ctx.fs.open_file(path)`          | Open in system viewer/editor (gated on `gui_apps` capability)     |
| `ctx.ui.confirm(message)`         | Yes/Cancel dialog (TUI-only; declare `confirm_dialog` capability) |
| `ctx.ui.notify(text)`             | TUI-strict notification (declare `ui_notify`)                     |
| `ctx.ui.clear_screen()`           | Clear the terminal output (declare `tui_mode`)                    |
| `ctx.ui.screenshot(path)`         | Save an SVG screenshot (declare `screen_capture`)                 |
| `ctx.dispatch(cmd)`               | Re-route a command through the full pipeline                      |
| `ctx.ns(name)`                    | Get/create a session-scoped state dict                            |
| `ctx.plugin_cfg(name)`            | Get a per-plugin persistent config dict                           |

**Capability gating.** Methods on `ctx.ui` and `ctx.fs.open_file` are gated on `CapabilitySet` flags. Calling a gated method without declaring the capability raises `MissingCapability`, which the dispatcher converts to `CmdResult.fail`. Declare what your command needs:

```python
COMMAND = Command(
    name="ask", help="Prompt user.",
    needs=CapabilitySet(confirm_dialog=True),  # refuse to dispatch in CLI
    handler=_handler,
)
```

There is also `ctx.internal`, a privileged escape hatch for built-ins (config save, capture, port control, engine forwarders, etc.). This is used by built-in commands and may change between versions, so external plugins should avoid it.

</details>

<details>
<summary>Example plugins</summary>

See `examples/plugins/` for working examples:

- **hello.py:** minimal greeting command
- **at_test.py:** send AT commands over serial
- **timestamp.py:** print the current date/time
- **ping.py:** send a command and measure response time

More complete examples ship with `--demo`: `probe.py` demonstrates the drain → write → read → parse cycle for device interaction; `traffic.py` (with `/traffic.count`, `/traffic.hexdump`, `/traffic.rate`, `/traffic.snoop`) demonstrates the passive RX/TX observer pattern via the `ctx.serial.rx_observer()` / `ctx.serial.tx_observer()` context managers. Run `/help probe` or `/help traffic` to see the documentation, or `/help.dev <name>` for the source docstrings.

</details>

### Binary format specs

Embedded protocols send raw bytes. Format specs decode them into human-readable fields - so you see "Temp: 200" and "CRC: OK" instead of `00 C8 ... XX XX`. Used in protocol testing (`.pro` files), data capture (`/cap.struct`, `/cap.hex`), and the proto debug screen.

A format spec is a one-line definition of your packet layout. Each field has a name, a type, and a byte range:

```text
"ID:H1 Temp:U2-3 Signed:I4-5 Status:H6"
```

Given the bytes `01 00 C8 FF FE 0A`, this decodes to:

```text
  ID     = 01      (byte 1 as hex)
  Temp   = 200     (bytes 2-3 as unsigned int, big-endian)
  Signed = -2      (bytes 4-5 as signed int, big-endian)
  Status = 0A      (byte 6 as hex)
```

In protocol tests, termapy decodes both expected and actual bytes using your spec, then shows per-column pass/fail:

```text
Expected: 01 00 C8 FF FE 0A  ->  ID:01  Temp:200   Signed:-2   Status:0A
Actual:   01 00 C9 FF FE 0A  ->  ID:01  Temp:201   Signed:-2   Status:0A
                                  match  MISMATCH   match       match
```

<details>
<summary>Supported types</summary>

| Code   | Meaning          | Example            | Output       |
| ------ | ---------------- | ------------------ | ------------ |
| `H`    | Hex bytes        | `H1`, `H3-4`       | `0A`, `01FF` |
| `U`    | Unsigned integer | `U1`, `U3-4`       | `10`, `256`  |
| `I`    | Signed integer   | `I1`, `I3-4`       | `-1`, `+127` |
| `S`    | ASCII string     | `S5-12`            | `Hello...`   |
| `F`    | IEEE 754 float   | `F1-4`             | `3.14`       |
| `B`    | Bit field        | `B1.3`, `B1-2.7-9` | `1`, `5`     |
| `_`    | Padding (hidden) | `_:_3-4`           | *(skipped)*  |
| `crc*` | CRC verify       | `CRC:crc16m_le`    | pass/fail    |

Integers support 1, 2, 3, 4, and 8 byte widths. Floats are 4-byte (F32) or 8-byte (F64).

</details>

<details>
<summary>Endianness</summary>

Byte order in the spec IS the endianness - no flags needed:

- `U2-3` = bytes 2 then 3 = big-endian: `00 C8` = 200
- `U3-2` = bytes 3 then 2 = little-endian: `C8 00` = 51200
- `I4-5` = big-endian signed: `FF FE` = -2
- `I5-4` = little-endian signed: `FE FF` = -257

You read the spec the same way you read the protocol datasheet. Modbus devices are big-endian (`U2-3`), x86-based devices are little-endian (`U3-2`).

</details>

<details>
<summary>Real-world examples</summary>

**Modbus RTU response** (read 2 holding registers):

```text
"Slave:H1 Func:H2 Len:U3 Reg0:U4-5 Reg1:U6-7 CRC:crc16-modbus_le"
```

Decodes: `01 03 04 00 C8 01 F4 XX XX` -> Slave:01 Func:03 Len:4 Reg0:200 Reg1:500 CRC:pass

**GPS binary packet** (mixed types):

```text
"Sync:H1-2 MsgID:U3 Lat:F4-7 Lon:F8-11 Alt:F12-15 Sats:U16 _:_17 CRC:crc8-maxim"
```

**Sensor with bit flags** (status byte with packed bits):

```text
"Temp:U1-2 Humid:U3 MotorOn:B4.0 AlarmHi:B4.1 AlarmLo:B4.2 Mode:B4.5-7"
```

Decodes byte 4 into individual bit fields: MotorOn=1, AlarmHi=0, AlarmLo=0, Mode=3

**Simple checksum** (not CRC - custom sum):

```text
"Header:H1 Payload:H2-9 Sum:H10"
```

For non-standard checksums, add a CRC plugin (3 lines of Python):

```python
NAME = "sum8"
WIDTH = 1

def compute(data: bytes) -> int:
    return sum(data) & 0xFF
```

Drop into `builtins/crc/` or `termapy_cfg/<name>/crc/`.

</details>

<details>
<summary>CRC support</summary>

60+ built-in algorithms covering CRC-8, CRC-16, CRC-32 families (Modbus, XMODEM, CCITT, USB, and more).

In format specs, CRC columns verify data integrity automatically:

- `CRC:crc16-modbus_le` - little-endian Modbus CRC-16
- `CRC:crc16-xmodem_be` - big-endian XMODEM CRC-16
- `CRC:crc8-maxim` - 1-byte CRC (no endianness needed)

From the REPL:

- `/proto.crc.list` - list every algorithm
- `/proto.crc.info crc16-modbus` - show parameters
- `/proto.crc.calc crc16-modbus 01 03 00 00 00 0A` - compute CRC
- `/proto.crc.find bin=01 03 00 00 00 0A C5 CD` - identify the algorithm from a captured packet

</details>

</details>

<details>
<summary><strong>Portability</strong></summary>

Developed and tested on **Windows**. Basic usage verified on **macOS** (serial, ANSI rendering, screenshots). macOS support is **alpha** until further testing. **Linux** is exercised by GitHub Actions CI on every push (the full test suite, including the CLI gold-standard integration test, runs on Ubuntu against Python 3.11 through 3.14), but interactive use on Linux has not been hand-verified by the author.

</details>

<details>
<summary><strong>Architecture</strong> - threading model</summary>

Textual runs on a single async event loop. Termapy uses `@work(thread=True)` for blocking operations, posting UI updates via `call_from_thread()`.

| Worker              | Lifetime    | Purpose                                                 |
| ------------------- | ----------- | ------------------------------------------------------- |
| `read_serial()`     | Long-lived  | Reads serial data in a loop, posts lines to the RichLog |
| `_auto_reconnect()` | Short-lived | Retries serial connection every 2.5s until success      |
| `_run_lines()`      | Short-lived | Sends multiple commands with inter-command delay        |
| `_run_script()`     | Short-lived | Executes a `.run` script file line by line              |
| `_send_test()`      | Short-lived | Runs a single protocol test case (send/receive/match)   |
| `_run_cmds()`       | Short-lived | Sends setup/teardown commands for protocol tests        |

Only `read_serial()` is long-lived. At most two workers run concurrently: the serial reader plus one command/script/test worker.

</details>

<details>
<summary><strong>Test coverage</strong> - 2850 tests, 71% core-module coverage</summary>

Run the full suite with `uv run pytest`. (The count above is the one place it
is tracked; `release_prep` refreshes it each release.)

Tests are scoped to **termapy's own concerns** — REPL dispatch, serial
engine, CLI flow, plugin loading, capture, protocol toolkit.  CRC
codegen correctness (generator output for every algorithm in every
language) lives in the [crcglot](https://github.com/hucker/crcglot)
test suite and runs on every crcglot release; termapy verifies only
"the dispatch reached crcglot and produced output," not every
generator permutation.

**Core logic** (serial engine, capture, REPL, protocol, config):

| Module               | Coverage | Test file                            |
| -------------------- | -------- | ------------------------------------ |
| `migration.py`       | 98%      | `test_migration.py`                  |
| `plugins/` (package) | 98%      | `test_plugins.py`, `test_handles.py` |
| `defaults.py`        | 97%      | `test_defaults.py`                   |
| `capture.py`         | 92%      | `test_capture.py`                    |
| `protocol.py`        | 90%      | `test_protocol.py`                   |
| `serial_engine.py`   | 90%      | `test_serial_engine.py`              |
| `repl.py`            | 89%      | `test_engine.py`, `test_repl_cfg.py` |
| `serial_port.py`     | 87%      | `test_serial_port.py`                |
| `scripting.py`       | 86%      | `test_scripting.py`                  |
| `config.py`          | 82%      | `test_app_config.py`                 |
| `demo.py`            | 80%      | `test_demo.py`                       |
| `port_control.py`    | 75%      | `test_port_control.py`               |
| `cli.py`             | 40%      | `test_cli.py`                        |

**Built-in plugins:** broad coverage via `test_builtins.py` plus per-plugin test files (`test_var.py`, `test_env_var.py`, `test_xmodem.py`, `test_ymodem.py`, `test_app_plugin.py`, `test_proto_send_crc.py`, etc.).

**UI code:** `app.py` (~3750 lines), `proto_debug.py` (~1200 lines), and `dialogs/` (~2450 lines) are Textual UI and tested manually (Textual Pilot + the CLI gold test), not unit-tested. The 70% core-module figure is measured with `app.py`, `dialogs/`, and `builtins/` **omitted** (see `[tool.coverage.run]` in `pyproject.toml`) — so it is *not* whole-repo coverage. Counting the whole repo (only vendored third-party code omitted), coverage is **~61%**; the ~8-point gap is exactly this untested UI layer. The omit is deliberate — the focus has been on extracting business logic into unit-testable modules and keeping the UI as thin delegation — but the headline number is core-module, not overall.

</details>

<details>
<summary><strong>Continuous integration</strong> - GitHub Actions</summary>

All tests run automatically on push to `main` and on pull requests via GitHub Actions.

| Job          | What it does                                                                                      |
| ------------ | ------------------------------------------------------------------------------------------------- |
| **test**     | Runs `pytest` across Python 3.11, 3.12, 3.13, and 3.14                                            |
| **coverage** | Runs `pytest --cov` on Python 3.14 and uploads to [Codecov](https://codecov.io/gh/hucker/termapy) |
| **audit**    | Runs `pip-audit` to check for known vulnerabilities in dependencies                               |

The CI badge at the top of this README reflects the current status of the test workflow. See [`.github/workflows/tests.yml`](.github/workflows/tests.yml) for the full configuration.

</details>

---

Built with heavy use of Claude. For how that worked out, see [On AI assistance](src/termapy/help/on-ai-assistance.md).
