"""Built-in plugin: show a Yes/Cancel confirmation dialog."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Pause execution and show a Yes/Cancel confirmation dialog.

    If the user clicks Yes, execution continues. If Cancel, the
    running script is stopped. Must be called from a background
    thread (scripts always are).

    Args:
        ctx: Plugin context for confirmation dialog.
        args: Message text to display in the dialog.
    """
    message = args.strip() or "Continue?"
    accepted = ctx.ui.confirm(message)
    if not accepted:
        ctx.io.result("Canceled.", "yellow")
        ctx.internal.script_stop()
    # The user's choice, a bool (registry value-typed), so a script can
    # capture it via ``$(OK) <- /confirm Continue?`` (as on/off) even if it
    # does not stop on cancel.
    return CmdResult.ok(value=accepted)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="confirm",
    args="{message}",
    help="Show Yes/Cancel dialog; Cancel stops a running script.",
    handler=_handler,
    needs=CapabilitySet(block_until=True, confirm_dialog=True),
)
