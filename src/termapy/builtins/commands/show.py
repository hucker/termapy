"""Built-in plugin: show file contents."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folders import file_suggestion
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _show_file(ctx: PluginContext, path: Path, typed: str) -> CmdResult:
    """Print a file's contents line by line, or fail saying why not (registry ``failure-is-failure``).

    Args:
        ctx: Plugin context for output.
        path: Absolute or relative path to the file.
        typed: The name as typed, for the did-you-mean.

    Returns:
        ``ok`` with the path as its value, or the failure.
    """
    if not path.exists():
        return CmdResult.fail(msg=f"File not found: {path}{file_suggestion(path.parent, typed)}")
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
        return CmdResult.fail(msg=f"Read error: {path}: {e}")
    return CmdResult.ok(value=path)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Display file contents in the terminal.

    Args:
        ctx: Plugin context for output.
        args: Filename to show.
    """
    name = args.strip()
    if not name:
        raise UsageError(f"For the active config use {ctx.prefix}show.cfg")
    # The value is the path, so scripts can confirm what was shown.
    return _show_file(ctx, Path(name), name)


def _handler_cfg(ctx: PluginContext, args: str) -> CmdResult:
    """Show the current config file contents.

    Args:
        ctx: Plugin context for config path and output.
        args: Unused.
    """
    return _show_file(ctx, Path(ctx.config_path), ctx.config_path)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="advanced",
    name="show",
    args="<name>",
    help="Show a file.",
    long_help="""\
Reads a file and prints its contents to the terminal.

Regular filenames are resolved relative to the working directory.

Examples:
  {prefix}show.cfg              - view current config
  {prefix}show my_script.run    - view a script file
  {prefix}show ../notes.txt     - relative path""",
    handler=_handler,
    shows=True,
    needs=CapabilitySet.GUI_APPS,
    sub_commands={
        "cfg": Command(
            help="Show the current config file.",
            handler=_handler_cfg,
            shows=True,
            needs=CapabilitySet.CONFIG_GUI_APPS,
        ),
    },
)
