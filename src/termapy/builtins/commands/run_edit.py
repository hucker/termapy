"""Built-in plugin: open .run scripts in the system editor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.help_dynamic import folder_line
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    name = args.strip()
    if not name:
        raise UsageError()
    scripts_dir = ctx.fs.scripts_dir
    scripts_dir.mkdir(parents=True, exist_ok=True)
    if not name.endswith(".run"):
        name += ".run"
    path = scripts_dir / name
    ctx.fs.open_file(path)
    return CmdResult.ok(value=path)


def _run_long_help(ctx: PluginContext) -> str:
    """Green one-liner showing the count of .run scripts."""
    return folder_line(ctx, "run", noun="script")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="run.edit",
    args="<filename>",
    help="Open a .run script in the system editor.",
    long_help=_run_long_help,
    handler=_handler,
    needs=CapabilitySet(gui_apps=True),
)
