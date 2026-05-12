"""Built-in plugin: forgiving /help with man-page detail view."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import (
    ENVIRONMENTS,
    CapabilitySet,
    CmdResult,
    Command,
    interpolate_help,
    resolve_long_help,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# ── Constants ────────────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"^(\s*)(\w+)(:)(.*)")
_TITLE_FMT = "  [bold]{text}[/]"
_LABEL_FMT = "  {indent}[bold]{label}[/]{rest}"

# Section-header format for man-page view. All-caps bold, flush-left.
_SECTION_FMT = "[bold]{text}[/]"

# Colors for help output
_CMD = "cyan"          # command names
_OPT = "green"         # {optional} args
_REQ = "yellow"        # <required> args
_SEP = "dim"           # section separators and hints
_SRC = "dim"           # source labels

_OPT_RE = re.compile(r"(\{[^}]+\})")
_REQ_RE = re.compile(r"(<[^>]+>)")
_MARKUP_RE = re.compile(r"\[[^\]]*\]")

# Cap the command column in listings so a pathologically long plugin name
# can't shove the help column off the right edge.
_MAX_CMD_COL = 28

# One-line "where is this available" hints for each restrictive capability
# field. Baseline capabilities are intentionally absent -- they're provided
# by every environment and don't belong in a REQUIRES listing.
_CAPABILITY_HINTS: dict[str, str] = {
    "block_until": "inside .run scripts only",
    "confirm_dialog": "TUI + script runner (needs Yes/Cancel dialog)",
    "ui_notify": "TUI only (toast notifications)",
    "status_bar": "TUI only (bottom status line)",
    "screen_capture": "TUI only (save_screenshot / get_screen_text)",
    "tui_mode": "TUI only (use /tui to switch)",
    "serial_connected": "when a serial port is open",
}

# Sentinel for the "everything-baseline" environment. ``needs.missing_from``
# against this returns exactly the restrictive capabilities the command
# opted into.
_BASELINE_CAPS = CapabilitySet()


# ── Markup helpers ───────────────────────────────────────────────────────────


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
    has_summary = len(lines) >= 2 and lines[1].strip() == ""
    for i, line in enumerate(lines):
        text = line.rstrip()
        if i == 0 and has_summary:
            ctx.io.output_markup(_TITLE_FMT.format(text=text))
        elif (m := _LABEL_RE.match(text)):
            ctx.io.output_markup(_LABEL_FMT.format(
                indent=m.group(1),
                label=m.group(2) + m.group(3),
                rest=m.group(4).rstrip(),
            ))
        else:
            ctx.io.output(f"  {text}")


# ── Flag + capability rendering ──────────────────────────────────────────────


def _canonical_flags(plugin) -> list[tuple[str, list[str], str]]:
    """Walk ``plugin.flags`` and return one row per canonical flag.

    Returns a list of ``(canonical, aliases, description)`` tuples, sorted
    by canonical name. Alias entries (``"-t": "--table"``) collapse onto
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


def _required_capability_rows(needs) -> list[tuple[str, str]]:
    """Return ``(name, hint)`` pairs for restrictive capabilities a command
    declares. Baseline capabilities are skipped -- a command that uses
    terminal output doesn't need a line saying so.
    """
    return [
        (name, _CAPABILITY_HINTS[name])
        for name in needs.missing_from(_BASELINE_CAPS)
        if name in _CAPABILITY_HINTS
    ]


# ── Forgiving /help search ───────────────────────────────────────────────────


def _find_candidates(term: str, plugins: dict, prefix: str) -> list[str]:
    """Return command names whose name or short help contains every word of ``term``.

    Substring match, case-insensitive. Multi-word queries AND-match across
    name+help. This is the ``/help`` lookup's forgiving layer -- narrower than
    ``/search`` (which hits args/long_help/flags too). Returns command names
    sorted alphabetically.

    ``prefix`` is the live REPL prefix, used to interpolate each plugin's
    short help before matching so a search for ``!cfg`` finds entries whose
    help says ``{prefix}cfg``.
    """
    words = [w.lower() for w in term.split() if w]
    if not words:
        return []
    hits: list[str] = []
    for name, plugin in plugins.items():
        if getattr(plugin, "hidden", False):
            continue
        help_text = interpolate_help(plugin.help, prefix)
        haystack = f"{name} {help_text}".lower()
        if all(w in haystack for w in words):
            hits.append(name)
    return sorted(hits)


def _siblings_for_see_also(name: str, plugins: dict) -> list[str]:
    """Return SEE ALSO entries for a command: siblings, plus parent if dotted.

    Siblings = other children of the same parent. For a leaf ``cap.poll``
    that's ``cap.bin``, ``cap.hex``, ``cap.stop``, ``cap.struct``, ``cap.text``,
    and the parent ``cap`` appended at the end. For a pure root with no
    peers the list is empty and the section is skipped.
    """
    parent, sep, _ = name.rpartition(".")
    if not sep:
        # Root command; no parent, no siblings under a shared parent.
        return []
    siblings = sorted(
        n for n in plugins
        if n.rpartition(".")[0] == parent and n != name
    )
    siblings.append(parent)
    return siblings


# ── Row rendering (shared by landscape + candidates + /help.plugin) ──────────


def _landscape_row(prefix: str, name: str, plugin, cmd_w: int,
                   needles: list[str] | None = None) -> str:
    """Format one row of the clean name + one-liner layout.

    Pass ``needles`` only in search-result contexts; underlining is the
    "here's why this matched" affordance and does not belong on the
    no-args landscape or exact-match man page.
    """
    help_text = interpolate_help(plugin.help, prefix)
    if needles:
        name_rendered = _underline(name, needles)
        help_rendered = _underline(help_text, needles)
    else:
        name_rendered = name
        help_rendered = help_text
    cmd_col = _pad(f"  [{_CMD}]{prefix}{name_rendered}[/]", cmd_w + 2)
    return f"{cmd_col}  {help_rendered}"


def _compute_cmd_w(names: list[str], prefix: str) -> int:
    """Derive the command column width from the longest visible name.

    Capped at ``_MAX_CMD_COL`` so a single very long plugin name can't
    shove the help column off the right edge of the terminal.
    """
    if not names:
        return _MAX_CMD_COL
    longest = max(_visible_len(f"{prefix}{n}") for n in names)
    return min(longest + 2, _MAX_CMD_COL)


# ── Man-page detail view ─────────────────────────────────────────────────────


def _render_man_page(ctx: PluginContext, name: str, plugin,
                     dev_mode: bool = False) -> None:
    """Render a command's full detail view in man-page format.

    Sections: NAME, SYNOPSIS (if args), DESCRIPTION, FLAGS (if any),
    REQUIRES (if restrictive caps), SUBCOMMANDS (if children), SEE ALSO
    (if siblings/parent exist). Empty sections are skipped so the page
    stays dense.

    No underlining on this path -- it's an exact-match detail view,
    nothing is a "search hit" to highlight.
    """
    prefix = ctx.engine.prefix
    plugins = ctx.engine.plugins

    # NAME ────────────────────────────────────────────────────────────────────
    ctx.io.output_markup(_SECTION_FMT.format(text="NAME"))
    help_line = interpolate_help(plugin.help, prefix)
    ctx.io.output_markup(f"  [{_CMD}]{prefix}{name}[/] - {help_line}")

    # SYNOPSIS ────────────────────────────────────────────────────────────────
    if plugin.args:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="SYNOPSIS"))
        args_colored = _color_args(plugin.args)
        ctx.io.output_markup(f"  [{_CMD}]{prefix}{name}[/] {args_colored}")

    # DESCRIPTION ─────────────────────────────────────────────────────────────
    # Developer mode shows the handler's Python docstring instead.
    if dev_mode:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="DESCRIPTION (developer)"))
        docstring = getattr(plugin.handler, "__doc__", None)
        if docstring:
            _write_docstring(ctx, docstring)
        else:
            ctx.io.output("  (no docstring)")
    else:
        # long_help may be a str or a callable(ctx) -> str. resolve_long_help
        # normalizes, catching any exception from a dynamic callable so that
        # /help rendering never itself fails.
        lh = resolve_long_help(plugin, ctx)
        if lh:
            ctx.io.output_markup("")
            ctx.io.output_markup(_SECTION_FMT.format(text="DESCRIPTION"))
            # ctx.write auto-indents; use write_markup with explicit indent
            # so markup in long_help passes through.
            for line in lh.strip().splitlines():
                ctx.io.output_markup(f"  {line}")

    # FLAGS ───────────────────────────────────────────────────────────────────
    rows = _canonical_flags(plugin)
    if rows:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="FLAGS"))
        for canonical, aliases, desc in rows:
            names = ", ".join([canonical, *aliases])
            ctx.io.output_markup(f"  [{_OPT}]{names}[/] - {desc}")

    # REQUIRES ────────────────────────────────────────────────────────────────
    required = _required_capability_rows(plugin.needs)
    if required:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="REQUIRES"))
        for cap_name, hint in required:
            ctx.io.output_markup(f"  [{_OPT}]{cap_name}[/] - [{_SEP}]{hint}[/]")

    # AVAILABLE ───────────────────────────────────────────────────────────────
    # Symmetric "where does this run" matrix across all known environments.
    # Derived from comparing plugin.needs against ENVIRONMENTS -- single
    # source of truth, no per-host special casing.  Future hosts get a
    # column for free by adding an entry to ENVIRONMENTS in plugins.py.
    ctx.io.output_markup("")
    ctx.io.output_markup(_SECTION_FMT.format(text="AVAILABLE"))
    cells: list[str] = []
    missing_by_env: dict[str, list[str]] = {}
    for env_name, env_caps in ENVIRONMENTS.items():
        missing = plugin.needs.missing_from(env_caps)
        if missing:
            cells.append(f"[{_REQ}]{env_name}: no[/]")
            missing_by_env[env_name] = missing
        else:
            cells.append(f"[{_OPT}]{env_name}: yes[/]")
    ctx.io.output_markup("  " + "   ".join(cells))
    if missing_by_env:
        # Group identical missing-capability sets so the explanation stays compact.
        # ``frozenset`` keys de-duplicate envs with the same gate.
        groups: dict[frozenset[str], list[str]] = {}
        for env_name, missing in missing_by_env.items():
            groups.setdefault(frozenset(missing), []).append(env_name)
        notes: list[str] = []
        for missing_set, env_names in groups.items():
            envs = "/".join(env_names)
            caps = ", ".join(sorted(missing_set))
            # Phrasing: the COMMAND requires the capability; the
            # environment doesn't supply it.  "MCP does not provide:
            # gui_apps" reads correctly either way.
            notes.append(f"{envs} does not provide: {caps}")
        ctx.io.output_markup(f"  [{_SEP}]({'; '.join(notes)})[/]")

    # SUBCOMMANDS ─────────────────────────────────────────────────────────────
    # Clean name + one-liner, no args column. This is the fix for the
    # pre-redesign "wall of text" complaint on /help cap.
    if plugin.children:
        children = [
            (n, plugins[n])
            for n in sorted(plugin.children)
            if n in plugins and not plugins[n].hidden
        ]
        if children:
            ctx.io.output_markup("")
            ctx.io.output_markup(_SECTION_FMT.format(text="SUBCOMMANDS"))
            cmd_w = _compute_cmd_w([n for n, _ in children], prefix)
            for child_name, child in children:
                ctx.io.output_markup(_landscape_row(prefix, child_name, child, cmd_w))

    # SEE ALSO ────────────────────────────────────────────────────────────────
    see = _siblings_for_see_also(name, plugins)
    if see:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="SEE ALSO"))
        refs = ", ".join(f"[{_CMD}]{prefix}{n}[/]" for n in see)
        ctx.io.output_markup(f"  {refs}")

    # Source annotation for non-built-in plugins (preserved from old renderer).
    if plugin.source not in ("built-in", "app"):
        ctx.io.output_markup("")
        ctx.io.output_markup(f"  [{_SRC}](source: {plugin.source})[/]")


def _render_target_man_page(ctx: PluginContext, tc) -> None:
    """Render a device-supplied command in the same man-page shape as plugins.

    Sections: NAME, SYNOPSIS (if args), DESCRIPTION (if long_help),
    FLAGS (if any).  The source line always reads "target device" so
    users can see at a glance that a command came from /include rather
    than a plugin.

    There is intentionally no REQUIRES, SUBCOMMANDS, or SEE ALSO --
    target commands have no capability declarations, no subcommand
    tree, and no sibling relationships in termapy's registry.
    """
    # NAME ────────────────────────────────────────────────────────────────────
    ctx.io.output_markup(_SECTION_FMT.format(text="NAME"))
    ctx.io.output_markup(f"  [{_CMD}]{tc.name}[/] - {tc.help}")

    # SYNOPSIS ────────────────────────────────────────────────────────────────
    if tc.args:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="SYNOPSIS"))
        ctx.io.output_markup(f"  [{_CMD}]{tc.name}[/] {_color_args(tc.args)}")

    # DESCRIPTION ─────────────────────────────────────────────────────────────
    if tc.long_help:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="DESCRIPTION"))
        for line in tc.long_help.strip().splitlines():
            ctx.io.output_markup(f"  {line}")

    # FLAGS ───────────────────────────────────────────────────────────────────
    # _canonical_flags is duck-typed on a `.flags` attribute, so it works
    # for TargetCommand unchanged.
    rows = _canonical_flags(tc)
    if rows:
        ctx.io.output_markup("")
        ctx.io.output_markup(_SECTION_FMT.format(text="FLAGS"))
        for canonical, aliases, desc in rows:
            names = ", ".join([canonical, *aliases])
            ctx.io.output_markup(f"  [{_OPT}]{names}[/] - {desc}")

    ctx.io.output_markup("")
    ctx.io.output_markup(f"  [{_SRC}](source: target device)[/]")


# ── Candidate list rendering ─────────────────────────────────────────────────


def _render_candidates(ctx: PluginContext, term: str, names: list[str]) -> None:
    """Render a candidate list produced by `_find_candidates`.

    Underlines the search term (it's a search-result context). Uses the
    same columnar layout as the landscape view so users see one visual
    idiom across both modes.
    """
    prefix = ctx.engine.prefix
    plugins = ctx.engine.plugins
    words = [w for w in term.split() if w]
    ctx.io.output_markup(
        f"Candidates matching [{_CMD}]{_underline(term, words)}[/]:"
    )
    cmd_w = _compute_cmd_w(names, prefix)
    for name in names:
        plugin = plugins[name]
        ctx.io.output_markup(
            _landscape_row(prefix, name, plugin, cmd_w, needles=words)
        )


# ── Extension hook: extra man-page section ───────────────────────────────────


def append_files_section(
    ctx: PluginContext,
    title: str,
    files: list[str],
) -> None:
    """Append a man-page-styled section listing available files as a tree.

    Used by ``_handler_help`` in /cfg, /run, /proto so that
    ``/cfg.help`` / ``/run.help`` / ``/proto.help`` show what's
    actually present in the relevant folder right where the user is
    reading the help.  ``files`` should already be sorted display
    strings; entries containing a ``/`` are grouped under their first
    path component (e.g. ``demo/demo.cfg`` becomes a child of
    ``demo/``).  Entries without ``/`` render at the root level.

    Style matches ``cfg._build_tree``: ``├── └── │`` connectors in
    dim, directory names in cyan, file names in blue.

    The plain ``/help <name>`` path bypasses this hook -- it stays
    purely declarative so its output is reproducible across
    environments and stable for the gold test.
    """
    from termapy.tree_render import FileTree

    ctx.io.output_markup("")
    ctx.io.output_markup(_SECTION_FMT.format(text=title))
    if not files:
        ctx.io.output_markup("  (none)")
        return

    # Group "dir/file" entries under their first component; entries
    # without "/" render as loose files at the root level (after the
    # directories, matching the `tree` command's order).
    dirs: dict[str, list[str]] = {}
    loose: list[str] = []
    for f in files:
        head, sep, tail = f.partition("/")
        if sep:
            dirs.setdefault(head, []).append(tail)
        else:
            loose.append(f)

    sections: list[tuple[str, list[str]]] = (
        [(d + "/", dirs[d]) for d in sorted(dirs)]
        + [(f, []) for f in loose]
    )
    for line in FileTree(sections, indent="  ").render():
        ctx.io.output_markup(line)


# ── Main /help lookup ────────────────────────────────────────────────────────


def _show_command_help(ctx: PluginContext, name: str,
                       dev_mode: bool = False) -> CmdResult:
    """Resolve ``name`` with forgiving lookup:

      1. Exact registry match  -> render man-page detail view.
      2. Target-device command -> render its brief one-line form.
      3. Candidate list        -> name+help substring match, CmdResult.ok
                                  with `value` = newline-joined names.
      4. Zero matches          -> fail with hint to try /search.

    ``dev_mode`` routes exact matches to the developer docstring view
    instead of the normal DESCRIPTION.
    """
    plugins = ctx.engine.plugins

    # 1. Exact match wins.
    plugin = plugins.get(name)
    if plugin is not None:
        _render_man_page(ctx, name, plugin, dev_mode=dev_mode)
        return CmdResult.ok()

    # 2. Target device (help-only commands imported from a connected device).
    tc = ctx.ns("target_commands").get(name)
    if tc is not None:
        _render_target_man_page(ctx, tc)
        return CmdResult.ok()

    # 3. Forgiving candidate list (name + short help only -- args, long_help,
    #    and flags live in /search's territory).
    candidates = _find_candidates(name, plugins, ctx.engine.prefix)
    if candidates:
        _render_candidates(ctx, name, candidates)
        return CmdResult.ok(value="\n".join(candidates))

    # 4. No hits anywhere -- hint at the deeper tool.
    p = ctx.engine.prefix
    return CmdResult.fail(
        msg=f"No command matches '{name}'. "
            f"Try {p}search {name} for a deeper search."
    )


# ── /help handler (landscape or per-command) ─────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """List REPL commands or show detail for one.

    With no args: clean landscape view grouped by source (names + one-liner
    help only -- no args column, no subcommand counts). With args: forgiving
    lookup that renders an exact match as a man page, otherwise a candidate
    list (substring match on name + short help), or fails with a `/search`
    hint when nothing matches.

    Args:
        ctx: Plugin context for engine plugin registry and output.
        args: Optional command name (or search term) to look up.
    """
    # Preserve case: plugin names are conventionally lowercase, but device
    # commands brought in by /include (AT+INFO, $GPGGA) are usually upper.
    # Matching exactly lets both kinds round-trip; users who mistype the
    # casing fall through to the forgiving candidate list, which does its
    # own case-insensitive matching internally.
    name = args.strip() if isinstance(args, str) else ""
    prefix = ctx.engine.prefix
    if name:
        return _show_command_help(ctx, name)

    # ── No args: landscape ───────────────────────────────────────────────────
    _SOURCE_ORDER = {"app": 0, "built-in": 1, "global": 2}
    _SOURCE_LABELS = {
        "app": "Application",
        "built-in": "Application Plugins",
        "global": "User Plugins",
    }

    all_plugins = ctx.engine.plugins

    # ``--mcp`` narrows the landscape to commands an MCP client would see.
    # The MCP host advertises ``ENVIRONMENTS["MCP"]`` so we just check
    # each plugin's needs against that capability set -- same gate the
    # catalog filter applies, no duplication.
    mcp_only = ctx.flag("--mcp")
    mcp_caps = ENVIRONMENTS["MCP"] if mcp_only else None

    # Collect top-level commands by source, excluding script-only ones
    # (they render in their own section so users see at a glance which
    # commands only work inside .run files).
    groups: dict[str, list] = {}
    for cmd_name, plugin in all_plugins.items():
        if "." in cmd_name:
            continue
        if plugin.needs.block_until:
            continue
        if getattr(plugin, "hidden", False):
            continue
        if mcp_caps is not None and plugin.needs.missing_from(mcp_caps):
            continue
        groups.setdefault(plugin.source, []).append((cmd_name, plugin))

    # Unified column width across every section of the landscape so all
    # rows line up regardless of which section they're in.
    script_only = [
        (cmd_name, plugin)
        for cmd_name, plugin in all_plugins.items()
        if "." not in cmd_name
        and plugin.needs.block_until
        and not getattr(plugin, "hidden", False)
        and (mcp_caps is None or not plugin.needs.missing_from(mcp_caps))
    ]
    directives = ctx.engine.directives or []
    all_names = (
        [n for names in groups.values() for n, _ in names]
        + [n for n, _ in script_only]
        + [d.name for d in directives]
    )
    cmd_w = _compute_cmd_w(all_names, prefix)

    if mcp_only:
        # Make the filter mode unmistakable.  Counts read against
        # everything that would otherwise have been listed top-level
        # (matching the post-filter denominator users care about).
        shown = sum(len(v) for v in groups.values()) + len(script_only)
        total = sum(
            1 for n, p in all_plugins.items()
            if "." not in n and not getattr(p, "hidden", False)
        )
        ctx.io.output_markup(
            f"[{_SEP}]-- MCP-visible only ({shown} of {total} top-level) --[/]"
        )
        ctx.io.output_markup("")

    sorted_sources = sorted(groups, key=lambda s: _SOURCE_ORDER.get(s, 3))
    first = True
    for source in sorted_sources:
        label = _SOURCE_LABELS.get(source, f"{source} Plugins")
        if not first:
            ctx.io.output_markup("")
        first = False
        ctx.io.output_markup(f"[{_SEP}]-- {label} --[/]")
        for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
            ctx.io.output_markup(_landscape_row(prefix, cmd_name, plugin, cmd_w))

    if directives:
        ctx.io.output_markup("")
        ctx.io.output_markup(f"[{_SEP}]-- Directives --[/]")
        for d in directives:
            cmd_col = _pad(f"  [{_CMD}]{d.name}[/]", cmd_w + 2)
            ctx.io.output_markup(f"{cmd_col}  {d.help}")

    if script_only:
        ctx.io.output_markup("")
        ctx.io.output_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
        for cmd_name, plugin in sorted(script_only, key=lambda x: x[0]):
            ctx.io.output_markup(_landscape_row(prefix, cmd_name, plugin, cmd_w))

    scripts_dir = ctx.fs.scripts_dir
    if scripts_dir.is_dir():
        scripts = sorted(scripts_dir.glob("*.run"))
        if scripts:
            ctx.io.output_markup("")
            _render_scripts(ctx, scripts, prefix, cmd_w)

    if ctx.ns("target_commands"):
        ctx.io.output_markup("")
        _render_target(ctx, cmd_w)

    # Footer: teach the two other modes. Single dim line, always emitted.
    ctx.io.output_markup("")
    ctx.io.output_markup(
        f"[{_SEP}]Use {prefix}help <term> to find a command, "
        f"{prefix}search <word> for a deep search.[/]"
    )
    return CmdResult.ok()


# ── /help.target, /help.run, /help.plugin, /help.dev ─────────────────────────


def _handler_target(ctx: PluginContext, args: str) -> CmdResult:
    """Show only imported target device commands."""
    target_cmds = ctx.ns("target_commands")
    if not target_cmds:
        ctx.io.result("No target commands included. Use /include first.")
        return CmdResult.ok()
    _render_target(ctx)
    ctx.io.result(f"{len(target_cmds)} device commands.")
    return CmdResult.ok()


def _render_target(ctx: PluginContext, cmd_w: int | None = None) -> None:
    """Render the target device command table.

    Target commands are help-only listings of a connected device's own
    commands. Unlike REPL plugins, the user needs to see the args syntax
    inline here because there's no separate ``/help <target>`` drill-down
    for device commands.
    """
    target_cmds = ctx.ns("target_commands")
    names = list(target_cmds)
    if cmd_w is None:
        cmd_w = _compute_cmd_w(names, "")
    ctx.io.output_markup(f"[{_SEP}]-- Target Device --[/]")
    for cmd_name in sorted(names):
        tc = target_cmds[cmd_name]
        cmd_col = _pad(f"  [{_CMD}]{tc.name}[/]", cmd_w + 2)
        args_col = f" {_color_args(tc.args)}" if tc.args else ""
        ctx.io.output_markup(f"{cmd_col}{args_col}  {tc.help}")


def _script_description(path: Path) -> str:
    """Extract a description from a script's leading comment.

    Valid format: first line starts with ``#``, second line is blank.
    If the first line contains `` -- `` the text after the dashes wins.
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


def _render_scripts(ctx: PluginContext, scripts: list, prefix: str,
                    cmd_w: int = _MAX_CMD_COL) -> None:
    """Render the scripts table with matched column width."""
    ctx.io.output_markup(f"[{_SEP}]-- Scripts --[/]")
    for path in scripts:
        name = path.stem
        desc = _script_description(path)
        cmd_col = _pad(f"  [{_CMD}]{prefix}run {name}[/]", cmd_w + 2)
        ctx.io.output_markup(f"{cmd_col}  {desc}")


def _handler_run(ctx: PluginContext, args: str) -> CmdResult:
    """List available .run scripts with descriptions."""
    scripts_dir = ctx.fs.scripts_dir
    if not scripts_dir.is_dir():
        ctx.io.result("No scripts directory.")
        return CmdResult.ok()
    scripts = sorted(scripts_dir.glob("*.run"))
    if not scripts:
        ctx.io.result("No .run scripts found.")
        return CmdResult.ok()
    prefix = ctx.engine.prefix
    _render_scripts(ctx, scripts, prefix)
    ctx.io.result(f"{len(scripts)} scripts.")
    return CmdResult.ok()


def _handler_plugin(ctx: PluginContext, args: str) -> CmdResult:
    """List loaded plugins grouped by source.

    Slim row layout that matches ``/help`` landscape (name + one-liner
    only) so users see one visual idiom across every listing context.
    """
    all_plugins = ctx.engine.plugins
    if not all_plugins:
        ctx.io.result("No plugins loaded.")
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
        if plugin.needs.block_until:
            continue
        if getattr(plugin, "hidden", False):
            continue
        groups.setdefault(plugin.source, []).append((cmd_name, plugin))
    script_only = [
        (cmd_name, plugin)
        for cmd_name, plugin in all_plugins.items()
        if "." not in cmd_name
        and plugin.needs.block_until
        and not getattr(plugin, "hidden", False)
    ]
    all_names = (
        [n for names in groups.values() for n, _ in names]
        + [n for n, _ in script_only]
    )
    prefix = ctx.engine.prefix
    cmd_w = _compute_cmd_w(all_names, prefix)
    sorted_sources = sorted(groups, key=lambda s: _SOURCE_ORDER.get(s, 3))
    first = True
    total = 0
    for source in sorted_sources:
        label = _SOURCE_LABELS.get(source, f"{source} Plugins")
        if not first:
            ctx.io.output_markup("")
        first = False
        ctx.io.output_markup(f"[{_SEP}]-- {label} --[/]")
        for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
            ctx.io.output_markup(_landscape_row(prefix, cmd_name, plugin, cmd_w))
            total += 1

    if script_only:
        ctx.io.output_markup("")
        ctx.io.output_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
        for cmd_name, plugin in sorted(script_only, key=lambda x: x[0]):
            ctx.io.output_markup(_landscape_row(prefix, cmd_name, plugin, cmd_w))
            total += 1

    ctx.io.result(f"{total} commands.")
    return CmdResult.ok()


def _handler_dev(ctx: PluginContext, args: str) -> CmdResult:
    """Show a command handler's Python docstring (developer info)."""
    name = args.strip() if isinstance(args, str) else ""
    if not name:
        return CmdResult.fail(msg="Usage: /help.dev <cmd>")
    return _show_command_help(ctx, name, dev_mode=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="help",
    args="{term}",
    flags={
        "--mcp": "Show only commands visible to the MCP catalog.",
    },
    help="Show the command landscape or look up a specific command.",
    long_help="""\
Three ways to find things:

  /help              landscape of all commands (name + one-liner).
  /help <term>       exact match -> detail; otherwise a candidate list
                     by substring match on name + short help.
  /help --mcp        landscape filtered to MCP-visible commands only
                     (same set the LLM sees via {prefix}mcp.catalog).
  /search <word>     deep search across names, args, flags, long help.

/help is forgiving: if you can't remember the full name, it lists
candidates. Typing /help with zero matches points you at /search.""",
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
    },
)
