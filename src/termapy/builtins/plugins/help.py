"""Built-in plugin: list commands or show help for a specific command."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

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
# fields rank higher when sorting matches for display. An exact flag-name
# hit is nearly as specific as a command-name hit, so "flags" sits right
# after "name". /help.search uses the same set (docstring added with --dev).
_FUZZY_FIELDS = ("name", "flags", "help", "args", "long_help")

# Section headings shown for each matched field on "Did you mean" output.
# Commands are grouped under the heading of the highest-priority field
# that matched. Keeps rows clean (no per-row label column).
_FIELD_HEADING = {
    "name": "Command Name:",
    "flags": "Flags:",
    "help": "Help String:",
    "args": "Arguments:",
    "long_help": "Long Help:",
}


def _field_text(name: str, plugin, field: str) -> str:
    """Return the text body for a given fuzzy-search field on one plugin."""
    if field == "name":
        return name
    if field == "flags":
        parts = []
        for canonical, aliases, desc in _canonical_flags(plugin):
            parts.extend([canonical, *aliases, desc])
        return " ".join(parts)
    return getattr(plugin, field, "") or ""


def _parse_search_terms(query: str) -> tuple[list[str], list[str]]:
    """Split a ``/help`` query into positive and negative substring terms.

    Rules:
      - tokens starting with a single ``-`` followed by a non-dash char
        (e.g. ``-foo``) are *negative* terms and strip the leading dash
      - every other token is a *positive* term, taken verbatim. This
        means ``--flag`` stays literal and matches declared flag names.

    Returns ``(positives, negatives)`` with the leading dash already
    stripped from negatives. Either list may be empty.
    """
    positives: list[str] = []
    negatives: list[str] = []
    for tok in query.split():
        if len(tok) > 1 and tok[0] == "-" and tok[1] != "-":
            negatives.append(tok[1:])
        else:
            positives.append(tok)
    return positives, negatives


def _best_tier(needle: str, name: str, plugin) -> tuple[int | None, str]:
    """Return the highest-priority (tier, field) where ``needle`` appears."""
    needle = needle.lower()
    for tier, field in enumerate(_FUZZY_FIELDS):
        text = _field_text(name, plugin, field).lower()
        if needle in text:
            return tier, field
    return None, ""


def _fuzzy_matches(query: str, plugins: dict) -> list[tuple[str, str]]:
    """Return ``(command_name, field)`` pairs for a multi-term query.

    Query grammar: space-separated terms; a leading single ``-`` marks a
    term as excluded. All positive terms must appear somewhere in the
    searched fields; any match on an excluded term drops the command.

    Field priority (name > flags > help > args > long_help) comes from the
    *first* positive term, so ranking stays predictable when two terms
    could each match different fields. Results are sorted by that tier,
    then alphabetically.
    """
    positives, negatives = _parse_search_terms(query)
    if not positives:
        return []
    ranked: list[tuple[int, str, str]] = []
    for name, plugin in plugins.items():
        # Every positive must match somewhere; the first term drives the tier.
        tier, field = _best_tier(positives[0], name, plugin)
        if tier is None:
            continue
        if not all(_best_tier(t, name, plugin)[0] is not None for t in positives[1:]):
            continue
        # Any excluded term hit drops this command.
        if any(_best_tier(t, name, plugin)[0] is not None for t in negatives):
            continue
        ranked.append((tier, name, field))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(name, field) for _, name, field in ranked]


# One-line "where is this available" hints for each restrictive
# capability field.  Baseline fields (terminal_output, serial_io,
# dispatch, config_read) are intentionally absent -- they're provided
# by every environment and don't belong in a "Requires:" listing.
_CAPABILITY_HINTS: dict[str, str] = {
    "block_until": "inside .run scripts only",
    "confirm_dialog": "TUI + script runner (needs Yes/Cancel dialog)",
    "ui_notify": "TUI only (toast notifications)",
    "status_bar": "TUI only (bottom status line)",
    "screen_capture": "TUI only (save_screenshot / get_screen_text)",
    "tui_mode": "TUI only (use /tui to switch)",
    "serial_connected": "when a serial port is open",
}


def _required_capability_rows(needs) -> list[tuple[str, str]]:
    """Return ``(name, hint)`` pairs for the restrictive capabilities a
    command declares.  Baseline capabilities are skipped -- a command
    that uses terminal output doesn't need a line saying so.
    """
    return [
        (name, _CAPABILITY_HINTS[name])
        for name in needs.missing_from(_BASELINE_CAPS)
        if name in _CAPABILITY_HINTS
    ]


# Sentinel for the "everything-baseline" environment.  A command's
# ``needs.missing_from(_BASELINE_CAPS)`` returns exactly its restrictive
# requirements -- the fields it had to opt into, since every baseline
# field is True on both sides and cancels out.
_BASELINE_CAPS = CapabilitySet()


def _canonical_flags(plugin) -> list[tuple[str, list[str], str]]:
    """Walk ``plugin.flags`` and return one row per canonical flag.

    Returns a list of ``(canonical, aliases, description)`` tuples, sorted
    by canonical name. Alias entries (``"-v": "--verbose"``) collapse onto
    their canonical row.
    """
    if not plugin.flags:
        return []
    aliases_of: dict[str, list[str]] = {}
    descriptions: dict[str, str] = {}
    for flag, val in plugin.flags.items():
        if val.startswith("-") and val in plugin.flags:
            aliases_of.setdefault(val, []).append(flag)
        else:
            descriptions[flag] = val
            aliases_of.setdefault(flag, [])
    return [
        (flag, sorted(aliases_of[flag]), descriptions[flag])
        for flag in sorted(descriptions)
    ]


def _matching_flag_names(needles: str | list[str], plugin) -> list[str]:
    """Canonical flag names whose name/alias/description matches any needle."""
    if isinstance(needles, str):
        needles = [needles]
    needles = [n.lower() for n in needles if n]
    hits = []
    for canonical, aliases, desc in _canonical_flags(plugin):
        haystacks = [canonical.lower(), desc.lower(), *(a.lower() for a in aliases)]
        if any(n in h for n in needles for h in haystacks):
            hits.append(canonical)
    return hits


def _underline(text: str, needles: str | list[str]) -> str:
    """Wrap every case-insensitive occurrence of any needle in ``[u]...[/u]``."""
    if isinstance(needles, str):
        needles = [needles]
    needles = [n for n in needles if n]
    if not needles or not text:
        return text
    pattern = "|".join(re.escape(n) for n in needles)
    return re.sub(
        pattern, lambda m: f"[u]{m.group()}[/u]", text, flags=re.IGNORECASE,
    )


def _show_did_you_mean(
    ctx: PluginContext, query: str, matches: list[tuple[str, str]],
) -> CmdResult:
    """Render a 'Did you mean:' list grouped by matched field.

    Positive terms from ``query`` drive what gets underlined and which
    flag names are called out on Flags: rows. Negative terms are already
    filtered by the caller and never appear in output.
    """
    positives, _ = _parse_search_terms(query)
    prefix = ctx.engine.prefix
    plugins = ctx.engine.plugins
    ctx.write_markup(f"Did you mean:")
    current_field: str | None = None
    for match_name, field in matches:
        if field != current_field:
            heading = _FIELD_HEADING.get(field, field)
            ctx.write_markup(f"  [{_SEP}]{heading}[/]")
            current_field = field
        p = plugins[match_name]
        name_rendered = _underline(match_name, positives)
        args_rendered = _underline(p.args, positives) if p.args else ""
        help_rendered = _underline(p.help, positives)
        arg_str = f" {_color_args(args_rendered)}" if args_rendered else ""
        flag_hint = ""
        if field == "flags":
            hits = _matching_flag_names(positives, p)
            if hits:
                rendered = " ".join(_underline(h, positives) for h in hits)
                flag_hint = f" [{_OPT}]{rendered}[/]"
        ctx.write_markup(
            f"    [{_CMD}]{prefix}{name_rendered}[/]{arg_str}{flag_hint}"
            f" - {help_rendered}"
        )
        if field == "long_help" and p.long_help:
            for line in p.long_help.strip().splitlines():
                ctx.write_markup(
                    f"      [{_SEP}]{_underline(line, positives)}[/]"
                )
    return CmdResult.ok(value="\n".join(m for m, _ in matches))


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
    query = name  # preserve what the user typed so we can underline it
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
        # Forgiving fallback: substring match across name, flags, help,
        # args, long_help. Each hit carries the field that matched.
        matches = _fuzzy_matches(name, ctx.engine.plugins)
        if len(matches) == 1:
            name = matches[0][0]
            plugin = ctx.engine.plugins[name]
            # fall through to the rendering block below
        elif matches:
            return _show_did_you_mean(ctx, name, matches)
        else:
            return CmdResult.fail(msg=f"Unknown command: {name}")
    # Underline the user's query wherever it appears -- works for both
    # exact matches (/help var) and single-hit fuzzy matches.
    positives, _ = _parse_search_terms(query)
    args_rendered = _underline(plugin.args, positives) if plugin.args else ""
    help_rendered = _underline(plugin.help, positives)
    arg_str = f" {_color_args(args_rendered)}" if args_rendered else ""
    ctx.write_markup(
        f"[{_CMD}]{prefix}{_underline(name, positives)}[/]{arg_str} - {help_rendered}"
    )
    if dev_mode:
        docstring = getattr(plugin.handler, "__doc__", None)
        if docstring:
            ctx.output("  -- developer docstring --")
            _write_docstring(ctx, docstring)
        else:
            ctx.output("  (no docstring)")
    elif plugin.long_help:
        # status (ctx.write) auto-indents 2 spaces; write_markup does not,
        # so pre-pad to match the legacy indentation.
        for line in plugin.long_help.strip().splitlines():
            ctx.write_markup(f"    {_underline(line, positives)}")
    # Show declared flags (first-class). Aliases collapse onto their
    # canonical row so each flag is documented exactly once.
    rows = _canonical_flags(plugin)
    if rows:
        ctx.write_markup(f"  [{_SEP}]Flags:[/]")
        for canonical, aliases, desc in rows:
            names = ", ".join([canonical, *aliases])
            ctx.write_markup(
                f"    [{_OPT}]{_underline(names, positives)}[/]"
                f" - {_underline(desc, positives)}"
            )
    # Show required environment capabilities so users can see why a
    # command might fail in a given context (REPL vs script, TUI vs CLI,
    # connected vs disconnected).  Only list restrictive capabilities the
    # command explicitly opted into -- baseline capabilities are noise.
    required = _required_capability_rows(plugin.needs)
    if required:
        ctx.write_markup(f"  [{_SEP}]Requires:[/]")
        for cap_name, hint in required:
            ctx.write_markup(
                f"    [{_OPT}]{_underline(cap_name, positives)}[/]"
                f" - [{_SEP}]{hint}[/]"
            )
    # Show subcommands if any
    if plugin.children:
        ctx.write_markup(f"  [{_SEP}]Subcommands:[/]")
        plugins = ctx.engine.plugins
        for child_name in sorted(plugin.children):
            child = plugins.get(child_name)
            if child:
                child_args = (
                    _underline(child.args, positives) if child.args else ""
                )
                arg_str = f" {_color_args(child_args)}" if child_args else ""
                suffix = f"  [{_SEP}]...[/]" if child.children else ""
                ctx.write_markup(
                    f"    [{_CMD}]{prefix}{_underline(child_name, positives)}[/]"
                    f"{arg_str} - {_underline(child.help, positives)}{suffix}"
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
            if "." in cmd_name:
                continue
            # Script-only commands (needs block_until) render in their own
            # section below so users can see at a glance which commands
            # only run inside .run files.
            if plugin.needs.block_until:
                continue
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

        # Script-only blocking commands (anything needing block_until).
        # Pulled from the live registry so adding a new script-only
        # command is just setting ``needs=CapabilitySet(block_until=True)``
        # on its Command -- no second place to update.
        script_only = [
            (cmd_name, plugin)
            for cmd_name, plugin in all_plugins.items()
            if "." not in cmd_name and plugin.needs.block_until
        ]
        if script_only:
            ctx.write_markup("")
            ctx.write_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
            for cmd_name, plugin in sorted(script_only, key=lambda x: x[0]):
                cmd_col = _pad(f"  [{_CMD}]{prefix}{cmd_name}[/]", cmd_w + 2)
                args_text = _truncate_args(plugin.args or "", prefix, cmd_name)
                arg_col = _pad(_color_args(args_text), arg_w)
                sub_count = f"  [dim]({len(plugin.children)})[/]" if plugin.children else ""
                ctx.write_markup(f"{cmd_col} {arg_col}  {plugin.help}{sub_count}")

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
        if "." in cmd_name:
            continue
        # Script-only (block_until) commands render in their own section.
        if plugin.needs.block_until:
            continue
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

    # Script-only commands grouped separately so users scanning for
    # interactive commands aren't misled by entries that only work in
    # .run files.
    script_only = [
        (cmd_name, plugin)
        for cmd_name, plugin in all_plugins.items()
        if "." not in cmd_name and plugin.needs.block_until
    ]
    if script_only:
        ctx.write_markup("")
        ctx.write_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
        for cmd_name, plugin in sorted(script_only, key=lambda x: x[0]):
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
        ("flags", _field_text(name, plugin, "flags")),
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
    include_dev = ctx.flag("--dev")
    tokens = args.split() if isinstance(args, str) else []
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
            args="<pattern>",
            flags={"--dev": "Also search handler docstrings."},
            help="Search command names and help text for a regex or literal.",
            long_help="""\
Search every registered command's name, short help, args, flags, and
long_help for a pattern. No regex metacharacters: treated as a
case-insensitive literal substring. Otherwise: compiled as a
case-insensitive regex.

  /help.search timeout            find all commands mentioning "timeout"
  /help.search ^proto\\.           commands starting with "proto."
  /help.search --dev ctx\\.result  also search handler docstrings

Returns matching command names as CmdResult.value (newline-joined),
suitable for $(VAR) <- capture in scripts.""",
            handler=_handler_search,
        ),
    },
)
