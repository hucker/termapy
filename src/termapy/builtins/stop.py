"""Built-in plugin: abort a running script."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "stop"
ARGS = ""
HELP = "Abort a running script."


def handler(ctx: PluginContext, args: str) -> None:
    """Abort a running script if one is executing."""
    if ctx.engine.in_script():
        ctx.engine.script_stop()
        ctx.write("Stopping script...")
    else:
        ctx.write("No script running.")
