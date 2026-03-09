"""Built-in plugin: show a Yes/Cancel confirmation dialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "confirm"
ARGS = "{message}"
HELP = "Show Yes/Cancel dialog; Cancel stops a running script."


def handler(ctx: PluginContext, args: str) -> None:
    """Pause execution and show a Yes/Cancel confirmation dialog.

    If the user clicks Yes, execution continues. If Cancel, the
    running script is stopped. Must be called from a background
    thread (scripts always are).

    Args:
        ctx: Plugin context for confirmation dialog.
        args: Message text to display in the dialog.
    """
    message = args.strip() or "Continue?"
    if not ctx.confirm(message):
        ctx.write("Cancelled.", "yellow")
        ctx.engine.script_stop()
