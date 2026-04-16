"""Built-in plugin: /line_no toggle (TUI render setting).

A placeholder declaration so the command is registered in every
environment and ``/help line_no`` works uniformly.  The TUI replaces
this handler via ``register_hook`` at startup; in CLI the capability
gate (``tui_mode``) fails dispatch with a clear message before the
placeholder ever runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_placeholder(ctx: PluginContext, args: str) -> CmdResult:
    """Never invoked: the TUI replaces this handler via register_hook.

    In non-TUI environments dispatch's capability gate fails before
    reaching the handler because ``tui_mode`` is not provided.
    """
    return CmdResult.fail(msg="line_no handler not installed")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="line_no",
    args="<on|off>",
    help="Toggle line numbers on or off.",
    handler=_handler_placeholder,
    needs=CapabilitySet(tui_mode=True),
)
