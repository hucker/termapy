# REPL commands

Commands prefixed with `/` (configurable via `cmd_prefix`) run locally instead of being sent to the serial device.

> **NOTE:** If the device you are communicating with uses `/` commands that conflict with termapy's, you can change the prefix by setting `cmd_prefix` in your config (e.g. `cmd_prefix: "!"`). With `!` as the prefix, `!help` runs the local help command and `/foo` gets sent verbatim to the device.

| Command                   | Description                                                                 |
| ------------------------- | --------------------------------------------------------------------------- |
| `/help`                   | Clean landscape of every command (name + one-liner)                         |
| `/help <term>`            | Exact match -> man-page detail; otherwise a candidate list                  |
| `/search <term>`          | Deep search: name, help, args, flags, long help (multi-term, `-exclude`, regex) |
| `/help.target`            | Show only imported target device commands                                   |
| `/help.run`               | List available .run scripts with descriptions                               |
| `/help.plugin`            | List loaded plugins grouped by source                                       |
| `/help.dev <cmd>`         | Show a command handler's Python docstring (developer view)                  |
| `/port {name}`            | TUI bare: open Port picker. CLI bare: list subcommands. With name: open it. |
| `/port.help`              | Show `/port` help (alias for `/help port`)                                  |
| `/port.list`              | Chip-aware port table (MFG, CHIP, SPEED, VID:PID, SN, ...)                  |
| `/port.connect {name} {baud} {mode}` | Connect with optional baud and mode (e.g. /port.connect COM3 9600 N81) |
| `/port.mode {baud} {mode}` | Show or set serial mode (e.g. /port.mode 9600 N81)                        |
| `/port.disconnect`        | Disconnect from the serial port                                             |
| `/port.info`              | Show port status, serial parameters, hardware lines, and xfer root          |
| `/port.baud_rate {value}` | Show or set baud rate (hardware only)                                       |
| `/port.byte_size {value}` | Show or set data bits (hardware only)                                       |
| `/port.parity {value}`    | Show or set parity (hardware only)                                          |
| `/port.stop_bits {value}` | Show or set stop bits (hardware only)                                       |
| `/port.flow_control {m}`  | Show or set flow control: none, rtscts, xonxoff, manual                     |
| `/port.dtr {0\|1}`        | Show or set DTR line                                                        |
| `/port.rts {0\|1}`        | Show or set RTS line                                                        |
| `/port.cts`               | Show CTS state (read-only)                                                  |
| `/port.dsr`               | Show DSR state (read-only)                                                  |
| `/port.ri`                | Show RI state (read-only)                                                   |
| `/port.cd`                | Show CD state (read-only)                                                   |
| `/port.break {ms}`        | Send break signal (default 250ms)                                           |
| `/cfg [key [value]]`      | TUI: open the Cfg picker. Bare in CLI: dump JSON. With args: get/set.       |
| `/cfg.auto <key> <val>`   | Set a config key without confirmation                                       |
| `/cfg.configs`            | List all config files                                                       |
| `/cfg.load <name>`        | Switch to a different config by name                                        |
| `/cfg.show`               | Open the current config file in the system viewer                           |
| `/cfg.help`               | Show `/cfg` help (alias for `/help cfg`)                                    |
| `/ss.svg [name]`          | Save an SVG screenshot                                                      |
| `/ss.txt [name]`          | Save a text screenshot                                                      |
| `/ss.dir`                 | Show the screenshot folder                                                  |
| `/cls`                    | Clear the terminal                                                          |
| `/run {file} {-v}`        | TUI bare: open Run picker. CLI bare: list scripts. With file: run it.       |
| `/run.list`               | List .run files in the run/ directory                                       |
| `/run.load <file>`        | Run a script file (same as /run)                                            |
| `/run.help`               | Show `/run` help (alias for `/help run`)                                    |
| `/run.legacy {file\|*}`   | Find pre-0.63 command names in scripts; `--fix` rewrites in place           |
| `/delay <duration>`       | Pause for a duration (e.g. `500ms`, `1.5s`)                                 |
| `/confirm {message}`      | Show Yes/Cancel dialog; Cancel stops a running script                       |
| `/repeat ...`             | Repeat a command N times: `count=<N> {delay=<dur>} {var=<name>} cmd=<cmd>`  |
| `/stop`                   | Abort a running script                                                      |
| `/seq`                    | Show sequence counters                                                      |
| `/seq.reset`              | Reset all sequence counters to zero                                         |
| `/print <text>`           | Print a message to the terminal                                             |
| `/print.r <text>`         | Print Rich markup text (e.g. `[bold red]Warning![/]`)                       |
| `/credits`                | Print acknowledgments (libraries and authors termapy depends on)            |
| `/show <name>`            | Show a file                                                                 |
| `/show.cfg`               | Show the current config file                                                |
| `/term`                   | Terminal display / session toggles (echo, line_no, timestamps, ...)         |
| `/term.info`              | Snapshot the state of every `/term.*` toggle                                |
| `/term.log <text>`        | Append a line to the session log without echoing to screen                  |
| `/term.echo [on\|off]`    | Toggle command echo                                                         |
| `/term.line_no [on\|off]` | Toggle line numbers in serial output (TUI only)                             |
| `/term.line_endings [on\|off]` | Toggle visible `\r` `\n` markers in serial output                      |
| `/term.output {level}`    | Show or set output level (silent/quiet/normal/verbose)                      |
| `/term.usb_db`            | Report bundled USB vendor-database freshness (local read)                   |
| `/term.timestamps [on\|off]` | Toggle `[HH:MM:SS.mmm]` timestamp prefix                                 |
| `/term.hex [on\|off]`     | Toggle hex display of incoming bytes                                        |
| `/term.encoding {name}`   | Show or set byte-decoding encoding (utf-8, latin-1, ...)                    |
| `/term.send_bare_enter [on\|off]` | Send line ending on empty Enter                                     |
| `/os <cmd>`               | Run a shell command (requires `os_cmd_enabled`)                             |
| `/grep <pattern>`         | Search scrollback for regex matches (case-insensitive)                      |
| `/edit <file>`            | Edit a project file (`run/`/`proto/` path)                                  |
| `/edit.run {file}`        | Edit a .run script, or list available scripts if no name given              |
| `/edit.proto {file}`      | Edit a .pro file, or list available files if no name given                  |
| `/edit.plugin {file}`     | Edit a plugin, or list available plugins if no name given                   |
| `/edit.cfg`               | Edit the current config file                                                |
| `/log.show`               | Open the session log in the system viewer                                   |
| `/log.dump {N}`           | Print the session log (all, or last N lines) to the terminal                |
| `/log.fingerprint`        | Write a full session fingerprint (OS, terminal, port params) to the log     |
| `/log.delete`             | Delete the session log file                                                 |
| `/edit.info`              | Open the info report in the system viewer                                   |
| `/cfg.info {--display}`   | Show project summary; `--display` opens full report                         |
| `/cfg.files`              | Show project directory tree                                                 |
| `/proto`                  | TUI: open the Proto picker. CLI: show /proto long-help.                     |
| `/proto.help`             | Show `/proto` help (alias for `/help proto`)                                |
| `/proto.send <hex>`       | Send raw hex bytes and display response                                     |
| `/proto.run <file>`       | Run a binary protocol test script (.pro)                                    |
| `/proto.list`             | List .pro files in the proto/ directory                                     |
| `/proto.load <file>`      | Run a protocol test script (same as /proto.run)                             |
| `/proto.debug <file>`     | Open interactive protocol debug screen for a .pro script                    |
| `/proto.hex [on\|off]`    | Toggle hex display mode for serial I/O                                      |
| `/proto.crc.list {pat}`   | List CRC algorithms (optional glob filter)                                  |
| `/proto.crc.info <name>`  | Show CRC algorithm parameters and description                               |
| `/proto.crc.calc <n> {d}` | Compute CRC over hex bytes, text, or file                                   |
| `/proto.crc.find <pkt>`   | Identify the CRC algorithm from a captured packet (bin= or asc=)            |
| `/proto.status`           | Show current protocol mode state                                            |
| `/var {name}`             | List user variables, or show one by name                                    |
| `/var.list`               | List user variables (explicit alias for bare `/var`)                        |
| `/var.set <NAME> <value>` | Set a user variable                                                         |
| `/var.clear`              | Clear all user variables                                                    |
| `/env.list {pattern}`     | List environment variables (all, by name, or glob)                          |
| `/env.set <name> <value>` | Set a session-scoped environment variable                                   |
| `/env.reload`             | Re-snapshot variables from the OS environment                               |
| `/cap.text <f> ...`       | Capture serial text to file for a timed duration                            |
| `/cap.bin <f> ...`        | Capture raw binary bytes to a file                                          |
| `/cap.struct <f> ...`     | Capture binary data, decode with format spec to CSV                         |
| `/cap.hex <f> ...`        | Capture hex text lines, decode with format spec to CSV                      |
| `/cap.stop`               | Stop an active capture                                                      |
| `/xmodem.send <file>`     | Send a file to the device via XMODEM                                        |
| `/xmodem.recv <file>`     | Receive a file from the device via XMODEM                                   |
| `/ymodem.send <file> ...` | Send file(s) to the device via YMODEM (batch)                               |
| `/ymodem.recv {dir}`      | Receive file(s) from the device via YMODEM                                  |
| `/xfer.root {path}`       | Show or set the file transfer root directory                                |
| `/run.edit <file>`        | Open a .run script in the system editor                                     |
| `/color {on\|off}`        | Show or toggle color output (CLI mode only)                                 |
| `/raw <text>`             | Send text to serial with no variable expansion or transforms                |
| `/exit`                   | Exit termapy                                                                |

## Script profiling

| Command                     | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| `/run.profile <script>`     | Run a script with per-line timing (saves CSV to `prof/`)     |
| `/run.profile.cmd <command>`| Profile a single command                                     |
| `/run.profile.show`         | Open newest profile in system viewer                         |
| `/run.profile.dump`         | Print newest profile to the terminal                         |
| `/run.profile.explore`      | Open `prof/` folder in file explorer                         |
| `/run.profile.list`         | List profile files                                           |

## Config file management

Each config subfolder has a consistent set of subcommands:

| Subcommand     | Action                                   | Folders                                     |
| -------------- | ---------------------------------------- | ------------------------------------------- |
| `cfg.<folder>` | List files                               | scripts, proto, plugins, ss, viz, cap, prof |
| `.explore`     | Open folder in file explorer             | all                                         |
| `.show`        | Open newest file in system viewer        | all                                         |
| `.dump {name}` | Print newest (or named) file to terminal | scripts, proto, plugins, viz, cap, prof     |
| `.clear`       | Delete all files                         | ss, cap, prof (generated output only)       |

Examples:

- `/cfg.scripts`: list script files
- `/cfg.scripts.dump`: print newest script to terminal
- `/cfg.proto.show`: open newest .pro file in editor
- `/cfg.cap.clear`: delete all capture files
- `/cfg.prof.dump`: print newest profile CSV to terminal
