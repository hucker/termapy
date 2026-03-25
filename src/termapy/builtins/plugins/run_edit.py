"""Built-in plugin: open .run scripts in the system editor."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> None:
    name = args.strip()
    if not name:
        ctx.write("Usage: /run.edit <filename>", "red")
        return
    scripts_dir = ctx.scripts_dir
    scripts_dir.mkdir(parents=True, exist_ok=True)
    if not name.endswith(".run"):
        name += ".run"
    ctx.open_file(scripts_dir / name)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="run.edit",
    args="<filename>",
    help="Open a .run script in the system editor.",
    handler=_handler,
)
