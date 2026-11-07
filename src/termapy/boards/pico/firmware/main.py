"""termapy's starter monitor for a Raspberry Pi Pico running MicroPython.

One command per line over USB serial, ended by CR, LF or both.  It runs on
the board, not in termapy: copy it and termapy_json.py to the board, and
MicroPython starts it at every power-up.  Ctrl-C in a terminal stops it and
returns to the MicroPython prompt; Ctrl-D restarts it.

The commands are the COMMANDS table at the bottom, each a one-line help and
a handler.  The monitor adds two of its own: ``help`` lists every command
with its help line, and ``json {on|off}`` switches the reply framing.  A new
command is a handler and a row.

The board runs asyncio: the monitor is one task, ``monitor.run()``, and the
project's own tasks start beside it in ``main()``.  A project with a super
loop of its own calls ``monitor.poll()`` on every pass instead.

termapy_json.py is the monitor every MicroPython starter shares: in text
framing a reply is the value and a failure ``ERR <reason>``; in JSON framing
each reply is one JSON-RPC response line, and a ``{...}`` request line
switches to it by itself.  termapy's Pico profile describes these commands,
so they show in /help and reach an MCP agent as typed tools.
"""

import asyncio

import machine
from termapy_json import CommandError, Monitor, on_off_arg

led = machine.Pin("LED", machine.Pin.OUT)
sensor = machine.ADC(4)


def cmd_led(args):
    if len(args) > 1:
        raise CommandError("usage: led {on|off|1|0}")
    if args:
        led.value(on_off_arg(args[0], "state"))
    return "on" if led.value() else "off"


def cmd_temp(args):
    # RP2040 datasheet, section 4.9.5: 27 C reads 0.706 V, -1.721 mV per degree.
    volts = sensor.read_u16() * 3.3 / 65535
    return round(27 - (volts - 0.706) / 0.001721, 1)


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


# Command name to (one-line help, handler); help and json are the monitor's own.
COMMANDS = {
    "led": ("the onboard LED: led on|off|1|0 sets it, bare led reads it", cmd_led),
    "temp": ("the on-chip temperature in degrees C", cmd_temp),
    "id": ("the board's unique ID in hex, also its USB serial number", cmd_id),
}


async def main():
    monitor = Monitor(COMMANDS)
    # The project's own tasks start here, beside the monitor's:
    # asyncio.create_task(my_task())
    await monitor.run()


asyncio.run(main())
