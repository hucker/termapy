"""Built-in plugin: print plain or Rich markup text to the terminal."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> None:
    """Write a message to the terminal output.

    Prints the argument text directly to the terminal. Useful in
    scripts for status messages or visual separators.

    Args:
        ctx: Plugin context for output.
        args: Text to print.
    """
    ctx.write(args)


def _handler_rich(ctx: PluginContext, args: str) -> None:
    """Write Rich markup text to the terminal output.

    Unlike ``!print`` which outputs plain text, ``!print.r`` passes
    text through the Rich markup parser, enabling styled output
    with tags like ``[bold red]text[/]``.

    Args:
        ctx: Plugin context for output.
        args: Rich markup text to render.
    """
    ctx.write_markup(args)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="print",
    args="<text>",
    help="Print a message to the terminal.",
    handler=_handler,
    sub_commands={
        "r": Command(
            args="<text>",
            help="Print Rich markup text (e.g. [bold red]Warning![/]).",
            handler=_handler_rich,
        ),
    },
)
