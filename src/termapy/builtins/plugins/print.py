"""Built-in plugin: print a message to the terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "print"
ARGS = "<text>"
HELP = "Print a message to the terminal."


def handler(ctx: PluginContext, args: str) -> None:
    """Write a message to the terminal output.

    Prints the argument text directly to the terminal. Useful in
    scripts for status messages or visual separators.

    Args:
        ctx: Plugin context for output.
        args: Text to print.
    """
    ctx.write(args)
