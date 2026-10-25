"""Built-in plugin: forgiving /help with man-page detail view."""

from __future__ import annotations

import inspect
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.folders import FOLDER_PATTERNS, RUN
from termapy.help_dynamic import is_state_line
from termapy.markup import markup_to_plain, match_parts, underline_markup
from termapy.plugins import (
    ENVIRONMENTS,
    REQUIREMENT_HINTS,
    CapabilitySet,
    CmdResult,
    Command,
    UsageError,
    canonical_flags,
    escape_markup,
    interpolate_help,
    resolve_long_help,
    synopsis_markup,
    synopsis_pieces,
)
from termapy.plugins.command import SYNOPSIS_OPTIONAL_STYLE, SYNOPSIS_REQUIRED_STYLE
from termapy.plugins.params import render_parameters_block
from termapy.plugins.views import Link, Piece, TableView
from termapy.run_docstring import extract_docstring

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

# ── Constants ────────────────────────────────────────────────────────────────

_LABEL_RE = re.compile(r"^(\s*)(\w+:)(.*)")

# Colors for help output.  The synopsis colors are the declaration's own
# (``plugins.command``), reused here for the flag and capability rows.
_CMD = "cyan"  # command names
_OPT = SYNOPSIS_OPTIONAL_STYLE  # {optional} args, flag names, a met capability
_REQ = SYNOPSIS_REQUIRED_STYLE  # <required> args, a missing capability
_SEP = "dim"  # section separators and hints
_SRC = "dim"  # source labels

# Cap the command column in listings so a pathologically long plugin name
# can't shove the help column off the right edge.
_MAX_CMD_COL = 28

# The rows a help page's list shows, newest first; the rest are a click away.
_HELP_LIST_ROWS = 10

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


def _long_help_line_markup(line: str) -> str:
    """A ``long_help`` line: a ``green()`` state line passes through, prose is escaped."""
    return line if is_state_line(line) else escape_markup(line)


def _pad_markup(markup: str, width: int) -> str:
    """Pad a markup string to a visible width."""
    return markup + " " * max(0, width - len(markup_to_plain(markup)))


def _help_link(name: str) -> Link:
    """A click on a command's name opens its help page: reading help changes nothing."""
    return Link(f"help {name}")


def _try_link(name: str) -> Link:
    """A click on a command's name on its own page fills the input line, never runs it.

    Trying the command is then one Enter away, and nothing runs by
    accident: the safety tier gates only MCP, and in the TUI
    ``/cap.delete <name>`` deletes a capture without asking.  The
    frontend adds the space after it.
    """
    return Link(name, run=False)


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


# ── Capability rendering ─────────────────────────────────────────────────────


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
) -> list[Piece]:
    """One row of the clean name + one-liner layout, as pieces.

    The command's name links to its help page (registry
    ``links-are-data``); where nothing can click, the pieces are the
    markup this row always was.  Pass ``needles`` only in search-result
    contexts; underlining is the "here's why this matched" affordance and
    does not belong on the no-args landscape or exact-match man page.
    """
    help_text = interpolate_help(plugin.help, prefix)
    link = _help_link(name)
    pieces = [Piece("  "), Piece(prefix, link, _CMD)]
    pieces += [
        Piece(part, link, f"{_CMD} underline" if matched else _CMD)
        for part, matched in match_parts(name, needles)
    ]
    pad = max(0, cmd_w + 2 - (2 + len(prefix) + len(name)))
    pieces.append(Piece(" " * pad + "  "))
    pieces += [
        Piece(part, None, "underline" if matched else "")
        for part, matched in match_parts(help_text, needles)
    ]
    return pieces


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


def _bare_pieces(plugin, name: str, prefix: str) -> list[Piece]:
    """What a bare invocation does, from ``picker=`` and ``bare_sub=``; ``[]`` when undeclared.

    The one wording for every command (registry ``bare-declared``), in
    the dispatcher's order -- the picker where the host has one, then the
    subcommand: ``bare: opens the Run picker in the TUI; elsewhere runs
    /run.list``, ``bare: runs /mem.info``.  A click does what the line
    says: the picker's name runs the bare command, which opens it, as its
    button does, and the subcommand runs itself.  Safe to run because a
    bare form only ever opens or shows something (pinned by
    ``test_a_bare_form_only_opens_or_shows``).
    """
    sub: list[Piece] = []
    if plugin.bare_sub:
        sub_name = f"{name}.{plugin.bare_sub}"
        sub = [Piece(prefix + sub_name, Link(sub_name))]
    if plugin.picker:
        picker = [
            Piece("bare: opens the "),
            Piece(f"{plugin.picker.capitalize()} picker", Link(name)),
            Piece(" in the TUI"),
        ]
        return [*picker, Piece("; elsewhere runs "), *sub] if sub else picker
    return [Piece("bare: runs "), *sub] if sub else []


def _render_man_page(ctx: PluginContext, name: str, plugin, dev_mode: bool = False) -> None:
    """Render a command's full detail view in man-page format.

    Sections: NAME, SYNOPSIS (if args), DESCRIPTION, FLAGS (if any),
    REQUIRED CAPABILITIES (if restrictive caps), SUBCOMMANDS (if children), SEE ALSO
    (if siblings/parent exist), then the ``help_list`` table (if declared).
    Empty sections are skipped so the page stays dense.

    No underlining on this path -- it's an exact-match detail view,
    nothing is a "search hit" to highlight.
    """
    prefix = ctx.prefix
    plugins = ctx.internal.plugins
    # The command's name, on each line that starts with it: a click
    # fills the input line with it (registry links-are-data).
    command = Piece(prefix + name, _try_link(name), _CMD)

    # NAME ────────────────────────────────────────────────────────────────────
    ctx.io.output_markup(_section_markup("NAME"))
    help_line = interpolate_help(plugin.help, prefix)
    ctx.io.output_markup_segments([Piece("  "), command, Piece(f" - {help_line}")])

    # SYNOPSIS ────────────────────────────────────────────────────────────────
    # A declared bare form gets its own line, built from the declaration,
    # so every command's bare behavior reads the same way.
    bare = _bare_pieces(plugin, name, prefix)
    if plugin.args or bare:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("SYNOPSIS"))
        if plugin.args:
            ctx.io.output_markup_segments(
                [Piece("  "), command, Piece(" "), *synopsis_pieces(plugin.args)]
            )
        if bare:
            ctx.io.output_markup_segments([Piece("  "), command, Piece(" -- "), *bare])

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
    rows = canonical_flags(plugin.flags)
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
        effective = _effective_capabilities(ctx)
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("REQUIRED CAPABILITIES"))
        for cap_name, hint in required:
            cap_markup = escape_markup(cap_name)
            hint_markup = escape_markup(interpolate_help(hint, ctx.prefix))
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
                ctx.io.output_markup_segments(_landscape_row(prefix, child_name, child, cmd_w))

    # SEE ALSO ────────────────────────────────────────────────────────────────
    see = _siblings_for_see_also(name, plugins)
    if see:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("SEE ALSO"))
        refs = [Piece("  ")]
        for index, ref in enumerate(see):
            if index:
                refs.append(Piece(", "))
            refs.append(Piece(prefix + ref, _help_link(ref), _CMD))
        ctx.io.output_markup_segments(refs)

    # Source annotation for non-built-in plugins (preserved from old renderer).
    if plugin.source not in ("built-in", "app"):
        ctx.io.output_markup("")
        ctx.io.output_markup(f"  [{_SRC}](source: {escape_markup(plugin.source)})[/]")

    _render_help_list(ctx, name, plugin)


def _effective_capabilities(ctx: PluginContext) -> CapabilitySet:
    """What a command can do right now: the set the dispatch gate checks."""
    if ctx.internal.effective_capabilities is not None:
        return ctx.internal.effective_capabilities()
    return ctx.capabilities


def _render_help_list(ctx: PluginContext, name: str, plugin) -> None:
    """End the page with the table of the list ``help_list`` names, indented like a section.

    ``/help cfg`` shows the configs and ``/help run`` the scripts: the
    command's own list, so its rows, and what a click on a name does
    (registry ``links-are-data``), are that list's.  Its lines are page
    lines, each piece in the table line's color, so they sit in the
    page's column in every frontend.  A folder of hundreds of
    screenshots shows its newest ``_HELP_LIST_ROWS``, the title still
    counting them all, then a link to the whole list.  Left off when the
    list cannot run now (``/help run`` with no config loaded) or holds
    nothing.
    """
    list_name = f"{name}.{plugin.help_list}"
    child = ctx.internal.plugins.get(list_name) if plugin.help_list else None
    if child is None or child.needs.missing_from(_effective_capabilities(ctx)):
        return
    view = child.handler(ctx, "").view
    if not isinstance(view, TableView) or not view.records:
        return
    shown = replace(view, records=view.records[:_HELP_LIST_ROWS], total=len(view.records))
    ctx.io.output_markup("")
    # The heading is the list's noun, plural: CONFIGS, SCRIPTS, SCREENSHOTS,
    # LOGS, and FILES for cap/ (build_folder_subcommands' default noun).
    ctx.io.output_markup(_section_markup(f"{view.noun}s".upper()))
    for segments, color in shown.segment_lines():
        pieces = [piece._replace(style=piece.style or color) for piece in segments]
        ctx.io.output_markup_segments([Piece("  "), *pieces])
    more = len(view.records) - len(shown.records)
    if more:
        ctx.io.output_markup_segments(
            [
                Piece("  "),
                Piece(f"... {more} more: ", None, _SEP),
                Piece(ctx.prefix + list_name, Link(list_name), _CMD),
            ]
        )


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
        ctx.io.output_markup(f"  [{_CMD}]{name_markup}[/] {synopsis_markup(tc.args)}")

    # DESCRIPTION ─────────────────────────────────────────────────────────────
    if tc.long_help:
        ctx.io.output_markup("")
        ctx.io.output_markup(_section_markup("DESCRIPTION"))
        for line in tc.long_help.strip().splitlines():
            ctx.io.output_markup(f"  {escape_markup(line)}")

    # FLAGS ───────────────────────────────────────────────────────────────────
    # The profile-command view carries a ``flags`` mapping of the same
    # shape as a Command's, so the rows render the same way.
    rows = canonical_flags(tc.flags)
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
    ctx.io.output_markup(f"Candidates matching [{_CMD}]{underline_markup(term, words)}[/]:")
    cmd_w = _compute_cmd_w(names, prefix)
    for name in names:
        plugin = plugins[name]
        ctx.io.output_markup_segments(_landscape_row(prefix, name, plugin, cmd_w, needles=words))


# ── Extension hook: extra man-page section ───────────────────────────────────


# ── Main /help lookup ────────────────────────────────────────────────────────


def _command_name(ctx: PluginContext, text: str) -> str:
    """The command ``text`` names, without the REPL prefix it may be typed with.

    The prefix is how a command is typed, not part of its name, so
    ``/help /cfg.loaded`` reads as ``/help cfg.loaded``.  It is the live
    ``cmd_prefix`` (``ctx.prefix``), never assumed to be ``/``.  A device
    command is kept as written: its own name may begin with the same text
    (``$GPGGA`` under a ``$`` prefix).
    """
    from termapy.profile import profile_command_view

    prefix = ctx.prefix
    if not prefix or not text.startswith(prefix):
        return text
    if text in profile_command_view(ctx.ns("active_profile")):
        return text
    return text[len(prefix) :]


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
    name = _command_name(ctx, args.strip() if isinstance(args, str) else "")
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
            ctx.io.output_markup_segments(_landscape_row(prefix, cmd_name, plugin, cmd_w))

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
            ctx.io.output_markup_segments(_landscape_row(prefix, cmd_name, plugin, cmd_w))

    scripts_dir = ctx.fs.scripts_dir
    if scripts_dir.is_dir():
        scripts = sorted(scripts_dir.glob(FOLDER_PATTERNS[RUN]))
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
            f"No device commands available.  {ctx.prefix}profile.load <path> "
            f"or {ctx.prefix}profile.load cmd=<command> to load one."
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
        args_markup = f" {synopsis_markup(tc.args)}" if tc.args else ""
        ctx.io.output_markup(f"{cmd_markup}{args_markup}  {escape_markup(tc.help)}")


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
    scripts = sorted(scripts_dir.glob(FOLDER_PATTERNS[RUN]))
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
            ctx.io.output_markup_segments(_landscape_row(prefix, cmd_name, plugin, cmd_w))
            total += 1

    if script_only:
        ctx.io.output_markup("")
        ctx.io.output_markup(f"[{_SEP}]-- Script Commands (.run files only) --[/]")
        for cmd_name, plugin in sorted(script_only, key=lambda x: x[0]):
            ctx.io.output_markup_segments(_landscape_row(prefix, cmd_name, plugin, cmd_w))
            total += 1

    ctx.io.result(f"{total} commands.")
    return CmdResult.ok(value=str(total))


def _handler_dev(ctx: PluginContext, args: str) -> CmdResult:
    """Show a command handler's Python docstring (developer info)."""
    name = _command_name(ctx, args.strip() if isinstance(args, str) else "")
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

  {prefix}help              landscape of all commands (name + one-liner).
  {prefix}help <term>       exact match -> detail; otherwise a candidate list
                     by substring match on name + short help.
  {prefix}help --mcp        landscape filtered to MCP-visible commands only
                     (same set the LLM sees via {prefix}mcp.catalog).
  {prefix}search <word>     deep search across names, args, flags, long help.

{prefix}help is forgiving: if you can't remember the full name, it lists
candidates, and a name typed with its prefix ({prefix}help {prefix}cfg)
reads as the name alone. Typing {prefix}help with zero matches points you at
{prefix}search.""",
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
