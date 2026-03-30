"""Example plugin: AT-specific ping with 500ms timeout.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make /at_ping available.

Demonstrates building a device-specific command using parse_keywords.
"""

import time

from termapy.plugins import CmdResult, Command, PluginContext
from termapy.scripting import parse_keywords


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    kw = parse_keywords(args, {"cmd"}, rest_keyword="cmd")
    cmd = kw.get("cmd", "AT")
    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")
    start = time.perf_counter()
    ctx.serial_send(cmd)
    ctx.serial_wait_idle(timeout_ms=500)
    ms = (time.perf_counter() - start) * 1000
    ctx.write(f"{cmd} -- {ms:.0f}ms", "green")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "AT ping with 500ms timeout (default cmd: AT).",
    name="at_ping",
    args="{cmd=<command>}",
    handler=_handler,
)
