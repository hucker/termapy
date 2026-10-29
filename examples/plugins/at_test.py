"""Example plugin: send AT commands to a serial device.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /at available.

Demonstrates using the serial port through the plugin context, and
declaring the open port it needs so the dispatcher answers
"Not connected." instead of the handler.
"""

from termapy.plugins import CapabilitySet, CmdResult, Command, PluginContext

# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send an AT command (``AT`` when bare) and wait for the reply; the value is the command sent."""
    cmd = args.strip() or "AT"
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
    needs=CapabilitySet.SERIAL_CONNECTED,
)
