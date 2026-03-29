"""Built-in plugin: exit the application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Exit the application.

    Args:
        ctx: Plugin context with exit_app callback.
        args: Ignored.
    """
    ctx.exit_app()
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="exit",
    help="Exit termapy.",
    handler=_handler,
)
