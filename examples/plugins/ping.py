"""Example plugin: AT-specific ping with 500ms timeout.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /at_ping available.

Demonstrates building a device-specific command using parse_keywords.
"""

import time

from termapy.plugins import CapabilitySet, CmdResult, Command, PluginContext
from termapy.scripting import parse_keywords

# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send ``cmd=`` (``AT`` by default) and time the round trip; the value is the time in ms."""
    kw = parse_keywords(args, {"cmd"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "AT")
    start = time.perf_counter()
    ctx.serial.send(cmd)
    ctx.serial.wait_idle(timeout_ms=500)
    ms = (time.perf_counter() - start) * 1000
    ctx.io.output(f"{cmd} -- {ms:.0f}ms", "green")
    # Return RTT in ms so scripts can capture / threshold it.
    return CmdResult.ok(value=f"{ms:.0f}")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="at_ping",
    args="{cmd=<command>}",
    help="AT ping with 500ms timeout (default cmd: AT).",
    handler=_handler,
    needs=CapabilitySet.SERIAL_CONNECTED,
)
