"""Example plugin: send AT commands to a serial device.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /at available.

Demonstrates using the serial port through the plugin context.
"""

from termapy.plugins import CmdResult, Command, PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
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
    name="at",
    args="{cmd}",
    help="Send an AT command (default: AT). Waits for response.",
    handler=_handler,
)
