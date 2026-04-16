"""Built-in plugin: screenshot commands (ss.dir, ss.svg, ss.txt).

``ss.dir`` works anywhere (it just prints a path).  ``ss.svg`` and
``ss.txt`` need a rendered surface, so they declare ``screen_capture``
and rely on dispatch's capability gate to produce a clean error in
environments that don't provide it.  The TUI installs real handlers
via ``register_hook`` that replace the placeholders below.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.help_dynamic import folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _ss_long_help(ctx: PluginContext) -> str:
    """Green one-liner showing the count of saved screenshots."""
    return folder_line(ctx, "ss", noun="screenshot")


def _handler_dir(ctx: PluginContext, args: str) -> CmdResult:
    """Print the resolved screenshot directory path.

    Args:
        ctx: Plugin context with ss_dir and write.
        args: Ignored.
    """
    ctx.write(f"Screenshot dir: {ctx.ss_dir.resolve()}")
    return CmdResult.ok()


def _handler_placeholder(ctx: PluginContext, args: str) -> CmdResult:
    """Never invoked: the TUI replaces this handler via register_hook.

    In non-TUI environments dispatch's capability gate fails before
    reaching the handler because ``screen_capture`` is not provided.
    """
    return CmdResult.fail(msg="screenshot handler not installed")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ss",
    help="Screenshot tools: save SVG/text, show folder.",
    long_help=_ss_long_help,
    sub_commands={
        "dir": Command(
            help="Show the screenshot folder path.",
            long_help=_ss_long_help,
            handler=_handler_dir,
        ),
        "svg": Command(
            args="{name}",
            help="Save an SVG screenshot of the terminal.",
            long_help=_ss_long_help,
            handler=_handler_placeholder,
            needs=CapabilitySet(screen_capture=True),
        ),
        "txt": Command(
            args="{name}",
            help="Save a text screenshot of the terminal.",
            long_help=_ss_long_help,
            handler=_handler_placeholder,
            needs=CapabilitySet(screen_capture=True),
        ),
    },
)
