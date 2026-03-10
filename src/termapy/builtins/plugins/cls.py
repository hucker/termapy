"""Built-in plugin: clear the terminal screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "cls"
ARGS = ""
HELP = "Clear the terminal screen."


def handler(ctx: PluginContext, args: str) -> None:
    """Clear the terminal output and reset the line counter.

    Args:
        ctx: Plugin context with clear_screen callback.
        args: Ignored.
    """
    ctx.clear_screen()
