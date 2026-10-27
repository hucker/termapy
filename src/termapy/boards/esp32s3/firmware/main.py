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
from termapy_json import CommandError, serve

# The DevKitC-1's addressable RGB LED, "driven by GPIO48" (Espressif's
# ESP32-S3-DevKitC-1 v1.0 user guide); one pixel.
pixel = neopixel.NeoPixel(machine.Pin(48, machine.Pin.OUT), 1)
# The LED keeps its last color through a reset; start it dark, matching the buffer.
pixel.write()

# The words led takes and the color each shows: on is a dim white.
LED_COLORS = {"on": (32, 32, 32), "off": (0, 0, 0)}


def cmd_led(args):
    if not args:
        return "off" if pixel[0] == LED_COLORS["off"] else "on"
    color = LED_COLORS.get(args[0])
    if color is None or len(args) > 1:
        raise CommandError("usage: led {on|off}")
    pixel[0] = color
    pixel.write()
    return args[0]


def cmd_temp(args):
    # The chip's own sensor, whole degrees C.
    return esp32.mcu_temperature()


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


# Command name to (one-line help, handler); help and json are the monitor's own.
COMMANDS = {
    "led": ("the RGB LED: led on|off sets it, bare led reads it", cmd_led),
    "temp": ("the on-chip temperature in degrees C", cmd_temp),
    "id": ("the chip's unique ID in hex, its factory MAC address", cmd_id),
}

serve(COMMANDS)
