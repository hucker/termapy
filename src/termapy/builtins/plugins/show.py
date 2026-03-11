"""Built-in plugin: show file contents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_SHOW_SPECIAL = {"$cfg"}


def _handler(ctx: PluginContext, args: str) -> None:
    """Display file contents in the terminal.

    Reads a file and prints each line to the terminal output.
    Supports special names: ``$cfg`` shows the current config file.
    Regular filenames are resolved relative to the working directory.

    Args:
        ctx: Plugin context for config path and output.
        args: Filename or special name (e.g. ``"$cfg"``).
    """
    name = args.strip()
    if not name:
        ctx.write("Usage: !show <name>  ($cfg for config, or a filename)", "red")
        return
    key = name.lower()
    if key == "$cfg":
        if not ctx.config_path:
            ctx.write("No config file loaded.", "red")
            return
        path = Path(ctx.config_path)
    elif name.startswith("$"):
        known = ", ".join(sorted(_SHOW_SPECIAL))
        ctx.write(f"Unknown special name: {name}. Known: {known}", "red")
        return
    else:
        path = Path(name)
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


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = {
    "name": "show",
    "args": "<name>",
    "help": "Show a file. $cfg for current config, or a filename.",
    "long_help": """\
Reads a file and prints its contents to the terminal.

Special names:
  $cfg  — show the current JSON config file

Regular filenames are resolved relative to the working directory.

Examples:
  !show $cfg             — view current config
  !show my_script.run    — view a script file
  !show ../notes.txt     — relative path""",
    "handler": _handler,
}
