"""Built-in plugin: exit the application."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Exit the application.

    Args:
        ctx: Plugin context with exit_app callback.
        args: Ignored.
    """
    ctx.ui.exit_app()
    # The session is ending, so no caller reads it: an explicit empty value.
    return CmdResult.ok(value="")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="terminal",
    name="exit",
    help="Exit termapy.",
    handler=_handler,
    needs=CapabilitySet.INTERACTIVE,  # interactive shutdown only
    safety="mutable",
)
