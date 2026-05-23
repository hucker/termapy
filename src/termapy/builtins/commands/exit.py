"""Built-in plugin: exit the application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Exit the application.

    Args:
        ctx: Plugin context with exit_app callback.
        args: Ignored.
    """
    ctx.ui.exit_app()
    # SPECIAL CASE: the process is about to terminate -- no caller
    # ever reads this value.  Empty string satisfies the required-value
    # contract while making the void intent visible.
    return CmdResult.ok(value="")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="exit",
    help="Exit termapy.",
    handler=_handler,
    needs=CapabilitySet(interactive=True),  # interactive shutdown only
)
