# Command-line flags

Termapy is a TUI first, but a few things are easier from the shell: **listing
what's plugged in, watching for changes, and inspecting a chip without
launching the UI**. Every `--flag` below prints plain text to stdout and
exits; none of them load the TUI.

Because these flags skip the Textual import, they start fast — no
config loading, no widget tree, no config directory scan.  Handy when
you just want to answer "what's plugged in?" from the shell.

```sh
termapy --ports                 # one-line-per-port table
termapy --watch                 # live log of plug/unplug/open/close
termapy --info                  # full per-port chip dump
termapy --info=COM4             # just COM4
```

## `--ports`: what's plugged in right now

A single-line-per-port table, same schema as the in-app port picker.

```text
$ termapy --ports
PORT  MFG   DESCRIPTION        CHIP                            SPEED       VID:PID    SN
----  ----  -----------------  ------------------------------  ----------  ---------  ------------------
COM3  MSFT  USB Serial Device  -                               -           04D8:9036  020026702RYN040952
COM4  FTDI  USB Serial Port    FTDI FT230X / FT231X / FT234XD  Full-Speed  0403:6015  D20JSV68A
COM7  FTDI  USB Serial Port    FTDI FT232R / FT245R            Full-Speed  0403:6001  BG03U7VTA
```

Columns you don't need are dropped automatically: on a machine where no
port has a serial number, the `SN` column disappears.  On narrow
terminals, speed / chip / vid_pid drop in that order until the row fits.

Filter to a single port by name:

```sh
termapy --ports=COM4
```

Exits 0 on match, 1 on "not found" or "no ports" (script-friendly).

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
[15:57:57]    COM3    closed  MSFT       USB Serial Device   -                                 -           04D8:9036  020026702RYN040952
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
  Description   USB Serial Device (COM3)
  Manufacturer  Microsoft
  Serial        020026702RYN040952
  Location      1-8.3:x.1
  VID:PID       04D8:9036
  Model         unknown
  USB speed     unknown (chip not in lookup table)
  Permissions   n/a
  In use        no
  (chip 04D8:9036 not in termapy's lookup table -- please report at https://github.com/hucker/termapy/issues so we can add it)

COM4
  Description   USB Serial Port (COM4)
  Manufacturer  FTDI
  Serial        D20JSV68A
  VID:PID       0403:6015
  Model         FTDI FT230X / FT231X / FT234XD
  USB speed     USB Full-Speed (1 ms min latency)
  Latency timer n/a (Windows - check Device Manager)  (set to 1 for low latency)
  Max baud      3,000,000 baud
  Permissions   n/a
  In use        no
```

The COM3 block shows what an unrecognized chip looks like: the VID:PID
is always printed so you can identify it manually, and termapy nudges
you to open an issue so the lookup table can grow.

Exits 0 if at least one port was printed, 1 if the named port wasn't
found or no ports are connected.

## Filtering and JSON output

`--ports` accepts these script-friendly filters (AND together):

| Flag        | Effect                                                              |
| ----------- | ------------------------------------------------------------------- |
| `--vid HEX` | Only USB devices matching this VID. Hex with or without `0x`.       |
| `--pid HEX` | Only USB devices matching this PID.                                 |
| `--mfg STR` | Manufacturer substring match (case-insensitive).                    |
| `--sn STR`  | Exact serial-number match (case-insensitive).                       |
| `--json`    | Emit a JSON array instead of the column table; also for `--chips`.  |

Example:

```sh
termapy --ports --vid 0403 --json | jq '.[].device'
```

Each `--ports --json` record has a stable schema:

```json
{
  "device": "COM4",
  "manufacturer": "FTDI",
  "manufacturer_raw": "Future Technology Devices, Inc.",
  "vendor": "FTDI",
  "description": "USB Serial Converter",
  "chip": "FTDI FT232R",
  "speed": "Full-Speed",
  "vid": 1027,
  "pid": 24577,
  "vid_pid": "0403:6001",
  "serial_number": "AL01ABCD",
  "in_use": false,
  "driver": "ftdi_sio",
  "location": "Hub_#0009.Port_#0004"
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
`vid_pid` is the canonical lowercase-hex form for literal matching.
`driver` is the kernel module name on Linux (`ftdi_sio`, `cdc_acm`,
`ch341`, `cp210x`, ...) and the Windows service name (`FTDIBUS`,
`usbser`, `silabser`, ...). On macOS `driver` is `null` for now.

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

## Other flags worth knowing

| Flag              | What it does                                                  |
| ----------------- | ------------------------------------------------------------- |
| `--chips`         | Dump the full USB-serial chip lookup table (VID:PID → model). |
| `--chips=ftdi`    | Filter the table (case-insensitive substring match).          |
| `--check`         | Validate your config, print JSON status, exit.                |
| `--cfg-dir PATH`  | Override the default config directory for this run.           |
| `--cli`           | Launch the CLI REPL instead of the TUI. With no config, shows a welcome banner listing available ports -- use `/port.connect <name>` to pick one. |
| `--run SCRIPT`    | Run a `.run` script headlessly, then exit.                    |
| `--silent`        | Output level: nothing (script reads `CmdResult.value` only).  |
| `--quiet`         | Output level: command results only.                           |
| `--verbose`       | Output level: results + data + progress chatter.              |

`termapy --help` has the full list.

## Try without hardware: `TERMAPY_DEMO_FLEET`

Want to see what `--ports`, `--watch`, or `--info` look like without any
USB-serial adapters plugged in?  Set `TERMAPY_DEMO_FLEET=1` and termapy
will enumerate a fixed three-port synthetic fleet (FTDI FT232R on COM3,
Silicon Labs CP2102 on COM4, Microsoft USB Serial on COM7) instead of
calling the OS.

```sh
TERMAPY_DEMO_FLEET=1 termapy --ports    # fake three-port table
TERMAPY_DEMO_FLEET=1 termapy --info=COM4  # fake CP2102 info dump
```

Useful for docs, screenshots, trying the tool before you own hardware,
and cross-platform bug reports.  This is a sibling to the `DEMO` port
(which fakes an *open* serial connection) and `DEMO_FAIL` (which makes
opens deterministically fail).
