# Dev boards

> **Status: prototype.** This is new and incomplete: its commands, files and behavior may change or be removed in any release.

A dev board starter turns a new board into a device termapy drives on day
one. It holds the code that runs on the board, plus a host config and a
device profile. The board answers at an interactive prompt, its commands
show in `/help`, and an MCP agent gets them as typed tools. This is real
hardware; the simulator is `/demo`.

| Board     | Runs        | Device code                                                        | Board commands                                                                  |
| --------- | ----------- | ------------------------------------------------------------------ | ------------------------------------------------------------------------------ |
| `pico`    | MicroPython | `main.py`, `termapy_json.py`, `termapy_mem.py`, `termapy_xmodem.py` | `help`, `led`, `temp`, `id`, `machine`, `json`, `xmodem`, `MEM.*`              |
| `esp32s3` | MicroPython | `main.py`, `termapy_json.py`, `termapy_mem.py`, `termapy_xmodem.py` | `help`, `led`, `led_rgb`, `temp`, `id`, `machine`, `json`, `xmodem`, `MEM.*`   |

The `xmodem recv` / `xmodem send` commands make a MicroPython board an XMODEM
peer: pair them with the host's [`/xfer.xmodem.*`](file-transfer.md) to move a
file to or from the board over the serial link.

```text
/board                    the boards, and which is plugged in now
/board.firmware pico      copy the board's device code here, for flashing
/board.new pico           create the config and profile for the attached board
/cfg.load pico            switch to it
termapy --board pico      the same from the command line: create once, then start it
/board.update             after upgrading termapy: bring the config's profile and device code up to date
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
The config's `profile_path` names the profile, so it loads at every
connect, in the TUI, the CLI and MCP alike, and `/help.target` lists the
board's commands with nothing typed first.

Board setup never replaces anything. A config by the same name stops it,
and so does any config that already connects to the attached board:
`/board.new` names that config instead of making a second one, and
`termapy --board` starts it. termapy does not write to the board itself;
flashing it and copying the device code onto it are your steps.

## Updating a board config

A board config keeps the profile and the device code it was made with, so
after termapy is upgraded they can be older than what termapy ships.
`/board.update` brings the active config up to date:

```text
esp32s3> /board.update
Updated esp32s3:
  esp32s3.profile.json  1.1.0 -> 2.2.0
  firmware/main.py
  firmware/termapy_xmodem.py  (new)
  esp32s3.cfg  profile_path = esp32s3.profile.json
  esp32s3.cfg  protocol = ndjson
Loaded esp32s3.profile.json: 13 commands (rev 2.2.0).
Put the device code on the board: uvx mpremote connect COM9 cp ... : + reset
```

It replaces only the copies that differ from what termapy ships, after
listing them and asking. The config gets the settings the board's code
depends on: `profile_path` when it names no profile, and `protocol`, so
JSON mode sends ids. A file you edited is replaced too, so read the list
before you answer. Then it loads the profile, and when device code changed
it prints the `mpremote` line that puts it on the board; run that to
finish. The board is the one the config is named for, or name it:
`/board.update pico`. An MCP agent cannot run it.

## Raspberry Pi Pico

The starter runs on MicroPython, so the first time takes three steps and
no compiler:

1. **Flash MicroPython.** Hold BOOTSEL while plugging the board in; it
   appears as a drive named `RPI-RP2`. Download the `RPI_PICO` `.uf2` from
   micropython.org and drag it onto the drive. The board restarts as a USB
   serial port.
2. **Copy the device code.** `/board.firmware pico` writes `main.py`,
   `termapy_json.py`, `termapy_mem.py` and `termapy_xmodem.py` to the active
   config's `firmware/` folder and prints the one command that puts them on
   the board, with the board's port and the files' full paths:
   `uvx mpremote connect COM5 cp ...\main.py ...\termapy_json.py ...\termapy_mem.py ...\termapy_xmodem.py : + reset`.
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
   `uvx mpremote` line it prints, as on the Pico.
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

## Memory

Both boards answer the MEM commands that `/mem.*` speaks ([Memory](memory.md)),
from `termapy_mem.py`: `MEM.R`, `MEM.W`, `MEM.M` and `MEM.INFO`. The board
reads and writes whatever address it is given and knows nothing about its
memory; checking the address is termapy's job, from the JSON that
describes the part. termapy does not check yet, so for now an address the
chip cannot reach resets it. A board that must fence memory off passes a
guard to `direct()` in `main.py`: `guard(addr, count, write)` sees every
access first, and returning False refuses it with `ERR range`. The MEM
replies are the wire spec's text even when the board answers MCP in JSON,
so `/mem.*` works the same in the TUI and over MCP.

This is full access to the chip, so an MCP agent gets it in two tiers. It
reads with `/mem.read`, `/mem.dump` and `/mem.info` freely. Every write
(`/mem.write`, `.or`, `.and`, `.xor`, `.clear`, `.not`) and every raw
`MEM.R`, `MEM.W` or `MEM.M` is destructive: it runs only after you confirm
it.

## Hardware by name

Both boards put MicroPython's `machine` module on the link, so every pin,
converter, bus and timer the board has is reachable with no command written
for it:

```text
machine                                 the module's names: ADC, I2C, Pin, PWM, freq, ...
machine.Pin                             a class's names: IN, OUT, value, ...
machine.Pin.OUT                         a constant's value
machine.freq                            the CPU clock, a number
machine.Pin.init 2 machine.Pin.OUT      make GPIO2 an output
machine.Pin.value 2 1                   drive it high
machine.Pin.value 2                     read it back
machine.ADC.read_u16 26                 one conversion on GPIO26
machine.I2C.scan 0                      the addresses that answer on bus 0
```

`machine` alone lists what the module has, and a class lists its own names
the same way, so you find what is there by asking the board. A line is a
dotted name and then its values, separated by spaces, like any other
command. The board finds the name by attribute: a function is called with
the values, and a constant answers its value. The line is never run as
Python, so `machine.freq()` is an unknown name.

A method needs its object, so a method reached through its class takes the
object's id as its first value: `machine.Pin.value 2 1` is
`Pin(2).value(1)`, and `machine.I2C.scan 0` is `I2C(0).scan()`. A value is
a number in decimal or `0x` hex, a quoted string with no spaces
(`machine.Pin.value "LED" 1`), a path such as `machine.Pin.OUT`, or
`name=value` for a keyword (`machine.Pin.init 2 mode=machine.Pin.OUT`). A
bare word is refused, never taken as text. Bytes cannot be given as a
value yet, so `I2C.writeto` is out of reach.

A number, a string or a list answers as itself, bytes as uppercase hex, and
anything else as Python prints it. A name that is not there is
`ERR unknown name: machine.nosuch`, and an exception from the call is the
error, named by its type: `ERR TypeError: ...`.

Every name `machine` has is in reach. `machine.reset` restarts the board
and `machine.bootloader` leaves MicroPython, and nothing checks a pin
number before it is used. So an MCP agent lists with `machine` freely, and
a `machine.` call is destructive: the agent is asked to confirm it with
you, as it is a memory write. A call that never returns stops the board
answering until you reset it.

This comes from one line of `main.py`, `objects={"machine": machine}`. A
project puts its own objects on the link by adding them to that dict, each
answered by its name the same way: with `"led": machine.Pin(25,
machine.Pin.OUT)` there, `led.value 1` drives it. Leave the argument out
and the board looks nothing up.

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
| `machine`        | the module's names, one per line    | `{"result":["ADC","I2C","Pin",...]}` |
| `machine.<name> {values}` | the name's value, or its call's | `{"result":125000000}`          |
| `json {on\|off}` | the framing, `on` or `off`          | `{"result":"on"}`                  |
| `MEM.R`, `MEM.W`, `MEM.M`, `MEM.INFO` | the [memory wire spec](memory.md)'s rows, `OK` or `ERR` | the same text: MEM keeps its own |

The board speaks two framings. Text, the default, is for a person: a reply
is the value, and an error is `ERR <reason>`. JSON is for termapy's MCP
server and any JSON-RPC client: each reply is one JSON-RPC 2.0 response
line, the same contract as the C module on [JSON integration](firmware-json.md),
with an error as `{"error":{"code":...,"message":...}}`. `json on` switches
to it, and a request line such as `{"method":"temp","id":1}` switches by
itself and gets its `id` back. Each board's config sends `json on` at
every connect, so the TUI, the CLI and an MCP agent all see the same JSON.

In JSON mode (`/term.request on`, a device line with `--json`, and always
under MCP) termapy sends each command as a request with an id, because the
board's config says `protocol: "ndjson"`: `temp` goes out as
`{"cmd":"temp","id":7}` and `machine.Pin.value 2 1` as
`{"cmd":"machine.Pin.value","args":"2 1","id":8}`. The answer is the one
line that carries the id back, so a late reply to an earlier command or a
line your own code printed is never taken for it. Typed at the prompt, a
command goes out as you typed it: a REPL has no ids.
On screen termapy shows each JSON line the board sends indented and
highlighted (`/term.json_fmt`), while the session log and captures keep it as
it arrived. `/term.json_fmt.indent 0` with `/term.json_fmt.highlight off` shows
the lines as they arrive.

Each command in `main.py` is a decorated handler: a name, a one-line help,
and a function that takes the argument words and returns the reply value.
`help` reads its answer from the same registration, so a new command is a
handler with one `@commands.command(...)` line, and `help` lists it at once.
`termapy_json.py` is the monitor itself, one file every MicroPython board
shares.

```python
from termapy_json import CommandSet, Monitor

commands = CommandSet()


@commands.command("led", "the onboard LED: led on|off")
def cmd_led(args): ...


monitor = Monitor(commands.table, native=direct().commands())
```

If you prefer to see every command in one place, a plain dict is
equivalent: `commands.table` is just `{name: (help, handler)}`, so you can
build that dict yourself and pass it to `Monitor`. The decorator and the
dict are the same mechanism.

`main.py` runs asyncio. The monitor is one task, `monitor.run()`, and your
project's own tasks start beside it in `main()`. The monitor answers a
command once its line ending (CR, LF or both) is in, then yields to the
other tasks; with nothing waiting it sleeps 10 ms. A line longer than 512
characters is refused with `ERR line too long`. A project with a super loop
of its own calls `monitor.poll()` on every pass instead: it never waits,
answers at most one command, and keeps a partial line for the next pass. A
board that only answers commands can call `serve(COMMANDS)`, which waits
for each character.

The board's profile describes every command in this table and every form
of it, `help` and `json` included, so `/help` shows them all and an MCP
agent can call each one, its arguments checked before they are sent. A new
command also gets its profile entry; the starter's tests fail until it has
one.

Ctrl-C stops the monitor and leaves the MicroPython `>>>` prompt; Ctrl-D
restarts it. In the TUI, press **Ctrl+C** with no text selected, and
**Ctrl+D** on an empty prompt. From a script, the CLI or an MCP agent, send
them with `/term.ctrl c` and `/term.ctrl d`.
