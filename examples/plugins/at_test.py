"""Example plugin: send AT commands to a serial device.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make !at available.

Demonstrates using the serial port through the plugin context.
"""

NAME = "at"
ARGS = "{cmd}"
HELP = "Send an AT command (default: AT). Waits for response."


def handler(ctx, args):
    cmd = args.strip() or "AT"
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return
    ctx.write(f"> {cmd}", "purple")
    ctx.serial_write((cmd + "\r\n").encode())
    ctx.serial_wait_idle()
