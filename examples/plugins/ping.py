"""Example plugin: AT-specific ping with 500ms timeout.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /at_ping available.

Demonstrates building a device-specific command using parse_keywords.
"""

import time

from termapy.plugins import CmdResult, Command, PluginContext
from termapy.scripting import parse_keywords


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    kw = parse_keywords(args, {"cmd"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "AT")
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")
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
)
