"""Built-in plugin: toggle verbose status output."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    val = args.strip().lower()
    if val in ("on", "1", "true"):
        ctx.verbose = True
    elif val in ("off", "0", "false"):
        ctx.verbose = False
    state = "on" if ctx.verbose else "off"
    ctx.result(state)
    return CmdResult.ok(value=state)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Show or toggle verbose status output.",
    name="verbose",
    args="{on|off}",
    handler=_handler,
)
