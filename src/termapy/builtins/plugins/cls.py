"""Built-in plugin: clear the terminal screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the terminal output and reset the line counter.

    Args:
        ctx: Plugin context with clear_screen callback.
        args: Ignored.
    """
    ctx.clear_screen()
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cls",
    help="Clear the terminal screen.",
    handler=_handler,
)
