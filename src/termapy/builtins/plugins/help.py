"""Built-in plugin: list commands or show help for a specific command."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command

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
        elif (m := _LABEL_RE.match(text)):
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


# Fields searched by the /help fuzzy fallback, in priority order. Earlier
# fields rank higher when sorting matches for display. The same field set is
# what /help.search uses without --dev (docstring excluded).
_FUZZY_FIELDS = ("name", "help", "args", "long_help")


def _fuzzy_matches(needle: str, plugins: dict) -> list[str]:
    """Return command names whose name/help/args/long_help contain ``needle``.

    Case-insensitive substring match. Results sorted by highest-priority
    field hit first (name > short help > args > long_help), then
    alphabetically within a tier. A command only appears once even if it
    matches in multiple fields.
    """
    needle = needle.lower()
    ranked: list[tuple[int, str]] = []
    for name, plugin in plugins.items():
        best_tier = None
        for tier, field in enumerate(_FUZZY_FIELDS):
            if field == "name":
                text = name
            else:
                text = getattr(plugin, field, "") or ""
            if needle in text.lower():
                best_tier = tier
                break
        if best_tier is not None:
            ranked.append((best_tier, name))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [name for _, name in ranked]


def _show_did_you_mean(ctx: PluginContext, name: str, matches: list[str]) -> CmdResult:
    """Render a 'Did you mean:' list for multi-match substring lookups."""
    prefix = ctx.engine.prefix
    plugins = ctx.engine.plugins
    ctx.write_markup(
        f"No exact match for [{_CMD}]{prefix}{name}[/]. Did you mean:"
    )
    for match_name in matches:
        p = plugins[match_name]
        arg_str = f" {_color_args(p.args)}" if p.args else ""
        ctx.write_markup(
            f"  [{_CMD}]{prefix}{match_name}[/]{arg_str} - {p.help}"
        )
    return CmdResult.ok(value="\n".join(matches))


def _show_command_help(ctx: PluginContext, name: str,
                      dev_mode: bool = False) -> CmdResult:
    """Show help for a single command by name.

    Exact-match first. On miss, falls back to case-insensitive substring
    matching against registered command names:
      - 1 match  -> show it (same as if the user typed the full name)
      - 2+ match -> render a "Did you mean:" list
      - 0 match  -> fail

    Args:
        ctx: Plugin context for engine plugin registry and output.
        name: Dotted command name (or partial) to look up.
        dev_mode: If True, show handler docstring instead of long_help.
    """
    prefix = ctx.engine.prefix
    plugin = ctx.engine.plugins.get(name)
    if not plugin:
        # Check target commands (no prefix, help-only)
        tc = ctx.ns("target_commands").get(name)
        if tc:
            arg_str = f" {_color_args(tc.args)}" if tc.args else ""
            ctx.write_markup(
                f"[{_CMD}]{tc.name}[/]{arg_str} - {tc.help}"
            )
            ctx.write_markup(f"  [{_SRC}](source: target device)[/]")
            return CmdResult.ok()
        # Forgiving fallback: substring match across name/help/args/long_help.
        matches = _fuzzy_matches(name, ctx.engine.plugins)
        if len(matches) == 1:
            name = matches[0]
            plugin = ctx.engine.plugins[name]
            # fall through to the rendering block below
        elif matches:
            return _show_did_you_mean(ctx, name, matches)
        else:
            return CmdResult.fail(msg=f"Unknown command: {name}")
    arg_str = f" {_color_args(plugin.args)}" if plugin.args else ""
    ctx.write_markup(f"[{_CMD}]{prefix}{name}[/]{arg_str} - {plugin.help}")
    if dev_mode:
        docstring = getattr(plugin.handler, "__doc__", None)
        if docstring:
            ctx.output("  -- developer docstring --")
            _write_docstring(ctx, docstring)
        else:
            ctx.output("  (no docstring)")
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
    return CmdResult.ok()


def _handler(ctx: PluginContext, args: str) -> CmdResult:
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
        return _show_command_help(ctx, name)
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

        # Fixed column widths for consistent layout
        cmd_w = 25
        arg_w = 25

        sorted_sources = sorted(
            groups, key=lambda s: _SOURCE_ORDER.get(s, 3)
        )
        first = True
        for source in sorted_sources:
            label = _SOURCE_LABELS.get(source, f"{source} Plugins")
            if not first:
                ctx.write_markup("")
            first = False
            ctx.write_markup(f"[{_SEP}]-- {label} --[/]")
            for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
                cmd_col = _pad(f"  [{_CMD}]{prefix}{cmd_name}[/]", cmd_w + 2)
                args_text = _truncate_args(plugin.args or "", prefix, cmd_name)
                arg_col = _pad(_color_args(args_text), arg_w)
                sub_count = f"  [dim]({len(plugin.children)})[/]" if plugin.children else ""
                ctx.write_markup(f"{cmd_col} {arg_col}  {plugin.help}{sub_count}")

        # Directives section
        directives = ctx.engine.directives
        if directives:
            ctx.write_markup("")
            ctx.write_markup(f"[{_SEP}]-- Directives --[/]")
            for d in directives:
                cmd_col = _pad(f"  [{_CMD}]{d.name}[/]", cmd_w + 2)
                arg_col = _pad(_color_args(d.pattern) if d.pattern else "", arg_w)
                ctx.write_markup(f"{cmd_col} {arg_col}  {d.help}")

        # Script-only blocking commands
        ctx.write_markup("")
        ctx.write_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
        for name, args, desc in (
            ("expect",       "match=<text> {timeout=<dur>}",  "Wait for text in serial output."),
            ("expect.regex", "match=<pattern> {timeout=<dur>}", "Wait for regex match in serial output."),
            ("confirm",      "{message}",                     "Show yes/no dialog, stop script on no."),
        ):
            cmd_col = _pad(f"  [{_CMD}]{prefix}{name}[/]", cmd_w + 2)
            arg_col = _pad(_color_args(args), arg_w)
            ctx.write_markup(f"{cmd_col} {arg_col}  {desc}")

        # Scripts section
        scripts_dir = ctx.scripts_dir
        if scripts_dir.is_dir():
            scripts = sorted(scripts_dir.glob("*.run"))
            if scripts:
                ctx.write_markup("")
                _render_scripts(ctx, scripts, prefix, cmd_w)

        # Target device commands section
        if ctx.ns("target_commands"):
            ctx.write_markup("")
            _render_target(ctx)
    return CmdResult.ok()


def _handler_target(ctx: PluginContext, args: str) -> CmdResult:
    """Show only imported target device commands.

    Args:
        ctx: Plugin context for target command namespace and output.
        args: Unused.
    """
    target_cmds = ctx.ns("target_commands")
    if not target_cmds:
        ctx.result("No target commands included. Use /include first.")
        return CmdResult.ok()
    _render_target(ctx)
    ctx.result(f"{len(target_cmds)} device commands.")
    return CmdResult.ok()


def _render_target(ctx: PluginContext) -> None:
    """Render the target device command table."""
    cmd_w = 25
    arg_w = 25
    ctx.write_markup(f"[{_SEP}]-- Target Device --[/]")
    target_cmds = ctx.ns("target_commands")
    for cmd_name in sorted(target_cmds):
        tc = target_cmds[cmd_name]
        cmd_col = _pad(f"  [{_CMD}]{tc.name}[/]", cmd_w + 2)
        arg_col = _pad(
            _color_args(tc.args), arg_w
        ) if tc.args else _pad("", arg_w)
        ctx.write_markup(f"{cmd_col} {arg_col}  {tc.help}")


def _script_description(path: Path) -> str:
    """Extract description from a script's header comment.

    Valid format: first line is ``# text``, second line is blank.
    """
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
            second = f.readline()
        if not first.strip().startswith("#"):
            return ""
        if second.strip():
            return ""
        text = first.strip().lstrip("#").strip()
        if " -- " in text:
            text = text.split(" -- ", 1)[1]
        return text
    except OSError:
        return ""


def _render_scripts(ctx: PluginContext, scripts: list, prefix: str, cmd_w: int = 25, arg_w: int = 25) -> None:
    """Render the scripts table."""
    ctx.write_markup(f"[{_SEP}]-- Scripts --[/]")
    for path in scripts:
        name = path.stem
        desc = _script_description(path)
        cmd_col = _pad(f"  [{_CMD}]{prefix}run {name}[/]", cmd_w + 2)
        arg_col = _pad("", arg_w)
        ctx.write_markup(f"{cmd_col} {arg_col}  {desc}")


def _handler_run(ctx: PluginContext, args: str) -> CmdResult:
    """List available .run scripts with descriptions.

    Args:
        ctx: Plugin context.
        args: Unused.
    """
    scripts_dir = ctx.scripts_dir
    if not scripts_dir.is_dir():
        ctx.result("No scripts directory.")
        return CmdResult.ok()
    scripts = sorted(scripts_dir.glob("*.run"))
    if not scripts:
        ctx.result("No .run scripts found.")
        return CmdResult.ok()
    prefix = ctx.engine.prefix
    _render_scripts(ctx, scripts, prefix)
    ctx.result(f"{len(scripts)} scripts.")
    return CmdResult.ok()


def _handler_plugin(ctx: PluginContext, args: str) -> CmdResult:
    """List loaded plugins grouped by source.

    Args:
        ctx: Plugin context.
        args: Unused.
    """
    all_plugins = ctx.engine.plugins
    if not all_plugins:
        ctx.result("No plugins loaded.")
        return CmdResult.ok()
    _SOURCE_ORDER = {"app": 0, "built-in": 1, "global": 2}
    _SOURCE_LABELS = {
        "app": "Application",
        "built-in": "Application Plugins",
        "global": "User Plugins",
    }
    groups: dict[str, list] = {}
    for cmd_name, plugin in all_plugins.items():
        if "." not in cmd_name:
            groups.setdefault(plugin.source, []).append((cmd_name, plugin))
    cmd_w = 25
    arg_w = 25
    prefix = ctx.engine.prefix
    sorted_sources = sorted(groups, key=lambda s: _SOURCE_ORDER.get(s, 3))
    first = True
    total = 0
    for source in sorted_sources:
        label = _SOURCE_LABELS.get(source, f"{source} Plugins")
        if not first:
            ctx.write_markup("")
        first = False
        ctx.write_markup(f"[{_SEP}]-- {label} --[/]")
        for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
            cmd_col = _pad(f"  [{_CMD}]{prefix}{cmd_name}[/]", cmd_w + 2)
            args_text = _truncate_args(plugin.args or "", prefix, cmd_name)
            arg_col = _pad(_color_args(args_text), arg_w)
            sub_count = f"  [dim]({len(plugin.children)})[/]" if plugin.children else ""
            ctx.write_markup(f"{cmd_col} {arg_col}  {plugin.help}{sub_count}")
            total += 1
    ctx.result(f"{total} commands.")
    return CmdResult.ok()


def _handler_dev(ctx: PluginContext, args: str) -> CmdResult:
    """Show a command handler's Python docstring (developer info).

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Command name to inspect.
    """
    name = args.strip().lower() if isinstance(args, str) else ""
    if not name:
        return CmdResult.fail(msg="Usage: /help.dev <cmd>")
    return _show_command_help(ctx, name, dev_mode=True)


# Regex metacharacters that signal "user meant a pattern, not a literal".
_REGEX_META_RE = re.compile(r"[.^$*+?()\[\]{}|\\]")

# Max matches to render before truncating.
_MAX_SEARCH_RESULTS = 50

# Characters of context to show around a match in long_help / docstrings.
_SEARCH_CONTEXT = 40


def _highlight(text: str, span: tuple[int, int]) -> str:
    """Wrap the matched span in yellow markup, trimming surrounding context."""
    start, end = span
    left = max(0, start - _SEARCH_CONTEXT)
    right = min(len(text), end + _SEARCH_CONTEXT)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    # Replace newlines/tabs with spaces so output stays one line.
    before = text[left:start].replace("\n", " ").replace("\t", " ")
    hit = text[start:end].replace("\n", " ").replace("\t", " ")
    after = text[end:right].replace("\n", " ").replace("\t", " ")
    return f"{prefix}{before}[yellow]{hit}[/]{after}{suffix}"


def _search_fields(name: str, plugin, include_dev: bool) -> list[tuple[str, str]]:
    """Return (field_label, field_text) pairs to search for one plugin."""
    fields = [
        ("name", name),
        ("help", plugin.help or ""),
        ("args", plugin.args or ""),
        ("long_help", plugin.long_help or ""),
    ]
    if include_dev:
        docstring = getattr(plugin.handler, "__doc__", None) or ""
        fields.append(("docstring", docstring))
    return fields


def _handler_search(ctx: PluginContext, args: str) -> CmdResult:
    """Search command names and help text for a regex or literal string.

    Searches across every registered command's name, short help, args, and
    long_help. Pass ``--dev`` to also search handler docstrings. If the
    pattern has no regex metacharacters, it is treated as a case-insensitive
    literal. Otherwise it is compiled as a case-insensitive regex.

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Pattern to search for, optionally prefixed with ``--dev``.
    """
    tokens = args.split() if isinstance(args, str) else []
    include_dev = "--dev" in tokens
    tokens = [t for t in tokens if t != "--dev"]
    if not tokens:
        return CmdResult.fail(msg="Usage: /help.search {--dev} <pattern>")
    pattern = " ".join(tokens)

    if _REGEX_META_RE.search(pattern):
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return CmdResult.fail(msg=f"Invalid regex: {e}")
    else:
        rx = re.compile(re.escape(pattern), re.IGNORECASE)

    prefix = ctx.engine.prefix
    matches: list[str] = []  # command names, for CmdResult.value
    rendered = 0
    truncated = False

    for name in sorted(ctx.engine.plugins):
        plugin = ctx.engine.plugins[name]
        hit_fields: list[tuple[str, str, tuple[int, int]]] = []
        for label, text in _search_fields(name, plugin, include_dev):
            m = rx.search(text)
            if m:
                hit_fields.append((label, text, m.span()))
        if not hit_fields:
            continue
        matches.append(name)
        if rendered >= _MAX_SEARCH_RESULTS:
            truncated = True
            continue
        arg_str = f" {_color_args(plugin.args)}" if plugin.args else ""
        ctx.write_markup(f"[{_CMD}]{prefix}{name}[/]{arg_str} - {plugin.help}")
        for label, text, span in hit_fields:
            if label in ("name", "help", "args"):
                continue  # already visible in the header line above
            ctx.write_markup(f"  [{_SEP}]({label})[/] {_highlight(text, span)}")
        rendered += 1

    if not matches:
        ctx.result(f"No matches for '{pattern}'.")
        return CmdResult.ok(value="")
    suffix = f" (showing first {_MAX_SEARCH_RESULTS})" if truncated else ""
    ctx.result(f"{len(matches)} match{'es' if len(matches) != 1 else ''}{suffix}.")
    # Newline-joined names follow the CmdResult.value convention (strings).
    return CmdResult.ok(value="\n".join(matches))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="help",
    args="{cmd}",
    help="List REPL commands, or show help for one command.",
    long_help="""\
Three modes:
  /help              - list top-level commands (subcommand count shown)
  /help <cmd>        - show usage, help text, and subcommands
  /help proto.crc    - show help for a subcommand (dot notation)""",
    handler=_handler,
    sub_commands={
        "target": Command(
            help="Show only imported target device commands.",
            handler=_handler_target,
        ),
        "run": Command(
            help="List available .run scripts with descriptions.",
            handler=_handler_run,
        ),
        "plugin": Command(
            help="List loaded plugins grouped by source.",
            handler=_handler_plugin,
        ),
        "dev": Command(
            args="<cmd>",
            help="Show a command handler's Python docstring.",
            handler=_handler_dev,
        ),
        "search": Command(
            args="{--dev} <pattern>",
            help="Search command names and help text for a regex or literal.",
            long_help="""\
Search every registered command's name, short help, args, and long_help
for a pattern. No regex metacharacters: treated as a case-insensitive
literal substring. Otherwise: compiled as a case-insensitive regex.

  /help.search timeout            find all commands mentioning "timeout"
  /help.search ^proto\\.           commands starting with "proto."
  /help.search --dev ctx\\.result  also search handler docstrings

Returns a list of matching command names as CmdResult.value, suitable
for $(VAR) <- capture in scripts.""",
            handler=_handler_search,
        ),
    },
)
