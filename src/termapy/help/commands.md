# REPL Commands

Commands prefixed with `/` (configurable via `cmd_prefix`) run locally instead of being sent to the serial device.

| Command                   | Description                                                                 |
| ------------------------- | --------------------------------------------------------------------------- |
| `/help [cmd]`             | List commands or show extended help for one                                 |
| `/help.dev <cmd>`         | Show a command handler's Python docstring                                   |
| `/port [name]`            | Open a port by name, or show subcommands                                    |
| `/port.list`              | List available serial ports                                                 |
| `/port.open {name}`       | Connect (optional port override)                                            |
| `/port.close`             | Disconnect from the serial port                                             |
| `/port.info`              | Show port status, serial parameters, and hardware lines                     |
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
| `/cfg [key [value]]`      | View or change config values                                                |
| `/cfg.auto <key> <val>`   | Set a config key without confirmation                                       |
| `/cfg.configs`            | List all config files                                                       |
| `/cfg.load <name>`        | Switch to a different config by name                                        |
| `/ss.svg [name]`          | Save an SVG screenshot                                                      |
| `/ss.txt [name]`          | Save a text screenshot                                                      |
| `/ss.dir`                 | Show the screenshot folder                                                  |
| `/cls`                    | Clear the terminal                                                          |
| `/run <file> {-v}`        | Run a script file (-v/--verbose for per-line timing)                        |
| `/run.list`               | List .run files in the run/ directory                                       |
| `/run.load <file>`        | Run a script file (same as /run)                                            |
| `/delay <duration>`       | Pause for a duration (e.g. `500ms`, `1.5s`)                                 |
| `/confirm {message}`      | Show Yes/Cancel dialog; Cancel stops a running script                       |
| `/stop`                   | Abort a running script                                                      |
| `/seq`                    | Show sequence counters                                                      |
| `/seq.reset`              | Reset all sequence counters to zero                                         |
| `/print <text>`           | Print a message to the terminal                                             |
| `/print.r <text>`         | Print Rich markup text (e.g. `[bold red]Warning![/]`)                       |
| `/show <name>`            | Show a file                                                                 |
| `/show.cfg`               | Show the current config file                                                |
| `/echo [on\|off]`         | Toggle command echo                                                         |
| `/echo.quiet <on\|off>`   | Set echo on/off silently (for scripts and on_connect_cmd)                   |
| `/os <cmd>`               | Run a shell command (requires `os_cmd_enabled`)                             |
| `/grep <pattern>`         | Search scrollback for regex matches (case-insensitive)                      |
| `/show_line_endings {on\|off}` | Toggle visible `\r` `\n` markers in serial output                      |
| `/edit <file>`            | Edit a project file (`run/`/`proto/` path)                                  |
| `/edit.run {file}`        | Edit a .run script, or list available scripts if no name given              |
| `/edit.proto {file}`      | Edit a .pro file, or list available files if no name given                  |
| `/edit.plugin {file}`     | Edit a plugin, or list available plugins if no name given                   |
| `/edit.cfg`               | Edit the current config file                                                |
| `/edit.log`               | Open the session log in the system viewer                                   |
| `/log.clear`              | Delete the session log file                                                 |
| `/edit.info`              | Open the info report in the system viewer                                   |
| `/cfg.info {--display}`   | Show project summary; `--display` opens full report                         |
| `/cfg.files`              | Show project directory tree                                                 |
| `/proto.send <hex>`       | Send raw hex bytes and display response                                     |
| `/proto.run <file>`       | Run a binary protocol test script (.pro)                                    |
| `/proto.list`             | List .pro files in the proto/ directory                                     |
| `/proto.load <file>`      | Run a protocol test script (same as /proto.run)                             |
| `/proto.debug <file>`     | Open interactive protocol debug screen for a .pro script                    |
| `/proto.hex [on\|off]`    | Toggle hex display mode for serial I/O                                      |
| `/proto.crc.list {pat}`   | List CRC algorithms (optional glob filter)                                  |
| `/proto.crc.help <name>`  | Show CRC algorithm parameters and description                               |
| `/proto.crc.calc <n> {d}` | Compute CRC over hex bytes, text, or file                                   |
| `/proto.status`           | Show current protocol mode state                                            |
| `/var {name}`             | List user variables, or show one by name                                    |
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
| `/run.edit <file>`        | Open a .run script in the system editor                                     |
| `/color {on\|off}`        | Show or toggle color output (CLI mode only)                                 |
| `/raw <text>`             | Send text to serial with no variable expansion or transforms                |
| `/exit`                   | Exit termapy                                                                |

## Script Profiling

| Command                     | Description                                                  |
| --------------------------- | ------------------------------------------------------------ |
| `/run.profile <script>`     | Run a script with per-line timing (saves CSV to `prof/`)     |
| `/run.profile.cmd <command>`| Profile a single command                                     |
| `/run.profile.show`         | Open newest profile in system viewer                         |
| `/run.profile.dump`         | Print newest profile to the terminal                         |
| `/run.profile.explore`      | Open `prof/` folder in file explorer                         |
| `/run.profile.list`         | List profile files                                           |

## Config File Management

Each config subfolder has a consistent set of subcommands:

| Subcommand     | Action                                   | Folders                                     |
| -------------- | ---------------------------------------- | ------------------------------------------- |
| `cfg.<folder>` | List files                               | scripts, proto, plugins, ss, viz, cap, prof |
| `.explore`     | Open folder in file explorer             | all                                         |
| `.show`        | Open newest file in system viewer        | all                                         |
| `.dump {name}` | Print newest (or named) file to terminal | scripts, proto, plugins, viz, cap, prof     |
| `.clear`       | Delete all files                         | ss, cap, prof (generated output only)       |

Examples:

- `/cfg.scripts` — list script files
- `/cfg.scripts.dump` — print newest script to terminal
- `/cfg.proto.show` — open newest .pro file in editor
- `/cfg.cap.clear` — delete all capture files
- `/cfg.prof.dump` — print newest profile CSV to terminal
