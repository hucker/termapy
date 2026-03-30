"""Built-in plugin: toggle end-of-line character visibility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Toggle or set EOL marker visibility.

    When enabled, dim ``\\r`` and ``\\n`` markers appear inline in
    serial output before the characters are consumed by line splitting.
    Sent commands also show the configured line ending. This setting
    is persisted to the config file via ``show_line_endings``.

    Note: markers use ANSI escape sequences and may interfere with
    device ANSI color output. Turn off when not actively debugging.

    Args:
        ctx: Plugin context for config access and output.
        args: ``"on"``, ``"off"``, or empty string to toggle.
    """
    arg = args.strip().lower()
    current = ctx.cfg.get("show_line_endings", False)
    if arg == "on":
        new = True
    elif arg == "off":
        new = False
    else:
        new = not current
    ctx.engine.apply_cfg("show_line_endings", new)
    state = "on" if new else "off"
    ctx.result(state)
    return CmdResult.ok(value=state)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="show_line_endings",
    args="{on | off}",
    help="Toggle visible \\r \\n markers in serial output for line-ending troubleshooting.",
    handler=_handler,
)
