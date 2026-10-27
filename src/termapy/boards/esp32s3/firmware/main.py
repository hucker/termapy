"""termapy's starter monitor for an ESP32-S3 DevKitC-1 running MicroPython.

One command per line over USB serial, ended by CR, LF or both, on either
USB port: the chip's own USB (the one termapy's config connects to, by its
serial number) and the USB-to-UART port.  It runs on the board, not in
termapy: copy it and termapy_json.py to the board, and MicroPython starts it
at every power-up.  Ctrl-C in a terminal stops it and returns to the
MicroPython prompt; Ctrl-D restarts it.

The commands are the COMMANDS table at the bottom, each a one-line help and
a handler.  The monitor adds two of its own: ``help`` lists every command
with its help line, and ``json {on|off}`` switches the reply framing.  A new
command is a handler and a row.

termapy_json.py is the monitor every MicroPython starter shares: in text
framing a reply is the value and a failure ``ERR <reason>``; in JSON framing
each reply is one JSON-RPC response line, and a ``{...}`` request line
switches to it by itself.  termapy's ESP32-S3 profile describes these
commands, so they show in /help and reach an MCP agent as typed tools.
"""

import esp32
import machine
import neopixel
from termapy_json import CommandError, int_arg, serve

# The DevKitC-1's addressable RGB LED, "driven by GPIO48" (Espressif's
# ESP32-S3-DevKitC-1 v1.0 user guide); one pixel, a red, a green and a
# blue LED in one package, each with its own level from 0 to 255.
pixel = neopixel.NeoPixel(machine.Pin(48, machine.Pin.OUT), 1)
# The LED keeps its last color through a reset; start it dark, matching the buffer.
pixel.write()

# The words led takes for a whole color: on is a dim white.
LED_COLORS = {"on": (32, 32, 32), "off": (0, 0, 0)}


def cmd_led(args):
    if len(args) == 1 and args[0] in LED_COLORS:
        pixel[0] = LED_COLORS[args[0]]
    elif len(args) == 3:
        # Every level is read before any is set, so a bad one changes nothing.
        pixel[0] = tuple(int_arg(word, "level", 0, 255) for word in args)
    elif args:
        raise CommandError("usage: led {on|off|<r> <g> <b>}")
    if args:
        pixel.write()
    return list(pixel[0])


def cmd_temp(args):
    # The chip's own sensor, whole degrees C.
    return esp32.mcu_temperature()


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


# Command name to (one-line help, handler); help and json are the monitor's own.
COMMANDS = {
    "led": ("the RGB LED: led <r> <g> <b> (0-255 each) or led on|off; bare led reads", cmd_led),
    "temp": ("the on-chip temperature in degrees C", cmd_temp),
    "id": ("the chip's unique ID in hex, its factory MAC address", cmd_id),
}

serve(COMMANDS)
