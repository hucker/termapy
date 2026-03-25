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

# Colors for help output
_CMD = "cyan"          # command names
_OPT = "green"         # {optional} args
_REQ = "yellow"        # <required> args
_SEP = "dim"           # section separators
_SRC = "dim"           # source labels

_OPT_RE = re.compile(r"(\{[^}]+\})")
_REQ_RE = re.compile(r"(<[^>]+>)")
_MARKUP_RE = re.compile(r"\[[^\]]*\]")

# Max visible length for args before truncation
_MAX_ARGS_LEN = 20


def _color_args(args: str) -> str:
    """Add Rich color markup to argument placeholders."""
    if not args:
        return ""
    result = args
    result = _OPT_RE.sub(rf"[{_OPT}]\1[/]", result)
    result = _REQ_RE.sub(rf"[{_REQ}]\1[/]", result)
    return result


def _visible_len(s: str) -> int:
    """Return the visible length of a string, ignoring Rich markup tags."""
    return len(_MARKUP_RE.sub("", s))


def _pad(s: str, width: int) -> str:
    """Pad a string with Rich markup to a visible width."""
    return s + " " * max(0, width - _visible_len(s))


def _truncate_args(args: str, prefix: str, name: str) -> str:
    """Truncate long args strings and add a help hint."""
    if not args or len(args) <= _MAX_ARGS_LEN:
        return args
    return f"[{_SEP}]... /help {name}[/]"


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
        prefix: REPL prefix string (e.g. "/").
        cmd_w: Column width for the command name.
        arg_w: Column width for the arguments.
        depth: Indentation depth (0 for top-level).
    """
    plugins = ctx.engine.plugins
    for child_name in sorted(plugin.children):
        child = plugins.get(child_name)
        if not child:
            continue
        indent = "  " * (depth + 1)
        cmd_col = _pad(f"{indent}[{_CMD}]{prefix}{child_name}[/]", cmd_w + 2)
        args_text = _truncate_args(child.args or "", prefix, child_name)
        arg_col = _pad(_color_args(args_text), arg_w)
        ctx.write_markup(f"{cmd_col} {arg_col}  {child.help}")
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
    arg_str = f" {_color_args(plugin.args)}" if plugin.args else ""
    ctx.write_markup(f"[{_CMD}]{prefix}{name}[/]{arg_str} - {plugin.help}")
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
        ctx.write_markup(f"  [{_SEP}]Subcommands:[/]")
        plugins = ctx.engine.plugins
        for child_name in sorted(plugin.children):
            child = plugins.get(child_name)
            if child:
                arg_str = f" {_color_args(child.args)}" if child.args else ""
                suffix = f"  [{_SEP}]...[/]" if child.children else ""
                ctx.write_markup(
                    f"    [{_CMD}]{prefix}{child_name}[/]{arg_str}"
                    f" - {child.help}{suffix}"
                )
    if plugin.source not in ("built-in", "app"):
        ctx.write_markup(f"  [{_SRC}](source: {plugin.source})[/]")


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
        # Group top-level commands by source, with display labels and order
        _SOURCE_ORDER = {"app": 0, "built-in": 1, "global": 2}
        _SOURCE_LABELS = {
            "app": "Application",
            "built-in": "Application Plugins",
            "global": "User Plugins",
        }

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
        arg_w = min(
            max(
                (len(p.args) for p in all_infos if p.args), default=0
            ) + 2,
            _MAX_ARGS_LEN + 2,
        )

        sorted_sources = sorted(
            groups, key=lambda s: _SOURCE_ORDER.get(s, 3)
        )
        for source in sorted_sources:
            label = _SOURCE_LABELS.get(source, f"{source} Plugins")
            ctx.write_markup(f"[{_SEP}]── {label} ──[/]")
            for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
                cmd_col = _pad(f"  [{_CMD}]{prefix}{cmd_name}[/]", cmd_w + 2)
                args_text = _truncate_args(plugin.args or "", prefix, cmd_name)
                arg_col = _pad(_color_args(args_text), arg_w)
                ctx.write_markup(f"{cmd_col} {arg_col}  {plugin.help}")
                if plugin.children:
                    _list_children(
                        ctx, plugin, prefix, cmd_w, arg_w, depth=1
                    )

        # Directives section
        directives = ctx.engine.directives
        if directives:
            ctx.write_markup(f"[{_SEP}]── Directives ──[/]")
            for d in directives:
                pattern = _color_args(d.pattern) if d.pattern else ""
                ctx.write_markup(f"  [{_CMD}]{pattern}[/]  {d.help}")


def _handler_dev(ctx: PluginContext, args: str) -> None:
    """Show a command handler's Python docstring (developer info).

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Command name to inspect.
    """
    name = args.strip().lower() if isinstance(args, str) else ""
    if not name:
        ctx.write("Usage: /help.dev <cmd>", "red")
        return
    _show_command_help(ctx, name, dev_mode=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="help",
    args="{cmd}",
    help="List REPL commands, or show help for one command.",
    long_help="""\
Three modes:
  /help              - list all commands with subcommands
  /help <cmd>        - show usage, help text, and subcommands
  /help proto.crc    - show help for a subcommand (dot notation)""",
    handler=_handler,
    sub_commands={
        "dev": Command(
            args="<cmd>",
            help="Show a command handler's Python docstring.",
            handler=_handler_dev,
        ),
    },
)
