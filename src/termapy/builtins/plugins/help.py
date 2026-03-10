"""Built-in plugin: list commands or show help for a specific command."""

from __future__ import annotations

import inspect
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

_LABEL_RE = re.compile(r"^(\s*)(\w+)(:)(.*)")
_TITLE_FMT = "  [bold]{text}[/]"
_LABEL_FMT = "  {indent}[bold]{label}[/]{rest}"


NAME = "help"
ARGS = "{cmd} {--dev}"
HELP = "List REPL commands, or show help for one command."
LONG_HELP = """\
Three modes:
  !help            — list all commands grouped by source
  !help <cmd>      — show usage, help text, and extended help
  !help --dev <cmd> — show the handler's Python docstring (developer info)"""


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


def handler(ctx: PluginContext, args: str) -> None:
    """List all REPL commands or show detailed help for one.

    With no arguments, lists all registered commands grouped by source
    (built-in, global, per-config) with aligned columns. With a command
    name, shows that command's usage and help text.

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Optional command name to get help for.
    """
    raw = args.strip() if isinstance(args, str) else ""
    parts = raw.split()
    dev_mode = "--dev" in parts
    if dev_mode:
        parts.remove("--dev")
    name = parts[0].lower() if parts else ""
    prefix = ctx.engine.prefix
    if name:
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
        if plugin.source != "built-in":
            ctx.write(f"  (source: {plugin.source})", "dim")
    else:
        groups = {}
        for cmd_name, plugin in ctx.engine.plugins.items():
            groups.setdefault(plugin.source, []).append((cmd_name, plugin))

        # Measure columns: cmd width and args width across ALL plugins
        all_plugins = list(ctx.engine.plugins.values())
        cmd_w = max(len(prefix) + len(p.name) for p in all_plugins) + 2
        arg_w = max((len(p.args) for p in all_plugins if p.args), default=0) + 2

        for source, plugins in groups.items():
            ctx.write(f"── {source} ──")
            for cmd_name, plugin in plugins:
                cmd_col = f"  {prefix}{cmd_name}".ljust(cmd_w + 2)
                arg_col = (plugin.args or "").ljust(arg_w)
                ctx.write(f"{cmd_col}{arg_col}  {plugin.help}")
