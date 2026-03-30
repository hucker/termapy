"""Built-in plugin: show file contents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _show_file(ctx: PluginContext, path: Path) -> None:
    """Read a file and print its contents line by line.

    Args:
        ctx: Plugin context for output.
        path: Absolute or relative path to the file.
    """
    if not path.exists():
        ctx.write(f"File not found: {path}", "red")
        return
    try:
        text = path.read_text(encoding="utf-8")
        ctx.write(f"--- {path} ---")
        for line in text.splitlines():
            ctx.write(line)
        ctx.write("--- end ---")
    except Exception as e:
        ctx.write(f"Error reading {path}: {e}", "red")


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Display file contents in the terminal.

    Args:
        ctx: Plugin context for output.
        args: Filename to show.
    """
    name = args.strip()
    if not name:
        return CmdResult.fail(msg="Usage: /show <name>  (or /show.cfg for config)")
    _show_file(ctx, Path(name))
    return CmdResult.ok()


def _handler_cfg(ctx: PluginContext, args: str) -> CmdResult:
    """Show the current config file contents.

    Args:
        ctx: Plugin context for config path and output.
        args: Unused.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config file loaded.")
    _show_file(ctx, Path(ctx.config_path))
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="show",
    args="<name>",
    help="Show a file.",
    long_help="""\
Reads a file and prints its contents to the terminal.

Regular filenames are resolved relative to the working directory.

Examples:
  /show.cfg              - view current config
  /show my_script.run    - view a script file
  /show ../notes.txt     - relative path""",
    handler=_handler,
    sub_commands={
        "cfg": Command(
            help="Show the current config file.",
            handler=_handler_cfg,
        ),
    },
)
