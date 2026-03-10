"""Built-in plugin: exit the application."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "exit"
ARGS = ""
HELP = "Exit termapy."


def handler(ctx: PluginContext, args: str) -> None:
    """Exit the application.

    Args:
        ctx: Plugin context with exit_app callback.
        args: Ignored.
    """
    ctx.exit_app()
