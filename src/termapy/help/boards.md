# Dev boards

A dev board starter turns a new board into a device termapy drives on day
one. It holds the code that runs on the board, plus a host config and a
device profile. The board answers at an interactive prompt, its commands
show in `/help`, and an MCP agent gets them as typed tools. This is real
hardware; the simulator is `/demo`.

| Board  | Runs        | Device code | Board commands              |
| ------ | ----------- | ----------- | --------------------------- |
| `pico` | MicroPython | `main.py`   | `help`, `led`, `temp`, `id` |

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
With none attached it says whether the board is missing or still waiting
in its boot loader.

Board setup never replaces anything. A config by the same name stops it,
and so does any config that already connects to the attached board:
`/board.new` names that config instead of making a second one, and
`termapy --board` starts it. termapy does not write to the board itself;
copying the device code onto it is your step.

## Raspberry Pi Pico

The starter runs on MicroPython, so the first time takes three steps and
no compiler:

1. **Flash MicroPython.** Hold BOOTSEL while plugging the board in; it
   appears as a drive named `RPI-RP2`. Download the `RPI_PICO` `.uf2` from
   micropython.org and drag it onto the drive. The board restarts as a USB
   serial port.
2. **Copy the device code.** `/board.firmware pico` writes `main.py` to the
   current folder; `uvx mpremote cp main.py :main.py + reset` puts it on
   the board, where MicroPython runs it at every power-up.
3. **Create the config.** `/board.new pico`, then `/cfg.load pico`, or
   start termapy with `termapy --board pico`.

The board then answers one command per line:

| Command        | Answer                                                     |
| -------------- | ---------------------------------------------------------- |
| `help`         | `commands: help id led temp`                               |
| `led on\|off`  | `OK`, and the onboard LED follows                          |
| `temp`         | the on-chip sensor in degrees C, such as `21.4`            |
| `id`           | the board's unique ID, which is also its USB serial number |

Errors answer `ERR <reason>`. Ctrl-C in the terminal stops the monitor and
leaves the MicroPython `>>>` prompt; Ctrl-D restarts it. The on-chip
sensor reads a few degrees off and is noisy; the RP2040 datasheet says as
much.
