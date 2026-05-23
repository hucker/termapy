"""Built-in plugin: clear the terminal screen."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Clear the terminal output and reset the line counter.

    Args:
        ctx: Plugin context with clear_screen callback.
        args: Ignored.
    """
    ctx.io.clear_screen()
    # SPECIAL CASE: clearing the screen produces no scriptable value.
    # Empty string is the explicit "nothing to capture" signal; the
    # required-value contract surfaces this as a deliberate choice.
    return CmdResult.ok(value="")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="cls",
    help="Clear the terminal screen.",
    handler=_handler,
    needs=CapabilitySet(interactive=True),  # no screen to clear in MCP
)
