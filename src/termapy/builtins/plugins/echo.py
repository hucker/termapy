"""Built-in plugin: toggle REPL command echo."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.scripting import parse_bool

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle or set REPL command echo on/off.

    When echo is on, REPL commands are printed to the terminal
    output before execution. This is an in-memory toggle that does
    not persist to the config file. Empty args toggles the current
    state. Any recognized boolean token (on/off/1/0/true/false/yes/no)
    is accepted.

    Args:
        ctx: Plugin context for engine API and output.
        args: a boolean token, or empty string to toggle.
    """
    val = parse_bool(args)
    if val is None:
        ctx.engine.set_echo(not ctx.engine.get_echo())
    else:
        ctx.engine.set_echo(val)
    state = "on" if ctx.engine.get_echo() else "off"
    ctx.result(state)
    return CmdResult.ok(value=state)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="echo",
    args="{on | off}",
    help="Toggle REPL command echo. Use /echo.quiet to set silently.",
    handler=_handler,
)
