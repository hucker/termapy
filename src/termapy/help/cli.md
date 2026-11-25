# Command-line flags

Termapy is a TUI first, but a few things are easier from the shell: **listing
what's plugged in, watching for changes, and inspecting a chip without
launching the UI**. Every flag in the inventory sections below prints
plain text to stdout and exits; none of them load the TUI.  (The
session flags at the end -- `--cli`, `--run`, `-e` -- start a session
instead.)

Because these flags skip the Textual import, they start fast — no
config loading, no widget tree, no config directory scan.  Handy when
you just want to answer "what's plugged in?" from the shell.

```sh
termapy --ports                 # one-line-per-port table
termapy --watch                 # live log of plug/unplug/open/close
termapy --info                  # full per-port chip dump
termapy --info=COM4             # just COM4
termapy --usb                   # the whole USB tree, serial ports marked
```

## `--ports`: what's plugged in right now

A single-line-per-port table, same schema as the in-app port picker.

```text
$ termapy --ports
PORT  MFG   VENDOR     DESCRIPTION        PRODUCT            CHIP                             SPEED       VID:PID    SERIAL #            DRIVER   LOCATION  IF
----  ----  ---------  -----------------  -----------------  -------------------------------  ----------  ---------  ------------------  -------  --------  --
COM3  MSFT  Microchip  USB Serial Device  MPLAB PICkit 5     Microchip MPLAB PICkit 5 (VCOM)  High-Speed  04D8:9036  020026702RYN040952  usbser   1-8.4     1
COM4  FTDI  FTDI       USB Serial Port    FT230X Basic UART  FTDI FT230X / FT231X / FT234XD   Full-Speed  0403:6015  D20JSV68A           FTSER2K  1-8.2
COM7  FTDI  FTDI       USB Serial Port    FT232R USB UART    FTDI FT232R / FT245R             Full-Speed  0403:6001  BG03U7VTA           FTSER2K  1-8.3
```

On Linux and macOS the PORT column shows the short name (`ttyUSB0`,
`usbserial-A50285BI`; see [Port names](ports.md#how-termapy-writes-a-ports-name)),
and `--json` keeps the path.

Three columns name the device, each from a different source:
DESCRIPTION is the name the driver gives the port (often just "USB Serial
Device"), PRODUCT is what the device calls itself (its USB product
string), and CHIP is termapy's own table of known chips.

Columns you don't need are dropped automatically: on a machine where no
port has a serial number, the `SERIAL #` column disappears.  On narrow
terminals, columns drop in this order until the row fits: speed, chip,
VID:PID, driver, vendor, PRODUCT, SERIAL #, IF, location.  PORT, MFG and
DESCRIPTION always stay.

Give a pattern to show only the ports with any field that contains it:
the maker, a serial number, the chip, VID:PID or the port name.  Case is
ignored, and a wildcard (`*`, `?`, `[`) makes a glob that must match a
whole field:

```sh
termapy --ports FTDI            # every FTDI adapter
termapy --ports Microchip
termapy --ports BG03U7VTA       # which COM is this adapter?
termapy --ports "COM[1]"        # COM1 alone; plain COM1 also finds COM10-COM19
```

Exits 0 on match, 1 on no match or no ports (script-friendly).

`--ports` lists physical ports only — the ones the OS reports via
pyserial's `comports()`.  Termapy also accepts pyserial URL handlers
(`loop://`, `socket://host:port`, `rfc2217://host:port`, `hwgrep://...`)
as the `port` config value, but those are connection methods rather
than inventory, so they don't appear here.  See
[Serial ports](ports.md) for the full list.

## `--watch`: live log of port events

Polls every 0.5 s and prints a log line for every change — plug,
unplug, open, close, chip re-EEPROM. Baseline rows show what's
present at startup; `Ctrl+C` to exit.

```text
$ termapy --watch
[15:57:57] monitoring 3 port(s); Ctrl+C to exit
[15:57:57]    COM3    closed  MSFT       USB Serial Device   Microchip MPLAB PICkit 5 (VCOM)   High-Speed  04D8:9036  020026702RYN040952
[15:57:57]    COM4    closed  FTDI       USB Serial Port     FTDI FT230X / FT231X / FT234XD    Full-Speed  0403:6015  D20JSV68A
[15:57:57]    COM7    closed  FTDI       USB Serial Port     FTDI FT232R / FT245R              Full-Speed  0403:6001  BG03U7VTA
[15:58:01]    COM7    open    FTDI       USB Serial Port     FTDI FT232R / FT245R              Full-Speed  0403:6001  BG03U7VTA
[15:58:02] -  COM7
[15:58:06] +  COM7
[15:58:06]    COM7    closed  FTDI       USB Serial Port     FTDI FT232R / FT245R              Full-Speed  0403:6001  BG03U7VTA
[15:58:13]    COM4    open    FTDI       USB Serial Port     FTDI FT230X / FT231X / FT234XD    Full-Speed  0403:6015  D20JSV68A
```

In that capture:

- Baseline at `15:57:57` shows the three ports that were plugged in when
  `--watch` started, all closed.
- `15:58:01` — something opened COM7 (blank marker, state column
  flipped to `open`).
- `15:58:02` / `15:58:06` — COM7 was unplugged (`-`), then plugged back
  in (`+`); the line after the `+` shows the post-plug state.
- `15:58:13` — a process opened COM4 (another blank-marker state
  transition).

### Reading the event column

The first column after the timestamp is a single-character event marker:

| Marker  | Meaning                                                          |
| ------- | ---------------------------------------------------------------- |
| *blank* | Baseline snapshot, or open/close transition (see state column)   |
| `+`     | Port appeared (new plug-in). Followed by a full state line.      |
| `-`     | Port disappeared (unplug). Only port name is known.              |
| `~`     | Serial number or VID:PID changed on an existing port (re-EEPROM) |

The `state` column carries `open` / `closed` on its own — no extra verb
needed. When a port toggles from closed to open (another process grabbed
it), the marker stays blank and the state column flips.  Plug events
"punch out" of the stream visually because the `+` / `-` line is
sparse.

### Why you'd use it

`--watch` is the fastest way to answer "which port is this cable?"
without guessing.  Start `termapy --watch`, then plug the cable in:
the `+` line tells you exactly which COM number showed up.  Unplug it
and the `-` line confirms.  No ambiguity, no poking at Device Manager.

It's also handy for diagnosing flaky connectors — if a cable
repeatedly appears and disappears on its own, you'll see a stream of
`+` / `-` events without touching anything.

Output width is ~140 columns and doesn't reflow to fit your terminal.
For long sessions, pipe to a file and `less -S` later:

```sh
termapy --watch > watch.log
less -S watch.log
```

## `--info`: chip details for one or every port

Full multi-line per-port dump — same data the in-app `/port.chip`
command produces, but no TUI.

```sh
termapy --info          # every port
termapy --info=COM4     # just one
```

Example, one known and one unknown chip:

```text
$ termapy --info
  COM3
  Description : USB Serial Device (COM3)
  Manufacturer: Microsoft
  Serial #    : 020026702RYN040952
  Location    : 1-8.3:x.1
  VID:PID     : 04D8:000A
  Model       : unknown
  USB Speed   : unknown (chip not in lookup table)
  Permissions : n/a
  In Use      : no
  (chip 04D8:000A not in termapy's lookup table -- please report at https://github.com/hucker/termapy/issues so we can add it)

  COM4
  Description  : USB Serial Port (COM4)
  Manufacturer : FTDI
  Serial #     : D20JSV68A
  VID:PID      : 0403:6015
  Model        : FTDI FT230X / FT231X / FT234XD
  USB Speed    : USB Full-Speed (1 ms min latency)
  Latency Timer: n/a (Windows - check Device Manager)
  Max Baud     : 3,000,000 baud
  Permissions  : n/a
  In Use       : no
```

The COM3 block shows what an unrecognized chip looks like: the VID:PID
is always printed so you can identify it manually, and termapy nudges
you to open an issue so the lookup table can grow.

Exits 0 if at least one port was printed, 1 if the named port wasn't
found or no ports are connected.

## JSON output

`--json` emits a JSON array instead of the column table, for `--ports`
and `--usb`.  Filter it with the tool you script in:

```sh
termapy --ports --json | jq -r '.[] | select(.vid_pid == "0403:6001") | .device'
```

Each `--ports --json` record has a stable schema:

```json
{
  "device": "COM4",
  "manufacturer": "FTDI",
  "manufacturer_raw": "Future Technology Devices, Inc.",
  "vendor": "FTDI",
  "description": "USB Serial Converter",
  "product": "FT232R USB UART",
  "chip": "FTDI FT232R",
  "speed": "Full-Speed",
  "vid": 1027,
  "pid": 24577,
  "vid_pid": "0403:6001",
  "serial_number": "AL01ABCD",
  "in_use": false,
  "session": null,
  "driver": "ftdi_sio",
  "location": "Hub_#0009.Port_#0004",
  "interface_number": null
}
```

Three vendor-related fields, intentionally separate:

- `manufacturer_raw` — the literal string the device descriptor or driver
  INF reports.
- `manufacturer` — the same string folded to a column-friendly short
  form (e.g. `"Future Technology Devices..."` → `"FTDI"`,
  `"Silicon Labs"` → `"SiLabs"`).
- `vendor` — the silicon vendor resolved from the VID per USB-IF
  assignment.  Independent of what the descriptor / INF reports.

These often agree.  They can disagree when a device uses a generic
driver — a Microchip USB-serial chip running on Microsoft's
`usbser.sys` reports `manufacturer_raw = "Microsoft"` (driver INF) but
`vendor = "Microchip"` (VID `0x04D8`).  Engineers diagnosing devices
benefit from seeing all three.

Numeric fields stay numeric; missing values are `null` (not omitted).
`vid_pid` is the canonical uppercase-hex form (`10C4:EA60`) for literal
matching.  `driver` is the kernel module name on Linux (`ftdi_sio`,
`cdc_acm`, `ch341`, `cp210x`, ...) and the Windows service name
(`FTDIBUS`, `usbser`, `silabser`, ...).  On macOS `driver` is `null` for
now.

`session` is the termapy session holding the port, from the breadcrumb
every running termapy leaves: `"termapy mcp, PID 17140"`, or `null` when
no termapy holds it.  Only termapy sessions are seen there, on every OS,
so `null` does not mean free: PuTTY or another program may have it.
`in_use` is `true` or `false` only when something checked (the in-use
probe `--ports --json` runs, or a session's breadcrumb), and `null` when
nothing did.  At a terminal `--ports` colors a port another termapy
session holds yellow.

### Hardware-free CI: `--ports DEMO`

`DEMO` and `DEMO_FAIL` are virtual port names that the OS doesn't
enumerate but termapy can connect to.  Naming one explicitly returns
a synthesized record so CI pipelines can exercise the CLI without
plugging in real hardware:

```sh
termapy --ports DEMO --json
termapy --info DEMO
```

Bare `--ports` (no filter) does NOT include `DEMO` in the listing --
it only appears when you specifically ask for it, the same way
pyserial's `loop://` URL handler is reachable but not enumerated.

## `--serve`: share a port on the network

**Status: prototype.** It may change or be removed in any release.

`termapy --serve` puts serial ports on the network for another computer to
open, over RFC 2217, the serial-over-TCP protocol pyserial speaks. It runs
no TUI and none of the command system, so it starts in a fraction of a
second. Name each port to share; each gets its own TCP port:

```text
termapy --serve COM3                              this machine only: rfc2217://localhost:2217
termapy --serve COM3 COM5 --listen 192.168.1.57   two ports, open to the network
termapy --serve BG03U7VTA=2300                    by USB serial number, on TCP port 2300
termapy --serve DEMO                              the simulator, no hardware
```

```text
termapy --serve (prototype): serial ports on the network over RFC 2217
  COM3  ->  rfc2217://192.168.1.57:2217
  COM5  ->  rfc2217://192.168.1.57:2218
  Open to the network at 192.168.1.57. There is no login:
  anyone who can reach this address can drive these ports.
  Stop with Ctrl+C, or from any termapy: /app.kill serve (termapy --kill serve).
```

On the other computer the URL is the port, in a config or at the REPL:
`/port rfc2217://192.168.1.57:2217`. RFC 2217 carries the settings across,
so the baud rate, frame, DTR, RTS and break you set there reach the real
port.

- **The ports you name, and no others.** Unnamed ports stay free on this
  machine. A port's TCP number is fixed by its place in the list or by
  `=TCPPORT`, so a client's URL never changes.
- **Opened as a session opens one.** `TERMAPY_ALLOWED_PORTS` applies, a
  port open elsewhere is refused, and a port that will not open stops the
  run before anything listens.
- **One client per port at a time.** Another is refused at once, and the
  server prints who holds the port. A client that vanishes without closing,
  a laptop gone to sleep or off the network, is dropped within about a
  minute, so it never holds the port for good.
- **Unplugged or reset, it comes back.** The client is dropped and the
  port is tried again about once a second while its URL keeps listening.
  Name the port by its USB serial number, so a board that comes back as a
  new COM number is still found.
- **No login.** `--listen` with this machine's address opens the ports to
  anyone who can reach it. Without `--listen`, only this machine can
  connect, which still works through an SSH tunnel.
- **A session kind of its own.** `--alive` lists it as `serve` with every
  port it holds, where each answers and who is connected to it, a TUI that
  tries one of them is told the server holds it, and `/app.kill serve` or
  `termapy --kill serve` stops every port server.

To run it in the background, add `--detach`. termapy starts the server as
a process of its own, with no console, waits until it listens, and
returns:

```text
$ termapy --serve COM7 --listen 192.168.1.57 --detach
termapy --serve is running in the background, PID 11120:
  COM7  ->  rfc2217://192.168.1.57:2217
  Open to the network at 192.168.1.57. There is no login.
  Its output: C:\Users\you\AppData\Local\termapy\log\20261124_164358-serve.log
  Stop it with: termapy --kill serve
```

Closing the window it was started from does not stop it. Its banner and
every event go to the output file, which the diagnostic log's own pruning
removes in time. A server that cannot start, a port that will not open or
a TCP port in use, prints its reason there and here, and `--detach` exits
with its code. To start one at every logon, register the same command as a
Task Scheduler task on Windows or a systemd unit on Linux or a Raspberry Pi.

## `--alive` and `--kill`: see and stop sessions

Every running termapy -- a TUI, a CLI, an MCP server an editor started, a
`--web` server, a `--serve` port server -- leaves a breadcrumb in the app state folder
(`%LOCALAPPDATA%\termapy\running\` on Windows) naming its config and the
port it has open.  `--alive` lists them:

```text
$ termapy --alive
running termapys - 2 sessions
PID    MODE  CONFIG  PORT  SETTINGS    VERSION  STARTED              STARTED BY
-----  ----  ------  ----  ----------  -------  -------------------  --------------------
21552  mcp   m3_bin  COM4  115200 8N1  0.80.0   2026-10-27 16:38:45  pwsh.exe 20112
28172  tui   demo    DEMO  115200 8N1  0.80.0   2026-10-31 08:56:24  powershell.exe 17604
```

SETTINGS is the open port's baud and frame.  ADDRESS appears while a
server runs: where a `--serve` port server or a `--web` server answers,
and for `--serve` the client on each port, so with several things running
you can see which port is shared where and who holds it:

```text
PID    MODE   CONFIG  PORT        ADDRESS                                              STARTED
-----  -----  ------  ----------  ---------------------------------------------------  -------------------
64448  serve  -       COM7, COM4  192.168.1.57:2217 (192.168.1.20), 192.168.1.57:2218  2026-11-24 16:09:03
77808  web    -       -           localhost:8000                                       2026-11-24 12:27:11
```

STARTED BY is the program that started the session, past python, termapy
and uv.  A one-shot (`-e`,
`--run`) records no version or starter, which take a tenth of a second to
read.  A narrow terminal drops STARTED BY first.

`--kill` stops them, after a yes; bare, it lists them as `--alive` does.
Each session is asked to leave and leaves as `/exit` does: its terminal
restored, its port closed, its history saved, its session log ended.
A TUI holding unsaved settings asks about them first, as its Exit does,
so it may still be running when the timeout is up; answer it there.
termapy never ends a session itself.  One still running when `timeout=`
is up (3 s unless you give one), hung, partway through a firmware write,
or started by an older termapy, keeps running, and the reply names the
command that ends it (`taskkill -PID 22056 -F` on Windows, which works in
cmd, PowerShell and Git Bash alike, `kill -9 22056` elsewhere):

```sh
termapy --kill all      # every termapy, say before an update
termapy --kill serve    # every session of that kind: tui, cli, mcp, web, vt100, serve
termapy --kill 21552    # the session with that PID, as --alive lists it
termapy --kill COM4     # whichever session holds COM4
termapy --kill m3_bin   # the sessions on that config (m3* picks several)
termapy --kill all timeout=10s   # give them longer to leave
termapy --kill all -y            # no question: yes (-y / --yes)
```

Inside termapy the same two are `/app.alive` and `/app.kill <target>`:
`/app.kill others` stops every session but the one you type it in,
`/app.kill all` that one too, and `/app.kill me` that one alone, which is
`/exit`.  When a target picks the session you type it in, that one goes
last, through `/exit`, so it saves its history.
`/app.alive.json` adds what does not fit a row: the config, the Python
executable, the folder the session started in and its session log, each
an absolute path, and the open port's serial number.

With no terminal to answer on (a pipe), the answer is no.  A session you
end with `taskkill` or `kill` writes no `Session End` to its log, and a TUI
ended that way leaves its window as the TUI had it: close the window.  A
session started before breadcrumbs existed is not listed: end that one
from Task Manager or `kill`, once.

## Every flag, by job

The same five groups `termapy --help` prints, in the same order.  The
flags with a section above link to it.  The common flags have a short
form, shown first; `-h` prints the same help.

### Start a session

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `CONFIG` | Open that config (see [Getting started](getting-started.md)). |
| `PORT [SETTING ...]` | Not a flag: a port where the config goes opens the port with its own config, kept beside your other configs (see [Quick open](getting-started.md#quick-open-just-a-port)). |
| `-d`, `--demo`    | Set up (if needed) and start the demo device config.          |
| `--new NAME PORT [SETTING ...]` | Create config NAME for PORT and start it connected (see [Quick setup](getting-started.md#quick-setup)). |
| `--board BOARD`   | Start a dev board's starter config, creating it on first use from the attached board (see [Dev boards](boards.md)). |
| `--cfg-dir PATH`  | Override the default config directory for this run.           |
| `-c`, `--cli`     | Plain-text mode instead of the TUI, an interactive REPL; `--run` and `-e` imply it.  With no config, shows a welcome banner listing available ports -- use `/port.connect <name>` to pick one. |
| `--vt100`         | Start in VT100 passthrough mode (see [VT100 mode](vt100.md)). |
| `--web [[HOST:]PORT]` | Serve the TUI in a browser: `http://localhost:8000`, this machine only, unless `9000`, `192.168.1.50` or `192.168.1.50:9000` says otherwise. A network address opens it to other computers, with no login, so files stay inside the config folder as under MCP ([Browser](environment.md#browser-termapy---web)). A browser tab is a WEB session: the TUI without the console. |
| `--serve PORT[=TCPPORT] ...` | Share serial ports on the network over RFC 2217, with no TUI (see [`--serve`](#--serve-share-a-port-on-the-network)). Each port gets its own TCP port, 2217 and up. |
| `--listen HOST` | The address `--serve` listens on; this machine only without it. A network address has no login. |
| `--detach` | With `--serve`, run the server in the background with no console; it prints its PID, addresses and output file, and returns. |
| `--mcp`           | Run as a stdio MCP server for an AI agent (see [MCP server](mcp-server.md)); `--mcp-verbose` tees its log to stderr. |

### Run one thing and exit

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `-e`, `--exec CMD...` | Run one command and exit (CLI mode).  The command is the rest of the line, quotes optional, so `-e` goes last.  Implies `--cli` and `--no-color`.  See [One-shot exec](#one-shot-exec--e----exec) below. |
| `-r`, `--run SCRIPT` | Run a `.run` script headlessly, then exit.                    |
| `--proto NAME`    | Run a `.pro` protocol test headlessly and write JSON results (see [Protocol testing](protocol-testing.md)). |
| `-y`, `--yes`     | Answer yes to the command line's questions: `--kill`, `--new`'s overwrite, a CLI session's. Not the TUI's dialogs; never under `--mcp`. |

### Settings

Shape a CLI run's output; each lasts this run.

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `--no-color`      | Plain output without ANSI color (implied by `-e`).            |
| `--silent`        | Output level: errors only (a script reads `CmdResult.value`). |
| `-q`, `--quiet`   | Output level: command results only.                           |
| `-v`, `--verbose` | Output level: results + data + progress chatter.              |
| `--term-width N`  | Override the terminal width for CLI output (default: detected). |

### Ask about the machine (no session, no port opened)

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `-V`, `--version` | Print the installed version and exit. Same as `-e /app.ver`.  |
| `--check`         | Validate your config, print JSON status, exit.                |
| `-p`, `--ports [PATTERN]` | List the serial ports (see [`--ports`](#--ports-whats-plugged-in-right-now) above). Same as `-e /port.list`. |
| `-w`, `--watch`   | Print port events as they happen (see [`--watch`](#--watch-live-log-of-port-events) above). |
| `-i`, `--info [PORT]` | Chip details for one or every port (see [`--info`](#--info-chip-details-for-one-or-every-port) above). Same as `-e /port.chip`. |
| `-u`, `--usb`     | Whole USB tree with serial ports marked, in color at a terminal (Windows and Linux); pairs with `--json`. Same as `-e /port.usb`. |
| `-j`, `--json`    | JSON instead of a table for `--ports` and `--usb`; with `-e`, the command's own `--json` (see [JSON output](#json-output) above). The chip lookup table is `-e /port.chip.list --json`. |
| `--validate-profile PATH` | Validate a device profile (.json or .toml) against the schema; exit 0 if valid, 1 with errors. `--strict` treats compatibility warnings as errors, for CI. Same as `-e /profile.validate`. |

### Running termapys

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `--alive`         | List the termapys running on this machine (see [`--alive` and `--kill`](#--alive-and---kill-see-and-stop-sessions) above). Same as `-e /app.alive`. |
| `--kill TARGET`   | Ask the termapys a TARGET picks to exit; bare, list them (same section). Same as `-e /app.kill`. |

Any REPL command also accepts `--json` to render its result as a JSON
envelope -- `termapy -e /port.list --json` pipes cleanly to `jq`.
The flag means JSON wherever it stands: `termapy --json -e /port.list`
is the same command, the flag handed to it.
See [Scripting](scripting.md#json-output-termrequest-on-or---json-per-call).

`termapy --help` has the full list.

## One-shot exec: `-e` / `--exec`

Run a single command, print its output to stdout, exit with status 0
(success) or 1 (failure):

```sh
termapy my_device -e AT+VER             # send a device command
termapy my_device -e /help port         # run a slash command
termapy my_device --exec /proto.crc.calc crc16-modbus 01 03 00 00 00 02
```

One command per invocation: a second `-e`, or a line break inside one,
is refused.  Several commands go in a `.run` file, run with `--run`.

A one-shot opens the config's port only when its command uses it: a
device line, or a command whose declaration says so (`/term.send`,
`/term.raw`, `/expect`).  So `termapy my_device -e /port.list` lists the
ports with the board unplugged, and `termapy my_device -e AT+VER` fails
with the port error.  A `--run` script always connects first.

### Worked example

The general shape is `termapy <cfg> -e <command>`.  For a
device whose cfg is `my_device`, asking for the firmware version
might look like:

```text
$ termapy my_device -e "AT+VER"
Firmware v1.4.2
$ echo $?
0
```

One line from the device, exit 0, no banner, no echo prefix, no
ANSI -- ready to feed into `awk`, `grep`, or `jq`.

If you don't have a cfg set up yet, swap the cfg name for `--demo`
to use the bundled simulator (no hardware needed); the rest of the
shape is identical:

```text
$ termapy --demo -e "AT+INFO"
Bassomatic v77 v1.0
Uptime: 0h 0m 0s
Free memory: 29135 bytes
```

### Exit code contract

Exit `0` if termapy successfully dispatched the command, `1` if the
dispatch itself failed.  **Termapy's dispatch is what's checked, not
the device's response.**  An unknown slash command exits 1 because
termapy can detect it locally:

```text
$ termapy --demo -e "/notacommand"
  Error: Unknown command: notacommand
$ echo $?
1
```

But a device-side error text (e.g. the simulator's
`"ERROR: Unknown command 'AT+NOPE'"`) still exits 0 -- termapy sent
the command and got a reply.  Reading device errors from exit codes
requires a device profile that parses the response, or you can
``grep -q`` the stdout yourself.

**Quoting.** `-e` takes the rest of the command line, as `ssh host cmd
args` does: the words after it, joined with single spaces, are the
command, so quotes are optional and `-e` goes last -- every word after
it belongs to the command, a `--flag` included.  Quote when the command's
own spacing matters, or when the shell would expand something in it:

```sh
termapy cfg -e start_system a=23 b=24     # the rest of the line is the command
termapy cfg -e "start_system a=23 b=24"   # the same command
termapy cfg -e "/print two  spaces"       # quoted: the shell would collapse the run
termapy cfg -e '/print $(X)'              # quoted: bash would expand $(X) itself
```

**Suppressed chrome.** Connect-time autorun (`on_connect_cmd`,
the help banner), the "`Connected: ...`" banner, and the
input-echo prefix (`> AT+VER`)
are all suppressed in `--exec` -- captured stdout contains only the
command's output.  `--exec` also implies `--no-color` so ANSI
escapes never reach captured stdout.  `-e` runs exactly one command;
if your one-shot needs init steps, write a `.run` file and use
`--run`.

**Pair with `request_mode=true`** in cfg to emit a JSON envelope
(`{cmd, success, error, elapsed_s, result}`) instead of plain text.
Piped or captured, each envelope is one line, so a consumer can read
stdout line by line. At a terminal termapy lays it out for a person, as
`json_fmt.indent` and `json_fmt.highlight` say (`/term.json_fmt`).

**Mutual exclusion.** `--exec` and `--run` can't both be set; passing
both exits with an argparse error.

## Try without hardware: `TERMAPY_DEMO_FLEET`

Want to see what `--ports`, `--watch`, or `--info` look like without any
USB-serial adapters plugged in?  Set `TERMAPY_DEMO_FLEET=1` and termapy
will enumerate a fixed three-port synthetic fleet (FTDI FT232R on COM3,
Silicon Labs CP2102 on COM4, Microsoft USB Serial on COM7) instead of
calling the OS.  It is a switch, not a boolean: any non-empty value
turns it on.

```sh
TERMAPY_DEMO_FLEET=1 termapy --ports    # fake three-port table
TERMAPY_DEMO_FLEET=1 termapy --info=COM4  # fake CP2102 info dump
```

Useful for docs, screenshots, trying the tool before you own hardware,
and cross-platform bug reports.  This is a sibling to the `DEMO` port
(which fakes an *open* serial connection) and `DEMO_FAIL` (which makes
opens deterministically fail).
