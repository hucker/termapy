"""termapy's starter monitor for a Raspberry Pi Pico running MicroPython.

One command per line over USB serial, ended by CR, LF or both.  It runs on
the board, not in termapy: copy it and termapy_json.py to the board, and
MicroPython starts it at every power-up.  Ctrl-C in a terminal stops it and
returns to the MicroPython prompt; Ctrl-D restarts it.

    help          list the commands
    led {on|off}  the onboard LED; bare, its state
    temp          on-chip temperature in degrees C, a number
    id            the board's unique ID in hex (also its USB serial number)
    json {on|off} the reply framing: text for a person, JSON-RPC for termapy's MCP

termapy_json.py is the reply envelope: in text framing a reply is the value
and a failure ``ERR <reason>``; in JSON framing each reply is one JSON-RPC
response line, and a ``{...}`` request line switches to it by itself.
termapy's Pico profile describes these commands, so they show in /help and
reach an MCP agent as typed tools.
"""

import sys

import machine
from termapy_json import CommandError, Monitor

led = machine.Pin("LED", machine.Pin.OUT)
sensor = machine.ADC(4)

# The words led takes, the pin level each sets, and back.
LED_LEVELS = {"on": 1, "off": 0}
LED_WORDS = {1: "on", 0: "off"}

# Raw bytes out, so a JSON line ends in exactly "\n" and a text line in "\r\n".
_out = getattr(sys.stdout, "buffer", None)


def write(text):
    """Send one reply line, its line ending included."""
    if _out is None:
        sys.stdout.write(text)
    else:
        _out.write(text.encode())


def read_line():
    """One line from the USB serial port, CR, LF or CRLF terminated; blank lines skipped."""
    chars = []
    while True:
        char = sys.stdin.read(1)
        if char in "\r\n":
            if chars:
                return "".join(chars)
        else:
            chars.append(char)


def cmd_help(args):
    return "commands: " + " ".join(monitor.names())


def cmd_led(args):
    if not args:
        return LED_WORDS[led.value()]
    level = LED_LEVELS.get(args[0])
    if level is None or len(args) > 1:
        raise CommandError("usage: led {on|off}")
    led.value(level)
    return args[0]


def cmd_temp(args):
    # RP2040 datasheet, section 4.9.5: 27 C reads 0.706 V, -1.721 mV per degree.
    volts = sensor.read_u16() * 3.3 / 65535
    return round(27 - (volts - 0.706) / 0.001721, 1)


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


COMMANDS = {"help": cmd_help, "led": cmd_led, "temp": cmd_temp, "id": cmd_id}

monitor = Monitor(COMMANDS, write)
while True:
    monitor.handle(read_line())
