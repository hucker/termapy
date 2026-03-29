"""Built-in plugin: screenshot commands (ss.dir, ss.svg, ss.txt)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command
from termapy.scripting import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_dir(ctx: PluginContext, args: str) -> CmdResult:
    """Print the resolved screenshot directory path.

    Args:
        ctx: Plugin context with ss_dir and write.
        args: Ignored.
    """
    ctx.write(f"Screenshot dir: {ctx.ss_dir.resolve()}")
    return CmdResult.ok()


def _handler_not_supported(ctx: PluginContext, args: str) -> CmdResult:
    ctx.write("Screenshots are not supported in CLI mode.", "yellow")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ss",
    help="Screenshot tools: save SVG/text, show folder.",
    sub_commands={
        "dir": Command(
            help="Show the screenshot folder path.",
            handler=_handler_dir,
        ),
        "svg": Command(
            args="{name}",
            help="Save SVG screenshot (TUI only).",
            handler=_handler_not_supported,
        ),
        "txt": Command(
            args="{name}",
            help="Save text screenshot (TUI only).",
            handler=_handler_not_supported,
        ),
    },
)
