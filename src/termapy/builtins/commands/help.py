"""Built-in plugin: forgiving /help with man-page detail view."""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.help_dynamic import is_state_line
from termapy.markup import markup_to_plain
from termapy.plugins import (
    ENVIRONMENTS,
    REQUIREMENT_HINTS,
    CapabilitySet,
    CmdResult,
    Command,
    UsageError,
    escape_markup,
    interpolate_help,
    resolve_long_help,
)
from termapy.plugins.params import render_parameters_block
from termapy.run_docstring import extract_docstring

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# ── Constants ────────────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"^(\s*)(\w+:)(.*)")

# Colors for help output
_CMD = "cyan"  # command names
_OPT = "green"  # {optional} args
_REQ = "yellow"  # <required> args
_SEP = "dim"  # section separators and hints
_SRC = "dim"  # source labels

_OPT_RE = re.compile(r"(\{[^}]+\})")
_REQ_RE = re.compile(r"(<[^>]+>)")

# Cap the command column in listings so a pathologically long plugin name
# can't shove the help column off the right edge.
_MAX_CMD_COL = 28

# Sentinel for the "everything-baseline" environment. ``needs.missing_from``
# against this returns exactly the restrictive capabilities the command
# opted into.
_BASELINE_CAPS = CapabilitySet()


# ── Markup helpers ───────────────────────────────────────────────────────────
#
# Help text is prose, never markup: every name, one-liner, synopsis, flag
# description and long_help line is escaped on its way into the page, so
# "[[test]] sections" and "/xfer.root [path]" read as written (registry
# markup-escape).  The page's own colors are the only tags, plus the one
# markup a long_help may carry, a whole-line ``help_dynamic.green`` state
# line.


def _section_markup(title: str) -> str:
    """A man-page section header: all-caps bold, flush-left."""
    return f"[bold]{escape_markup(title)}[/]"


def _args_markup(args: str) -> str:
    """A synopsis, escaped, with its ``{optional}`` and ``<required>`` groups colored."""
    if not args:
        return ""
    result = escape_markup(args)
    result = _OPT_RE.sub(rf"[{_OPT}]\1[/]", result)
    result = _REQ_RE.sub(rf"[{_REQ}]\1[/]", result)
    return result


def _long_help_line_markup(line: str) -> str:
    """A ``long_help`` line: a ``green()`` state line passes through, prose is escaped."""
    return line if is_state_line(line) else escape_markup(line)


def _pad_markup(markup: str, width: int) -> str:
    """Pad a markup string to a visible width."""
    return markup + " " * max(0, width - len(markup_to_plain(markup)))


def _underline_markup(text: str, needles: str | list[str]) -> str:
    """``text`` escaped, every case-insensitive occurrence of a needle in ``[u]...[/u]``."""
    if isinstance(needles, str):
        needles = [needles]
    needles = [n for n in needles if n]
    if not needles or not text:
        return escape_markup(text)
    pattern = re.compile("|".join(re.escape(n) for n in needles), re.IGNORECASE)
    parts: list[str] = []
    last = 0
    for m in pattern.finditer(text):
        parts.append(escape_markup(text[last : m.start()]))
        parts.append(f"[u]{escape_markup(m.group())}[/u]")
        last = m.end()
    parts.append(escape_markup(text[last:]))
    return "".join(parts)


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
            ctx.io.output_markup(f"  [bold]{escape_markup(text)}[/]")
        elif m := _LABEL_RE.match(text):
            indent, label, rest = m.groups()
            ctx.io.output_markup(
                f"  {escape_markup(indent)}[bold]{escape_markup(label)}[/]{escape_markup(rest)}"
            )
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
    return [(flag, sorted(aliases_of[flag]), descriptions[flag]) for flag in sorted(descriptions)]


def _required_capability_rows(needs) -> list[tuple[str, str]]:
    """Return ``(name, hint)`` pairs for restrictive capabilities a command
    declares. Baseline capabilities are skipped -- a command that uses
    terminal output doesn't need a line saying so.

    Hints are declared on the fields they describe (``_restricted`` in
    ``termapy.plugins.capabilities``); ``REQUIREMENT_HINTS`` is computed
    from them.  Indexing is deliberately direct
    (no membership filter): a missing entry is a test failure, not a
    silently dropped row -- the old silent skip hid every interactive /
    gui_apps declaration from /help.
    """
    return [(name, REQUIREMENT_HINTS[name]) for name in needs.missing_from(_BASELINE_CAPS)]


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
    words = [word.lower() for word in term.split() if word]
    if not words:
        return []
    hits: list[str] = []
    for name, plugin in plugins.items():
        if getattr(plugin, "hidden", False):
            continue
        help_text = interpolate_help(plugin.help, prefix)
        haystack = f"{name} {help_text}".lower()
        if all(word in haystack for word in words):
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
    siblings = sorted(n for n in plugins if n.rpartition(".")[0] == parent and n != name)
    siblings.append(parent)
    return siblings


# ── Row rendering (shared by landscape + candidates + /help.plugin) ──────────


def _landscape_row(
    prefix: str, name: str, plugin, cmd_w: int, needles: list[str] | None = None
) -> str:
    """Format one row of the clean name + one-liner layout.

    Pass ``needles`` only in search-result contexts; underlining is the
    "here's why this matched" affordance and does not belong on the
    no-args landscape or exact-match man page.
    """
    help_text = interpolate_help(plugin.help, prefix)
    if needles:
        name_markup = _underline_markup(name, needles)
        help_markup = _underline_markup(help_text, needles)
    else:
        name_markup = escape_markup(name)
        help_markup = escape_markup(help_text)
    cmd_markup = _pad_markup(f"  [{_CMD}]{escape_markup(prefix)}{name_markup}[/]", cmd_w + 2)
    return f"{cmd_markup}  {help_markup}"


def _compute_cmd_w(names: list[str], prefix: str) -> int:
    """Derive the command column width from the longest name.

    Capped at ``_MAX_CMD_COL`` so a single very long plugin name can't
    shove the help column off the right edge of the terminal.
    """
    if not names:
        return _MAX_CMD_COL
    longest = max(len(f"{prefix}{n}") for n in names)
    return min(longest + 2, _MAX_CMD_COL)


# ── Man-page detail view ─────────────────────────────────────────────────────


def _bare_form(plugin, name: str, prefix: str) -> str:
    """What a bare invocation does, from ``picker=`` and ``bare_sub=``; "" when undeclared.

    The one wording for every command (registry ``bare-declared``), in
    the dispatcher's order -- the picker where the host has one, then the
    subcommand: ``bare: opens the Run picker in the TUI; elsewhere runs
    /run.list``, ``bare: runs /mem.info``.
    """
    sub = f"{prefix}{name}.{plugin.bare_sub}" if plugin.bare_sub else ""
    if plugin.picker:
        picker = f"bare: opens the {plugin.picker.capitalize()} picker in the TUI"
        return f"{picker}; elsewhere runs {sub}" if sub else picker
    return f"bare: runs {sub}" if sub else ""


def _render_man_page(ctx: PluginContext, name: str, plugin, dev_mode: bool = False) -> None:
    """Render a command's full detail view in man-page format.

    Sections: NAME, SYNOPSIS (if args), DESCRIPTION, FLAGS (if any),
    REQUIRED CAPABILITIES (if restrictive caps), SUBCOMMANDS (if children), SEE ALSO
    (if siblings/parent exist). Empty sections are skipped so the page
    stays dense.

    No underlining on this path -- it's an exact-match detail view,
    nothing is a "search hit" to highlight.
    """
    prefix = ctx.prefix
    plugins = ctx.internal.plugins
    command_markup = escape_markup(f"{prefix}{name}")

    # NAME ────────────────────────────────────────────────────────────────────
    ctx.io.output_markup(_section_markup("NAME"))
    help_line = interpolate_help(plugin.help, prefix)
    ctx.io.output_markup(f"  [{_CMD}]{command_markup}[/] - {escape_markup(help_line)}")

    # SYNOPSIS ────────────────────────────────────────────────────────────────
    # A declared bare form gets its own line, built from the declaration,
    # so every command's bare behavior reads the same way.
    bare = _bare_form(plugin, name, prefix)
    if plugin.args or bare:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("SYNOPSIS"))
        if plugin.args:
            ctx.io.output_markup(f"  [{_CMD}]{command_markup}[/] {_args_markup(plugin.args)}")
        if bare:
            ctx.io.output_markup(f"  [{_CMD}]{command_markup}[/] -- {escape_markup(bare)}")

    # DESCRIPTION ─────────────────────────────────────────────────────────────
    # Developer mode shows the handler's Python docstring instead.
    if dev_mode:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("DESCRIPTION (developer)"))
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
            ctx.io.output_markup(_section_markup("DESCRIPTION"))
            # The markup channel, not output(), so a dynamic state line
            # keeps its color; every other line is escaped prose.
            for line in lh.strip().splitlines():
                ctx.io.output_markup(f"  {_long_help_line_markup(line)}")

    # PARAMETERS ──────────────────────────────────────────────────────────────
    # Synthesized from Command.params -- the single source of truth for a
    # declared command's arguments (synopsis above comes from the same list).
    if plugin.params:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("PARAMETERS"))
        for line in render_parameters_block(plugin.params):
            ctx.io.output_markup(escape_markup(line))

    # FLAGS ───────────────────────────────────────────────────────────────────
    rows = _canonical_flags(plugin)
    if rows:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("FLAGS"))
        for canonical, aliases, desc in rows:
            names = ", ".join([canonical, *aliases])
            ctx.io.output_markup(f"  [{_OPT}]{escape_markup(names)}[/] - {escape_markup(desc)}")

    # REQUIRED CAPABILITIES ───────────────────────────────────────────────────
    # Named to round-trip with the code: the rows are CapabilitySet fields,
    # declared as needs=CapabilitySet.<PROFILE> on the Command, so
    # grep -i capabilit finds the feature from the help page.
    # The rows double as LIVE STATUS: each is checked against the engine's
    # effective set -- the same answer the dispatch gate uses, including
    # the dynamic fields (serial_connected, block_until) -- so a
    # requirement the session can't meet right now renders yellow with a
    # "(missing)" marker.  The marker is text, not just color, so MCP and
    # no-color output carry the status too.
    required = _required_capability_rows(plugin.needs)
    if required:
        effective = (
            ctx.internal.effective_capabilities()
            if ctx.internal.effective_capabilities is not None
            else ctx.capabilities
        )
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("REQUIRED CAPABILITIES"))
        for cap_name, hint in required:
            cap_markup = escape_markup(cap_name)
            hint_markup = escape_markup(hint)
            if getattr(effective, cap_name):
                ctx.io.output_markup(f"  [{_OPT}]{cap_markup}[/] - [{_SEP}]{hint_markup}[/]")
            else:
                ctx.io.output_markup(
                    f"  [{_REQ}]{cap_markup}[/] - [{_SEP}]{hint_markup}[/] [{_REQ}](missing)[/]"
                )

    # AVAILABLE ───────────────────────────────────────────────────────────────
    # Symmetric "where does this run" matrix across all known environments.
    # Derived from comparing plugin.needs against ENVIRONMENTS -- single
    # source of truth, no per-host special casing.  Future hosts get a
    # column for free by adding an entry to ENVIRONMENTS in plugins.py.
    # Right only while every frontend that meets a command's needs
    # registers it: test_architecture pins that (registry
    # available-matches-registration).
    # ENVIRONMENTS treats dynamic capabilities as attainable (see its
    # comment), so this matrix means "could ever run there"; right-now
    # status lives in the REQUIRED CAPABILITIES rows above.  The column
    # matching ctx.environment is marked "(current)" so the reader knows
    # which one they are in.
    ctx.io.output_markup("")
    ctx.io.output_markup(_section_markup("AVAILABLE"))
    cells: list[str] = []
    missing_by_env: dict[str, list[str]] = {}
    for env_name, env_caps in ENVIRONMENTS.items():
        missing = plugin.needs.missing_from(env_caps)
        if missing:
            cell = f"[{_REQ}]{escape_markup(env_name)}: no[/]"
            missing_by_env[env_name] = missing
        else:
            cell = f"[{_OPT}]{escape_markup(env_name)}: yes[/]"
        if env_name == ctx.environment:
            cell += f" [{_SEP}](current)[/]"
        cells.append(cell)
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
        ctx.io.output_markup(f"  [{_SEP}]({escape_markup('; '.join(notes))})[/]")

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
            ctx.io.output_markup(_section_markup("SUBCOMMANDS"))
            cmd_w = _compute_cmd_w([n for n, _ in children], prefix)
            for child_name, child in children:
                ctx.io.output_markup(_landscape_row(prefix, child_name, child, cmd_w))

    # SEE ALSO ────────────────────────────────────────────────────────────────
    see = _siblings_for_see_also(name, plugins)
    if see:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("SEE ALSO"))
        refs_markup = ", ".join(f"[{_CMD}]{escape_markup(prefix + n)}[/]" for n in see)
        ctx.io.output_markup(f"  {refs_markup}")

    # Source annotation for non-built-in plugins (preserved from old renderer).
    if plugin.source not in ("built-in", "app"):
        ctx.io.output_markup("")
        ctx.io.output_markup(f"  [{_SRC}](source: {escape_markup(plugin.source)})[/]")


def _render_target_man_page(ctx: PluginContext, tc) -> None:
    """Render a device-supplied command in the same man-page shape as plugins.

    Sections: NAME, SYNOPSIS (if args), DESCRIPTION (if long_help),
    FLAGS (if any).  The source line always reads "target device" so
    users can see at a glance that a command came from the active
    profile rather than a plugin.

    There is intentionally no REQUIRED CAPABILITIES, SUBCOMMANDS, or SEE ALSO --
    device commands have no capability declarations, no subcommand
    tree, and no sibling relationships in termapy's registry.

    Every field comes from the device's profile, so all of it is escaped,
    long_help included: a profile has no state lines to pass through.
    """
    name_markup = escape_markup(tc.name)

    # NAME ────────────────────────────────────────────────────────────────────
    ctx.io.output_markup(_section_markup("NAME"))
    ctx.io.output_markup(f"  [{_CMD}]{name_markup}[/] - {escape_markup(tc.help)}")

    # SYNOPSIS ────────────────────────────────────────────────────────────────
    if tc.args:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("SYNOPSIS"))
        ctx.io.output_markup(f"  [{_CMD}]{name_markup}[/] {_args_markup(tc.args)}")

    # DESCRIPTION ─────────────────────────────────────────────────────────────
    if tc.long_help:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("DESCRIPTION"))
        for line in tc.long_help.strip().splitlines():
            ctx.io.output_markup(f"  {escape_markup(line)}")

    # FLAGS ───────────────────────────────────────────────────────────────────
    # _canonical_flags is duck-typed on a `.flags` attribute, so it works
    # for the profile-command view's SimpleNamespace unchanged.
    rows = _canonical_flags(tc)
    if rows:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("FLAGS"))
        for canonical, aliases, desc in rows:
            names = ", ".join([canonical, *aliases])
            ctx.io.output_markup(f"  [{_OPT}]{escape_markup(names)}[/] - {escape_markup(desc)}")

    ctx.io.output_markup("")
    ctx.io.output_markup(f"  [{_SRC}](source: target device)[/]")


# ── Candidate list rendering ─────────────────────────────────────────────────


def _render_candidates(ctx: PluginContext, term: str, names: list[str]) -> None:
    """Render a candidate list produced by `_find_candidates`.

    Underlines the search term (it's a search-result context). Uses the
    same columnar layout as the landscape view so users see one visual
    idiom across both modes.
    """
    prefix = ctx.prefix
    plugins = ctx.internal.plugins
    words = [word for word in term.split() if word]
    ctx.io.output_markup(f"Candidates matching [{_CMD}]{_underline_markup(term, words)}[/]:")
    cmd_w = _compute_cmd_w(names, prefix)
    for name in names:
        plugin = plugins[name]
        ctx.io.output_markup(_landscape_row(prefix, name, plugin, cmd_w, needles=words))


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
    ctx.io.output_markup(_section_markup(title))
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

    sections: list[tuple[str, list[str]]] = [(d + "/", dirs[d]) for d in sorted(dirs)] + [
        (f, []) for f in loose
    ]
    for line in FileTree(sections, indent="  ").render():
        ctx.io.output_markup(line)


# ── Main /help lookup ────────────────────────────────────────────────────────


def _show_command_help(ctx: PluginContext, name: str, dev_mode: bool = False) -> CmdResult:
    """Resolve ``name`` with forgiving lookup:

      1. Exact registry match  -> render man-page detail view.
      2. Target-device command -> render its brief one-line form.
      3. Candidate list        -> name+help substring match, CmdResult.ok
                                  with `value` = newline-joined names.
      4. Zero matches          -> fail with hint to try /search.

    ``dev_mode`` routes exact matches to the developer docstring view
    instead of the normal DESCRIPTION.
    """
    plugins = ctx.internal.plugins

    # 1. Exact match wins.
    plugin = plugins.get(name)
    if plugin is not None:
        _render_man_page(ctx, name, plugin, dev_mode=dev_mode)
        # Resolved name so scripts can confirm what /help matched.
        return CmdResult.ok(value=name)

    # 2. Device command from the active profile.
    from termapy.profile import profile_command_view

    tc = profile_command_view(ctx.ns("active_profile")).get(name)
    if tc is not None:
        _render_target_man_page(ctx, tc)
        return CmdResult.ok(value=name)

    # 3. Forgiving candidate list (name + short help only -- args, long_help,
    #    and flags live in /search's territory).
    candidates = _find_candidates(name, plugins, ctx.prefix)
    if candidates:
        _render_candidates(ctx, name, candidates)
        return CmdResult.ok(value="\n".join(candidates))

    # 4. No hits anywhere -- hint at the deeper tool.
    p = ctx.prefix
    return CmdResult.fail(
        msg=f"No command matches '{name}'. Try {p}search {name} for a deeper search."
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
        ctx: Plugin context for the plugin registry and output.
        args: Optional command name (or search term) to look up.
    """
    # Preserve case: plugin names are conventionally lowercase, but device
    # commands published in v2 profiles (AT+INFO, $GPGGA) are usually upper.
    # Matching exactly lets both kinds round-trip; users who mistype the
    # casing fall through to the forgiving candidate list, which does its
    # own case-insensitive matching internally.
    name = args.strip() if isinstance(args, str) else ""
    prefix = ctx.prefix
    if name:
        return _show_command_help(ctx, name)

    # ── No args: landscape ───────────────────────────────────────────────────
    _SOURCE_ORDER = {"app": 0, "built-in": 1, "global": 2}
    _SOURCE_LABELS = {
        "app": "Application",
        "built-in": "Application Plugins",
        "global": "User Plugins",
    }

    all_plugins = ctx.internal.plugins

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
    directives = ctx.internal.directives or []
    all_names = (
        [n for names in groups.values() for n, _ in names]
        + [n for n, _ in script_only]
        + [directive.name for directive in directives]
    )
    cmd_w = _compute_cmd_w(all_names, prefix)

    if mcp_only:
        # Make the filter mode unmistakable.  Counts read against
        # everything that would otherwise have been listed top-level
        # (matching the post-filter denominator users care about).
        shown = sum(len(value) for value in groups.values()) + len(script_only)
        total = sum(
            1 for n, p in all_plugins.items() if "." not in n and not getattr(p, "hidden", False)
        )
        ctx.io.output_markup(
            f"[{_SEP}]-- MCP-visible only ({shown:d} of {total:d} top-level) --[/]"
        )
        ctx.io.output_markup("")

    sorted_sources = sorted(groups, key=lambda s: _SOURCE_ORDER.get(s, 3))
    first = True
    for source in sorted_sources:
        label = _SOURCE_LABELS.get(source, f"{source} Plugins")
        if not first:
            ctx.io.output_markup("")
        first = False
        ctx.io.output_markup(f"[{_SEP}]-- {escape_markup(label)} --[/]")
        for cmd_name, plugin in sorted(groups[source], key=lambda x: x[0]):
            ctx.io.output_markup(_landscape_row(prefix, cmd_name, plugin, cmd_w))

    if directives:
        ctx.io.output_markup("")
        ctx.io.output_markup(f"[{_SEP}]-- Directives --[/]")
        for directive in directives:
            cmd_markup = _pad_markup(f"  [{_CMD}]{escape_markup(directive.name)}[/]", cmd_w + 2)
            ctx.io.output_markup(f"{cmd_markup}  {escape_markup(directive.help)}")

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

    from termapy.profile import profile_command_view

    if profile_command_view(ctx.ns("active_profile")):
        ctx.io.output_markup("")
        _render_target(ctx, cmd_w)

    # Footer: teach the two other modes. Single dim line, always emitted.
    ctx.io.output_markup("")
    prefix_markup = escape_markup(prefix)
    ctx.io.output_markup(
        f"[{_SEP}]Use {prefix_markup}help <term> to find a command, "
        f"{prefix_markup}search <word> for a deep search.[/]"
    )
    # The landscape view's natural scriptable summary is the total
    # command count -- scripts can compare it across cfgs to detect
    # plugin drift, e.g., ``$(N) <- /help.quiet`` then arithmetic.
    return CmdResult.ok(value=str(len(all_plugins)))


# ── /help.target, /help.run, /help.plugin, /help.dev ─────────────────────────


def _handler_target(ctx: PluginContext, args: str) -> CmdResult:
    """Show only device commands from the active profile."""
    from termapy.profile import profile_command_view

    target_cmds = profile_command_view(ctx.ns("active_profile"))
    if not target_cmds:
        ctx.io.result(
            "No device commands available.  /profile.load <path> "
            "or /profile.load cmd=<command> to load one."
        )
        return CmdResult.ok(value="")
    _render_target(ctx)
    ctx.io.result(f"{len(target_cmds)} device commands.")
    return CmdResult.ok(value="\n".join(sorted(target_cmds)))


def _render_target(ctx: PluginContext, cmd_w: int | None = None) -> None:
    """Render the device command table from the active profile.

    Device commands are help-only listings of a connected device's own
    commands.  Unlike REPL plugins, the user needs to see the args
    syntax inline here because there's no separate ``/help <name>``
    drill-down for device commands.
    """
    from termapy.profile import profile_command_view

    target_cmds = profile_command_view(ctx.ns("active_profile"))
    names = list(target_cmds)
    if cmd_w is None:
        cmd_w = _compute_cmd_w(names, "")
    ctx.io.output_markup(f"[{_SEP}]-- Target Device --[/]")
    for cmd_name in sorted(names):
        tc = target_cmds[cmd_name]
        cmd_markup = _pad_markup(f"  [{_CMD}]{escape_markup(tc.name)}[/]", cmd_w + 2)
        synopsis_markup = f" {_args_markup(tc.args)}" if tc.args else ""
        ctx.io.output_markup(f"{cmd_markup}{synopsis_markup}  {escape_markup(tc.help)}")


def _script_description(path: Path) -> str:
    """The script's docstring summary -- the same one ``/run.list`` shows.

    One extractor (``run_docstring.extract_docstring``) for both listings;
    this used to have its own rule (summary only if line 2 was BLANK), so
    a docstring whose second line was a ``#`` continuation showed a
    summary in ``/run.list`` and nothing here.
    """
    return extract_docstring(path)[0]


def _render_scripts(
    ctx: PluginContext, scripts: list, prefix: str, cmd_w: int = _MAX_CMD_COL
) -> None:
    """Render the scripts table with matched column width."""
    ctx.io.output_markup(f"[{_SEP}]-- Scripts --[/]")
    for path in scripts:
        cmd_markup = _pad_markup(
            f"  [{_CMD}]{escape_markup(f'{prefix}run {path.stem}')}[/]", cmd_w + 2
        )
        ctx.io.output_markup(f"{cmd_markup}  {escape_markup(_script_description(path))}")


def _handler_run(ctx: PluginContext, args: str) -> CmdResult:
    """List available .run scripts with descriptions."""
    scripts_dir = ctx.fs.scripts_dir
    if not scripts_dir.is_dir():
        ctx.io.result("No scripts directory.")
        return CmdResult.ok(value="")
    scripts = sorted(scripts_dir.glob("*.run"))
    if not scripts:
        ctx.io.result("No .run scripts found.")
        return CmdResult.ok(value="")
    prefix = ctx.prefix
    _render_scripts(ctx, scripts, prefix)
    ctx.io.result(f"{len(scripts)} scripts.")
    return CmdResult.ok(value="\n".join(script.stem for script in scripts))


def _handler_plugin(ctx: PluginContext, args: str) -> CmdResult:
    """List loaded plugins grouped by source.

    Slim row layout that matches ``/help`` landscape (name + one-liner
    only) so users see one visual idiom across every listing context.
    """
    all_plugins = ctx.internal.plugins
    if not all_plugins:
        ctx.io.result("No plugins loaded.")
        return CmdResult.ok(value="")
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
        if "." not in cmd_name and plugin.needs.block_until and not getattr(plugin, "hidden", False)
    ]
    all_names = [n for names in groups.values() for n, _ in names] + [n for n, _ in script_only]
    prefix = ctx.prefix
    cmd_w = _compute_cmd_w(all_names, prefix)
    sorted_sources = sorted(groups, key=lambda s: _SOURCE_ORDER.get(s, 3))
    first = True
    total = 0
    for source in sorted_sources:
        label = _SOURCE_LABELS.get(source, f"{source} Plugins")
        if not first:
            ctx.io.output_markup("")
        first = False
        ctx.io.output_markup(f"[{_SEP}]-- {escape_markup(label)} --[/]")
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
    return CmdResult.ok(value=str(total))


def _handler_dev(ctx: PluginContext, args: str) -> CmdResult:
    """Show a command handler's Python docstring (developer info)."""
    name = args.strip() if isinstance(args, str) else ""
    if not name:
        raise UsageError()
    return _show_command_help(ctx, name, dev_mode=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="help",
    args="{term}",
    flags={
        "--mcp": "Show only commands visible to the MCP catalog.",
    },
    help="Print the command landscape or look up a specific command.",
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
            help="Print only imported target device commands.",
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
            help="Print a command handler's Python docstring.",
            handler=_handler_dev,
        ),
    },
)
