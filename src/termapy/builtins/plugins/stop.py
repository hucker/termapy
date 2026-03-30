"""Built-in plugin: abort a running script."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Abort a running script if one is executing.

    Signals the script runner's stop event, which is checked between
    each line of the script. The script will stop at the next line
    boundary. Has no effect if no script is running.

    Args:
        ctx: Plugin context for engine state and output.
        args: Ignored (no arguments accepted).
    """
    if ctx.engine.in_script():
        ctx.engine.script_stop()
        ctx.write("Stopping script...")
    else:
        ctx.write("No script running.")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="stop",
    help="Abort a running script.",
    handler=_handler,
)
