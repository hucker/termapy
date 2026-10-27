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
from termapy_json import CommandError, int_arg, on_off_arg, serve

# The DevKitC-1's addressable RGB LED, "driven by GPIO48" (Espressif's
# ESP32-S3-DevKitC-1 v1.0 user guide); one pixel, a red, a green and a
# blue LED in one package, each with its own level from 0 to 255.
pixel = neopixel.NeoPixel(machine.Pin(48, machine.Pin.OUT), 1)
# The LED keeps its last color through a reset; start it dark, matching the buffer.
pixel.write()

# led on sets the red, green and blue LEDs all to this level: a dim white.
ON_LEVEL = 32


def cmd_led(args):
    if len(args) > 1:
        raise CommandError("usage: led {on|off|1|0}")
    if args:
        level = ON_LEVEL if on_off_arg(args[0], "state") else 0
        pixel[0] = (level, level, level)
        pixel.write()
    return "on" if any(pixel[0]) else "off"


def cmd_led_rgb(args):
    if len(args) not in (0, 3):
        raise CommandError("usage: led_rgb {<r> <g> <b>}")
    if args:
        # Every level is read before any is set, so a bad one changes nothing.
        pixel[0] = tuple(int_arg(word, "level", 0, 255) for word in args)
        pixel.write()
    return list(pixel[0])


def cmd_temp(args):
    # The chip's own sensor, whole degrees C.
    return esp32.mcu_temperature()


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


# Command name to (one-line help, handler); help and json are the monitor's own.
COMMANDS = {
    "led": ("the RGB LED on, a dim white, or off: led on|off|1|0; bare led reads", cmd_led),
    "led_rgb": (
        "the RGB LED's levels: led_rgb <r> <g> <b>, 0-255 each, decimal or 0x hex; "
        "bare led_rgb reads",
        cmd_led_rgb,
    ),
    "temp": ("the on-chip temperature in degrees C", cmd_temp),
    "id": ("the chip's unique ID in hex, its factory MAC address", cmd_id),
}

serve(COMMANDS)
