"""Example plugin: measure serial response time.

Drop this file into termapy_cfg/plugins/ (global) or
termapy_cfg/<config>/plugins/ (per-config) to make /ping available.

Demonstrates using serial_write, serial_wait_idle, and timing.
"""

import time

from termapy.plugins import Command, PluginContext


def _handler(ctx: PluginContext, args: str):
    cmd = args.strip() or "AT"
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return
    start = time.perf_counter()
    ctx.serial_write((cmd + "\r\n").encode())
    ctx.serial_wait_idle()
    ms = (time.perf_counter() - start) * 1000
    ctx.write(f"{cmd} — {ms:.0f}ms", "green")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send a command and measure response time (default: AT).",
    name="ping",
    args="{cmd}",
    handler=_handler,
)
