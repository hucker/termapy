"""Built-in plugin: toggle verbose status output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command
from termapy.scripting import parse_bool

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _set_verbose(ctx: PluginContext, args: str) -> str:
    """Apply verbose setting from args. Returns the new state string.

    Empty or unrecognized input leaves the current value unchanged --
    this is what makes ``/verbose`` alone act as a "show current state"
    query. Callers that want strict parsing should validate first.
    """
    flags = ctx.ns("flags")
    val = parse_bool(args)
    if val is not None:
        flags["verbose"] = val
    return "on" if flags["verbose"] else "off"


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Show or toggle verbose status output."""
    state = _set_verbose(ctx, args)
    ctx.result(state)
    return CmdResult.ok(value=state)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Show or toggle verbose status output. Use {prefix}verbose.quiet to set silently.",
    name="verbose",
    args="{on|off}",
    handler=_handler,
)
