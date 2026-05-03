"""Built-in plugin: screenshot commands (ss.svg, ss.txt + folder ops).

``ss.svg`` and ``ss.txt`` need a rendered surface, so they declare
``screen_capture`` and rely on dispatch's capability gate to produce
a clean error in environments that don't provide it.  The TUI
installs real handlers via ``register_hook`` that replace the
placeholders below.

The ``list / explore / show / clear`` folder operations come from
``folder_ops.build_folder_subcommands("ss")`` -- one uniform family
shared with every other top-level folder plugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.folder_ops import build_folder_subcommands
from termapy.help_dynamic import folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _ss_long_help(ctx: PluginContext) -> str:
    """Green one-liner showing the count of saved screenshots."""
    return folder_line(ctx, "ss", noun="screenshot")


def _handler_placeholder(ctx: PluginContext, args: str) -> CmdResult:
    """Never invoked: the TUI replaces this handler via register_hook.

    In non-TUI environments dispatch's capability gate fails before
    reaching the handler because ``screen_capture`` is not provided.
    """
    return CmdResult.fail(msg="screenshot handler not installed")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ss",
    help="Screenshot tools: save SVG/text, manage folder.",
    long_help=_ss_long_help,
    mcp_visible=False,  # screenshots; TUI-bound; subcommands inherit
    sub_commands={
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
        **build_folder_subcommands("ss"),
    },
)
