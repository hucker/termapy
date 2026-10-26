"""termapy's starter monitor for a Raspberry Pi Pico running MicroPython.

One command per line over USB serial, ended by CR, LF or both, and one
answer per line.  It runs on the board, not in termapy: copy it to the
board as main.py and MicroPython starts it at every power-up.  Ctrl-C in a
terminal stops it and returns to the MicroPython prompt; Ctrl-D restarts it.

    help          list the commands
    led on|off    the onboard LED
    temp          on-chip temperature in degrees C
    id            the board's unique ID, in hex (also its USB serial number)

Errors answer ``ERR <reason>``.  termapy's Pico profile describes these
commands, so they show in /help and reach an MCP agent as typed tools.
"""

import sys

import machine

led = machine.Pin("LED", machine.Pin.OUT)
sensor = machine.ADC(4)

# The words led takes and the pin level each sets.
LED_LEVELS = {"on": 1, "off": 0}


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
    return "commands: " + " ".join(sorted(COMMANDS))


def cmd_led(args):
    level = LED_LEVELS.get(args[0]) if args else None
    if level is None:
        return "ERR usage: led on|off"
    led.value(level)
    return "OK"


def cmd_temp(args):
    # RP2040 datasheet, section 4.9.5: 27 C reads 0.706 V, -1.721 mV per degree.
    volts = sensor.read_u16() * 3.3 / 65535
    return f"{27 - (volts - 0.706) / 0.001721:.1f}"


def cmd_id(args):
    return "".join(f"{byte:02X}" for byte in machine.unique_id())


COMMANDS = {"help": cmd_help, "led": cmd_led, "temp": cmd_temp, "id": cmd_id}

while True:
    parts = read_line().split()
    handler = COMMANDS.get(parts[0])
    if handler is None:
        print("ERR unknown command: " + parts[0])
        continue
    try:
        print(handler(parts[1:]))
    except Exception as e:
        print("ERR " + str(e))
