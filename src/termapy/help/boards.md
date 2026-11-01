# Dev boards

> **Status: prototype.** This is new and incomplete: its commands, files and behavior may change or be removed in any release.

A dev board starter turns a new board into a device termapy drives on day
one. It holds the code that runs on the board, plus a host config and a
device profile. The board answers at an interactive prompt, its commands
show in `/help`, and an MCP agent gets them as typed tools. This is real
hardware; the simulator is `/demo`.

| Board     | Runs        | Device code                                     | Board commands                                            |
| --------- | ----------- | ----------------------------------------------- | --------------------------------------------------------- |
| `pico`    | MicroPython | `main.py`, `termapy_json.py`                    | `help`, `led`, `temp`, `id`, `json`                       |
| `esp32s3` | MicroPython | `main.py`, `termapy_json.py`, `termapy_mem.py`  | `help`, `led`, `led_rgb`, `temp`, `id`, `json`, `MEM.*`   |

```text
/board                    the boards, and which is plugged in now
/board.firmware pico      copy the board's device code here, for flashing
/board.new pico           create the config and profile for the attached board
/cfg.load pico            switch to it
termapy --board pico      the same from the command line: create once, then start it
```

The config connects by the board's USB serial number, so a COM number
that moves between plug-ins does not matter, and the same config works on
Windows, Linux and macOS. `/board.new` finds the board by its USB VID:PID.
When several are attached it takes the first and says how many it skipped.
With none attached it says whether the board is missing or attached but
not running MicroPython yet.

Everything for the board lands in its config's folder: the config, its
profile, and the device code in `firmware/`, so a new board is one folder
under `termapy_cfg/` and nothing in the folder you started termapy from.

Board setup never replaces anything. A config by the same name stops it,
and so does any config that already connects to the attached board:
`/board.new` names that config instead of making a second one, and
`termapy --board` starts it. termapy does not write to the board itself;
flashing it and copying the device code onto it are your steps.

## Raspberry Pi Pico

The starter runs on MicroPython, so the first time takes three steps and
no compiler:

1. **Flash MicroPython.** Hold BOOTSEL while plugging the board in; it
   appears as a drive named `RPI-RP2`. Download the `RPI_PICO` `.uf2` from
   micropython.org and drag it onto the drive. The board restarts as a USB
   serial port.
2. **Copy the device code.** `/board.firmware pico` writes `main.py` and
   `termapy_json.py` to the active config's `firmware/` folder and prints
   the one command that puts them on the board, with the board's port and
   the files' full paths:
   `uvx mpremote connect COM5 cp ...\main.py ...\termapy_json.py : + reset`.
   MicroPython then runs `main.py` at every power-up.
3. **Create the config.** `/board.new pico`, then `/cfg.load pico`, or
   start termapy with `termapy --board pico`.

The on-chip sensor reads a few degrees off and is noisy; the RP2040
datasheet says as much.

## ESP32-S3 DevKitC-1

The board has two USB ports. Use the one wired to the chip's own USB
(usually marked USB): there the board has a unique serial number. The
other port goes through a USB-to-UART bridge chip, which reports its own
maker and no serial number, so nothing on it says which board it is.

1. **Flash MicroPython.** Download `ESP32_GENERIC_S3` from micropython.org:
   the SPIRAM_OCT build for a module with octal PSRAM (N8R8, N16R8; esptool
   shows it as "Embedded PSRAM 8MB"), the plain one otherwise. Then, with
   the port the chip's USB shows now:

   ```text
   uvx esptool --chip esp32s3 --port COM8 erase-flash
   uvx esptool --chip esp32s3 --port COM8 write-flash 0 ESP32_GENERIC_S3-SPIRAM_OCT-<date>-v<version>.bin
   ```

   The board restarts as a new serial port, MicroPython's (`303A:4001`).
2. **Copy the device code.** `/board.firmware esp32s3`, then the
   `uvx mpremote` line it prints, as on the Pico; the S3 has a third file,
   `termapy_mem.py`.
3. **Create the config.** `/board.new esp32s3`, then `/cfg.load esp32s3`,
   or start termapy with `termapy --board esp32s3`.

The board's RGB LED on GPIO48 is a red, a green and a blue LED in one
package, each with its own level. `led` switches it as on the Pico: `on`
is a dim white, every level 32. `led_rgb` sets the three levels:
`led_rgb 255 0 0` is full red, each level 0 to 255 in decimal or `0x` hex.
A level out of range is refused, not clipped. Bare `led_rgb` reports the
levels: `[255,0,0]` in JSON, one level per line in text. A board with its
LED on another pin changes one line of `main.py`. `id` is the chip's
factory MAC address. MicroPython answers on the UART port too, but the
config uses the chip's own USB, by its serial number.

The S3 also answers the MEM commands that `/mem.*` speaks ([Memory](memory.md)),
from `termapy_mem.py`: `MEM.R`, `MEM.W`, `MEM.M` and `MEM.INFO`. The board
reads and writes whatever address it is given and knows nothing about its
memory; checking the address is termapy's job, from the JSON that
describes the part. termapy does not check yet, so for now an address the
chip cannot reach resets it. A board that must fence memory off passes a
guard to `direct()` in `main.py`: `guard(addr, count, write)` sees every
access first, and returning False refuses it with `ERR range`. The MEM
replies are the wire spec's text even when the board answers MCP in JSON,
so `/mem.*` works the same in the TUI and over MCP.

## The board's commands

Each board answers one command per line:

| Command          | Text reply                          | JSON reply                         |
| ---------------- | ----------------------------------- | ---------------------------------- |
| `help`           | one `name  help` line per command   | `{"result":{"led":"the onboard LED...",...}}` |
| `help <command>` | that command's help line            | `{"result":"the on-chip temperature in degrees C"}` |
| `led`            | the LED's state                     | `{"result":"on"}`                  |
| `led on\|off\|1\|0` | the new state, and the LED follows | `{"result":"off"}`              |
| `led_rgb`        | ESP32-S3: the levels, one per line  | `{"result":[255,0,0]}`             |
| `led_rgb <r> <g> <b>` | ESP32-S3: the new levels       | `{"result":[0,0,32]}`              |
| `temp`           | degrees C, such as `21.4`           | `{"result":21.4}`, a number        |
| `id`             | the board's unique ID in hex        | `{"result":"E66548545710822C"}`    |
| `json {on\|off}` | the framing, `on` or `off`          | `{"result":"on"}`                  |
| `MEM.R`, `MEM.W`, `MEM.M`, `MEM.INFO` | ESP32-S3: the [memory wire spec](memory.md)'s rows, `OK` or `ERR` | the same text: MEM keeps its own |

The board speaks two framings. Text, the default, is for a person: a reply
is the value, and an error is `ERR <reason>`. JSON is for termapy's MCP
server and any JSON-RPC client: each reply is one JSON-RPC 2.0 response
line, the same contract as the C module on [JSON integration](firmware-json.md),
with an error as `{"error":{"code":...,"message":...}}`. `json on` switches
to it, and a request line such as `{"method":"temp","id":1}` switches by
itself and gets its `id` back. Each board's config sends `json on` when
the MCP server connects, so an agent gets typed values while the TUI stays
in text.

Each command in `main.py` is a row of its `COMMANDS` table: the name, a
one-line help and the handler, which takes the argument words and returns
the reply value. `help` reads its answer from that table, so a new command
is a handler and a row, and `help` lists it at once. `termapy_json.py` is
the monitor itself, one file every MicroPython board shares.

The board's profile describes every command in this table and every form
of it, `help` and `json` included, so `/help` shows them all and an MCP
agent can call each one, its arguments checked before they are sent. A new
command also gets its profile entry; the starter's tests fail until it has
one.

Ctrl-C stops the monitor and leaves the MicroPython `>>>` prompt; Ctrl-D
restarts it. In the TUI, press **Ctrl+C** with no text selected, and
**Ctrl+D** on an empty prompt. From a script, the CLI or an MCP agent, send
them with `/term.ctrl c` and `/term.ctrl d`.
