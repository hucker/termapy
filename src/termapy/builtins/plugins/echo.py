"""Built-in plugin: toggle REPL command echo."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle or set REPL command echo on/off.

    When echo is on, REPL commands are printed to the terminal
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
    ctx.result(state)
    return CmdResult.ok(value=state)


def _handler_quiet(ctx: PluginContext, args: str) -> CmdResult:
    """Set REPL echo on/off silently (no output).

    Useful in on_connect_cmd and scripts where you want to
    suppress echo without printing a status message.

    Args:
        ctx: Plugin context for engine API.
        args: ``"on"`` or ``"off"``.
    """
    arg = args.strip().lower()
    if arg == "on":
        ctx.engine.set_echo(True)
    elif arg == "off":
        ctx.engine.set_echo(False)
    else:
        return CmdResult.fail(msg="Usage: /echo.quiet <on|off>")
    state = "on" if ctx.engine.get_echo() else "off"
    return CmdResult.ok(value=state)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="echo",
    args="{on | off}",
    help="Toggle REPL command echo, or set on/off. Output is not affected.",
    handler=_handler,
    sub_commands={
        "quiet": Command(
            args="<on | off>",
            help="Set echo on/off silently (no output message).",
            handler=_handler_quiet,
        ),
    },
)
