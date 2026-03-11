"""Built-in plugin: list commands or show help for a specific command."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_LABEL_RE = re.compile(r"^(\s*)(\w+)(:)(.*)")
_TITLE_FMT = "  [bold]{text}[/]"
_LABEL_FMT = "  {indent}[bold]{label}[/]{rest}"


def _write_docstring(ctx: PluginContext, docstring: str) -> None:
    """Format and write a Google-style docstring with markup.

    Detects a summary line (first non-blank line followed by a blank line)
    and renders it bold. Lines matching the Google-style ``word:`` pattern
    (e.g. ``Args:``, ``ctx: Plugin context``) have the label portion
    rendered bold while the rest of the line stays normal.

    Args:
        ctx: Plugin context for output.
        docstring: Raw docstring string from the handler function.
    """
    lines = inspect.cleandoc(docstring).splitlines()
    # Detect "summary line + blank line" pattern
    has_summary = len(lines) >= 2 and lines[1].strip() == ""
    for i, line in enumerate(lines):
        text = line.rstrip()
        if i == 0 and has_summary:
            ctx.write_markup(_TITLE_FMT.format(text=text))
        elif _LABEL_RE.match(text):
            m = _LABEL_RE.match(text)
            ctx.write_markup(_LABEL_FMT.format(
                indent=m.group(1),
                label=m.group(2) + m.group(3),
                rest=m.group(4).rstrip(),
            ))
        else:
            ctx.write(f"  {text}")


def _list_children(ctx: PluginContext, plugin, prefix: str,
                   cmd_w: int, arg_w: int, depth: int) -> None:
    """Recursively list a command's children with indentation.

    Args:
        ctx: Plugin context for output.
        plugin: PluginInfo for the parent command.
        prefix: REPL prefix string (e.g. "!").
        cmd_w: Column width for the command name.
        arg_w: Column width for the arguments.
        depth: Indentation depth (0 for top-level).
    """
    plugins = ctx.engine.plugins
    for child_name in plugin.children:
        child = plugins.get(child_name)
        if not child:
            continue
        indent = "  " * (depth + 1)
        cmd_col = f"{indent}{prefix}{child_name}".ljust(cmd_w + 2)
        arg_col = (child.args or "").ljust(arg_w)
        ctx.write(f"{cmd_col}{arg_col}  {child.help}")
        if child.children:
            _list_children(ctx, child, prefix, cmd_w, arg_w, depth + 1)


def _show_command_help(ctx: PluginContext, name: str,
                      dev_mode: bool = False) -> None:
    """Show help for a single command by name.

    Args:
        ctx: Plugin context for engine plugin registry and output.
        name: Dotted command name to look up.
        dev_mode: If True, show handler docstring instead of long_help.
    """
    prefix = ctx.engine.prefix
    plugin = ctx.engine.plugins.get(name)
    if not plugin:
        ctx.write(f"Unknown command: {name}", "red")
        return
    arg_str = f" {plugin.args}" if plugin.args else ""
    ctx.write(f"{prefix}{name}{arg_str} — {plugin.help}")
    if dev_mode:
        docstring = getattr(plugin.handler, "__doc__", None)
        if docstring:
            ctx.write("  ── developer docstring ──", "dim")
            _write_docstring(ctx, docstring)
        else:
            ctx.write("  (no docstring)", "dim")
    elif plugin.long_help:
        for line in plugin.long_help.strip().splitlines():
            ctx.write(f"  {line}")
    # Show subcommands if any
    if plugin.children:
        ctx.write("  Subcommands:")
        plugins = ctx.engine.plugins
        for child_name in plugin.children:
            child = plugins.get(child_name)
            if child:
                arg_str = f" {child.args}" if child.args else ""
                suffix = "  ..." if child.children else ""
                ctx.write(
                    f"    {prefix}{child_name}{arg_str}"
                    f" — {child.help}{suffix}"
                )
    if plugin.source not in ("built-in", "app"):
        ctx.write(f"  (source: {plugin.source})", "dim")


def _handler(ctx: PluginContext, args: str) -> None:
    """List all REPL commands or show detailed help for one.

    With no arguments, lists all registered commands grouped by source
    (built-in, global, per-config) with aligned columns and indented
    subcommands. With a command name (dotted for subcommands), shows
    that command's usage, help text, and subcommand list.

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Optional command name to get help for.
    """
    name = args.strip().lower() if isinstance(args, str) else ""
    prefix = ctx.engine.prefix
    if name:
        _show_command_help(ctx, name)
        return
    else:
        # Group top-level commands by source
        all_plugins = ctx.engine.plugins
        groups: dict[str, list] = {}
        for cmd_name, plugin in all_plugins.items():
            # Only show top-level commands (no dots = root level)
            if "." not in cmd_name:
                groups.setdefault(plugin.source, []).append(
                    (cmd_name, plugin)
                )

        # Measure column widths across ALL plugins (including children)
        all_infos = list(all_plugins.values())
        cmd_w = max(
            (len(prefix) + len(p.name) + 2 * p.name.count(".")
             for p in all_infos),
            default=10,
        ) + 2
        arg_w = max(
            (len(p.args) for p in all_infos if p.args), default=0
        ) + 2

        for source, plugins_list in groups.items():
            ctx.write(f"── {source} ──")
            for cmd_name, plugin in plugins_list:
                cmd_col = f"  {prefix}{cmd_name}".ljust(cmd_w + 2)
                arg_col = (plugin.args or "").ljust(arg_w)
                ctx.write(f"{cmd_col}{arg_col}  {plugin.help}")
                if plugin.children:
                    _list_children(
                        ctx, plugin, prefix, cmd_w, arg_w, depth=1
                    )


def _handler_dev(ctx: PluginContext, args: str) -> None:
    """Show a command handler's Python docstring (developer info).

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Command name to inspect.
    """
    name = args.strip().lower() if isinstance(args, str) else ""
    if not name:
        ctx.write("Usage: !help.dev <cmd>", "red")
        return
    _show_command_help(ctx, name, dev_mode=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="help",
    args="{cmd}",
    help="List REPL commands, or show help for one command.",
    long_help="""\
Three modes:
  !help              — list all commands with subcommands
  !help <cmd>        — show usage, help text, and subcommands
  !help proto.crc    — show help for a subcommand (dot notation)
  !help.dev <cmd>    — show the handler's Python docstring (developer info)""",
    handler=_handler,
    sub_commands={
        "dev": Command(
            args="<cmd>",
            help="Show a command handler's Python docstring.",
            handler=_handler_dev,
        ),
    },
)
