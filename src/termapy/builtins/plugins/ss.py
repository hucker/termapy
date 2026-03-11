"""Built-in plugin: screenshot commands (ss.dir, ss.svg, ss.txt)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_dir(ctx: PluginContext, args: str) -> None:
    """Print the resolved screenshot directory path.

    Args:
        ctx: Plugin context with ss_dir and write.
        args: Ignored.
    """
    ctx.write(f"Screenshot dir: {ctx.ss_dir.resolve()}")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = {
    "name": "ss",
    "help": "Screenshot tools: save SVG/text, show folder.",
    "sub_commands": {
        "dir": {
            "help": "Show the screenshot folder path.",
            "handler": _handler_dir,
        },
    },
}
