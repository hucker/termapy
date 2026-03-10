"""Built-in plugin: show the screenshot directory path."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "ss_dir"
ARGS = ""
HELP = "Show the screenshot folder path."


def handler(ctx: PluginContext, args: str) -> None:
    """Print the resolved screenshot directory path.

    Args:
        ctx: Plugin context with ss_dir and write.
        args: Ignored.
    """
    ctx.write(f"Screenshot dir: {ctx.ss_dir.resolve()}")
