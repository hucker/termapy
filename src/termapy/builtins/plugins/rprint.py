"""Built-in plugin: print Rich markup text to the terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "rprint"
ARGS = "<text>"
HELP = "Print Rich markup text to the terminal (e.g. [bold red]Warning![/])."


def handler(ctx: PluginContext, args: str) -> None:
    """Write Rich markup text to the terminal output.

    Unlike ``!print`` which outputs plain text, ``!rprint`` passes
    text through the Rich markup parser, enabling styled output
    with tags like ``[bold red]text[/]``.

    Args:
        ctx: Plugin context for output.
        args: Rich markup text to render.
    """
    ctx.write_markup(args)
