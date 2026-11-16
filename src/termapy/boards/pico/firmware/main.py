"""termapy's starter monitor for a Raspberry Pi Pico running MicroPython.

One command per line over USB serial, ended by CR, LF or both.  It runs on
the board, not in termapy: copy it, termapy_json.py and termapy_mem.py to
the board, and MicroPython starts it at every power-up.  Ctrl-C in a
terminal stops it and returns to the MicroPython prompt; Ctrl-D restarts it.

The commands are the decorated handlers below, each with a one-line help.
The monitor adds its own: ``help`` lists every command with its help line,
and ``json {on|off}`` switches the reply framing.  A new command is a
handler with an ``@commands.command(name, help)`` line.  termapy_mem.py adds
the MEM commands that termapy's /mem.* speaks.

``objects={"machine": machine}`` puts MicroPython's machine module on the
link with no handler written: ``machine`` lists its names, and
``machine.<name> {values}`` reads or calls one, found by attribute on the
board: ``machine.freq``, or ``machine.Pin.value 2`` for ``Pin(2).value()``.
Nothing is evaluated as Python.  A project adds its own objects to the same
dict.

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
from termapy_json import CommandError, CommandSet, Monitor, on_off_arg
from termapy_mem import direct

led = machine.Pin("LED", machine.Pin.OUT)
sensor = machine.ADC(4)

commands = CommandSet()


@commands.command("led", "the onboard LED: led on|off|1|0 sets it, bare led reads it")
def cmd_led(args):
    if len(args) > 1:
        raise CommandError("usage: led {on|off|1|0}")
    if args:
        led.value(on_off_arg(args[0], "state"))
    return "on" if led.value() else "off"


@commands.command("temp", "the on-chip temperature in degrees C")
def cmd_temp(args):
    # RP2040 datasheet, section 4.9.5: 27 C reads 0.706 V, -1.721 mV per degree.
    volts = sensor.read_u16() * 3.3 / 65535
    return round(27 - (volts - 0.706) / 0.001721, 1)


@commands.command("id", "the board's unique ID in hex, also its USB serial number")
def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


async def main():
    # The MEM commands for termapy's /mem.*: pure reads and writes of any address.
    # termapy checks addresses against the part's JSON; this board's own rules,
    # if it needs any, are a guard(addr, count, write) passed to direct().
    # objects: machine is answered by name, machine.<name> {values} found by attribute.
    monitor = Monitor(commands.table, native=direct().commands(), objects={"machine": machine})
    # The project's own tasks start here, beside the monitor's:
    # asyncio.create_task(my_task())
    await monitor.run()


asyncio.run(main())
