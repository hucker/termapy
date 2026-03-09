"""Built-in plugin: toggle REPL command echo."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "echo"
ARGS = "{on | off}"
HELP = "Toggle REPL command echo, or set on/off. Output is not affected."


def handler(ctx: PluginContext, args: str) -> None:
    """Toggle or set REPL command echo on/off.

    When echo is on, ``!`` commands are printed to the terminal
    output before execution. This is an in-memory toggle that does
    not persist to the config file.

    Args:
        ctx: Plugin context for engine API and output.
        args: ``"on"``, ``"off"``, or empty string to toggle.
    """
    arg = args.strip().lower()
    if arg == "on":
        ctx.engine.set_echo(True)
    elif arg == "off":
        ctx.engine.set_echo(False)
    else:
        ctx.engine.set_echo(not ctx.engine.get_echo())
    state = "on" if ctx.engine.get_echo() else "off"
    ctx.write(f"REPL echo {state}.", "green")
