"""Example plugin: print the current timestamp.

Drop this file into termapy_cfg/plugin/ (global) or
termapy_cfg/<config>/plugin/ (per-config) to make /ts available.

Demonstrates writing output and returning it as a scriptable value.
"""

from datetime import datetime

from termapy.plugins import CmdResult, Command, PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ctx.io.output(stamp, "green")
    return CmdResult.ok(value=stamp)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ts",
    help="Print the current date and time.",
    handler=_handler,
)
