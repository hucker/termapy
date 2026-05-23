"""Example plugin: send AT commands to a serial device.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make /at available.

Demonstrates using the serial port through the plugin context.
"""


from termapy.plugins import CmdResult, Command


def _handler(ctx, args):
    cmd = args.strip() or "AT"
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")
    ctx.io.output(f"> {cmd}", "purple")
    ctx.serial.send(cmd)
    ctx.serial.wait_idle()
    # Return the sent command so scripts can capture it via
    # ``$(SENT) <- /at AT+VER``.
    return CmdResult.ok(value=cmd)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send an AT command (default: AT). Waits for response.",
    name="at",
    args="{cmd}",
    handler=_handler,
)
