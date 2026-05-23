"""Built-in plugin: show file contents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _show_file(ctx: PluginContext, path: Path) -> None:
    """Read a file and print its contents line by line.

    Args:
        ctx: Plugin context for output.
        path: Absolute or relative path to the file.
    """
    if not path.exists():
        ctx.io.output(f"File not found: {path}", "red")
        return
    # OSError covers file-locked / permission-denied / disappeared
    # between the exists() check and here; UnicodeDecodeError means
    # the user pointed /show at a binary file.  Both are user-facing
    # conditions worth a friendly message.  Anything else is a bug.
    try:
        text = path.read_text(encoding="utf-8")
        ctx.io.output(f"--- {path} ---")
        for line in text.splitlines():
            ctx.io.output(line)
        ctx.io.output("--- end ---")
    except (OSError, UnicodeDecodeError) as e:
        ctx.io.output(f"Error reading {path}: {e}", "red")


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
    # Return the resolved path so scripts can confirm what was shown.
    return CmdResult.ok(value=Path(name))


def _handler_cfg(ctx: PluginContext, args: str) -> CmdResult:
    """Show the current config file contents.

    Args:
        ctx: Plugin context for config path and output.
        args: Unused.
    """
    if not ctx.config_path:
        return CmdResult.fail(msg="No config loaded.")
    _show_file(ctx, Path(ctx.config_path))
    return CmdResult.ok(value=Path(ctx.config_path))


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
    needs=CapabilitySet(gui_apps=True),
    sub_commands={
        "cfg": Command(
            help="Show the current config file.",
            handler=_handler_cfg,
            needs=CapabilitySet(gui_apps=True),
        ),
    },
)
