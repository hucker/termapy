"""Built-in plugin: /search -- Google-style deep command search.

Complement to ``/help``. Where ``/help <term>`` matches on name + short
help only, ``/search`` hits every searchable field (name, short help,
args, flags, long_help, and handler docstrings with ``--dev``). Multi-
term queries AND-match; a leading ``-`` on a term excludes matches. If
the term contains regex metacharacters it's compiled as a regex.

This used to live as ``/help.search``; promoted to top-level so it's
easier to reach in the "I'm old and can't remember" case.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

# Reuse only the thin rendering helpers from the help plugin. Everything
# search-specific (grammar, field extraction, highlighter) lives below.
from termapy.builtins.commands.help import (
    _canonical_flags,
    _color_args,
    _underline,
)
from termapy.plugins import (
    CmdResult,
    Command,
    UsageError,
    interpolate_help,
    resolve_long_help,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Constants ────────────────────────────────────────────────────────────────

# Characters that flip the search into regex mode.
_REGEX_META_RE = re.compile(r"[.^$*+?()\[\]{}|\\]")

# Cap rendered results so a too-broad search doesn't flood the terminal.
_MAX_SEARCH_RESULTS = 50

# Context window around a match in long-text fields (long_help, docstring).
_SEARCH_CONTEXT = 40

# Fields searched, in priority order for tier ranking. An exact flag-name
# hit is nearly as specific as a command-name hit, so flags sits right
# after name.
_FUZZY_FIELDS = ("name", "flags", "help", "args", "long_help")

_CMD = "cyan"
_SEP = "dim"


def _indexable_commands(ctx: PluginContext) -> dict:
    """Merged view of REPL plugins plus device commands from the active profile.

    The active-profile view is built by ``profile_command_view`` which
    yields ``SimpleNamespace`` objects exposing ``help``, ``args``,
    ``flags``, ``long_help`` -- the four attributes search reads.
    No ``handler`` attribute; the ``--dev`` docstring branch in
    ``_field_text`` uses ``getattr(..., None)`` and degrades cleanly.

    Plugin names win on collision -- a REPL /command wearing the same
    spelling as a device command is the more specific hit.
    """
    from termapy.profile import profile_command_view

    merged = dict(profile_command_view(ctx.ns("active_profile")))
    merged.update(ctx.internal.plugins)
    return merged


# ── Grammar + matching ───────────────────────────────────────────────────────


def _parse_search_terms(query: str) -> tuple[list[str], list[str]]:
    """Split a query into positive and negative substring terms.

    Rules:
      - tokens starting with a single ``-`` followed by a non-dash char
        (e.g. ``-foo``) are *negative* terms and strip the leading dash.
      - every other token is a *positive* term, taken verbatim. This means
        ``--flag`` stays literal and matches declared flag names.

    Returns ``(positives, negatives)`` with the leading dash stripped from
    negatives. Either list may be empty.
    """
    positives: list[str] = []
    negatives: list[str] = []
    for tok in query.split():
        if len(tok) > 1 and tok[0] == "-" and tok[1] != "-":
            negatives.append(tok[1:])
        else:
            positives.append(tok)
    return positives, negatives


def _field_text(name: str, plugin, field: str,
                ctx: PluginContext | None = None) -> str:
    """Return the text body for a given searchable field on one plugin.

    ``long_help`` may be a callable; when ``ctx`` is supplied we resolve
    it. Without ``ctx`` (some unit tests) a callable long_help contributes
    no searchable text. Exceptions inside a callable are caught via
    ``resolve_long_help``.
    """
    if field == "name":
        return name
    if field == "flags":
        parts: list[str] = []
        for canonical, aliases, desc in _canonical_flags(plugin):
            parts.extend([canonical, *aliases, desc])
        return " ".join(parts)
    if field == "docstring":
        return getattr(plugin.handler, "__doc__", None) or ""
    if field == "long_help":
        if ctx is not None:
            return resolve_long_help(plugin, ctx)
        lh = plugin.long_help
        return lh if isinstance(lh, str) else ""
    return getattr(plugin, field, "") or ""


def _tier_fields(include_dev: bool) -> tuple[str, ...]:
    """Return the field-priority tuple, optionally extended with docstring."""
    if include_dev:
        return (*_FUZZY_FIELDS, "docstring")
    return _FUZZY_FIELDS


def _best_tier(needle: str, name: str, plugin, fields: tuple[str, ...],
               ctx: PluginContext | None = None
               ) -> tuple[int | None, str]:
    """Return the highest-priority (tier, field) where ``needle`` appears.

    ``ctx`` is forwarded to ``_field_text`` so callable long_help is
    resolved to its current rendered string before matching.
    """
    needle = needle.lower()
    for tier, field in enumerate(fields):
        text = _field_text(name, plugin, field, ctx).lower()
        if needle in text:
            return tier, field
    return None, ""


def _fuzzy_matches(query: str, plugins: dict, include_dev: bool = False,
                   ctx: PluginContext | None = None
                   ) -> list[tuple[str, str]]:
    """Return ``(command_name, field)`` pairs for a multi-term query.

    All positive terms must appear somewhere in the searched fields; any
    match on an excluded term drops the command. Field priority (name >
    flags > help > args > long_help, plus docstring with ``include_dev``)
    comes from the *first* positive term, keeping ranking predictable
    when two terms could each hit different fields. Results sorted by
    that tier, then alphabetically.

    ``ctx`` is threaded through to ``_field_text`` so callable long_help
    values are resolved before matching.
    """
    positives, negatives = _parse_search_terms(query)
    if not positives:
        return []
    fields = _tier_fields(include_dev)
    ranked: list[tuple[int, str, str]] = []
    for name, plugin in plugins.items():
        if getattr(plugin, "hidden", False):
            continue
        tier, field = _best_tier(positives[0], name, plugin, fields, ctx)
        if tier is None:
            continue
        if not all(
            _best_tier(t, name, plugin, fields, ctx)[0] is not None
            for t in positives[1:]
        ):
            continue
        if any(
            _best_tier(t, name, plugin, fields, ctx)[0] is not None
            for t in negatives
        ):
            continue
        ranked.append((tier, name, field))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(name, field) for _, name, field in ranked]


# ── Rendering ────────────────────────────────────────────────────────────────


def _highlight(text: str, span: tuple[int, int]) -> str:
    """Return a context snippet around ``span`` with the match wrapped in yellow.

    User-supplied text (e.g. a long_help that contains its own Rich
    markup like ``[green]Current X = Y[/]``) is escaped before being
    spliced into the final string -- without that, a snippet boundary
    can land between an opening tag and its closer, leaving an orphan
    ``[/]`` that breaks Rich's parser.
    """
    from rich.markup import escape

    start, end = span
    left = max(0, start - _SEARCH_CONTEXT)
    right = min(len(text), end + _SEARCH_CONTEXT)
    prefix = "..." if left > 0 else ""
    suffix = "..." if right < len(text) else ""
    # Collapse newlines/tabs so each hit stays on one line, then escape
    # so any literal "[...]" in the source text is treated as text, not
    # as Rich markup that would collide with our [yellow] wrapper.
    before = escape(text[left:start].replace("\n", " ").replace("\t", " "))
    hit = escape(text[start:end].replace("\n", " ").replace("\t", " "))
    after = escape(text[end:right].replace("\n", " ").replace("\t", " "))
    return f"{prefix}{before}[yellow]{hit}[/]{after}{suffix}"


def _search_fields(name: str, plugin, include_dev: bool,
                   ctx: PluginContext | None = None
                   ) -> list[tuple[str, str]]:
    """Return (field_label, field_text) pairs to search for one plugin.

    ``long_help`` may be a callable; when ``ctx`` is supplied we resolve it
    via ``resolve_long_help`` so dynamic DESCRIPTION content is searchable
    too. If ``ctx`` is None (e.g. a unit test that doesn't have one handy)
    the callable is not invoked and a callable long_help contributes no
    search text.
    """
    if ctx is not None:
        long_help_text = resolve_long_help(plugin, ctx)
    else:
        lh = plugin.long_help
        long_help_text = lh if isinstance(lh, str) else ""
    fields = [
        ("name", name),
        ("help", plugin.help or ""),
        ("args", plugin.args or ""),
        ("flags", _field_text(name, plugin, "flags")),
        ("long_help", long_help_text),
    ]
    if include_dev:
        docstring = getattr(plugin.handler, "__doc__", None) or ""
        fields.append(("docstring", docstring))
    return fields


def _render_hit(ctx: PluginContext, prefix: str, name: str, plugin,
                hit_fields: list[tuple[str, str, tuple[int, int]]],
                underlines: list[str] | None = None,
                is_target: bool = False) -> None:
    """Render a single search result: header line + per-field context sublines.

    ``is_target`` suppresses the REPL prefix and appends a dim "(target)"
    marker so device commands are visually distinguishable from plugins.
    """
    args_colored = _color_args(plugin.args) if plugin.args else ""
    help_text = interpolate_help(plugin.help, prefix)
    if underlines:
        header_name = _underline(name, underlines)
        header_help = _underline(help_text, underlines)
    else:
        header_name = name
        header_help = help_text
    arg_str = f" {args_colored}" if args_colored else ""
    shown_prefix = "" if is_target else prefix
    target_tag = f"  [{_SEP}](target)[/]" if is_target else ""
    ctx.io.output_markup(
        f"[{_CMD}]{shown_prefix}{header_name}[/]{arg_str} - {header_help}{target_tag}"
    )
    for label, text, span in hit_fields:
        if label in ("name", "help", "args"):
            continue
        ctx.io.output_markup(f"  [{_SEP}]({label})[/] {_highlight(text, span)}")


# ── Handler ──────────────────────────────────────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Deep-search command metadata for a regex or literal string.

    Searches name, short help, args, flags, and long_help. With ``--dev``
    also searches each handler's Python docstring. Regex metacharacters
    trigger regex mode; otherwise the query splits into positive/negative
    literal terms (AND match, leading ``-`` excludes).

    This command uses ``raw_args=True`` so the dispatch-level flag parser
    doesn't eat ``-exclude`` terms. ``--dev`` is parsed here instead.

    Args:
        ctx: Plugin context for the plugin registry and output.
        args: Search pattern, possibly with a ``--dev`` flag somewhere.
    """
    tokens = args.split() if isinstance(args, str) else []
    include_dev = "--dev" in tokens
    tokens = [t for t in tokens if t != "--dev"]
    if not tokens:
        raise UsageError()
    pattern = " ".join(tokens)
    prefix = ctx.prefix

    if _REGEX_META_RE.search(pattern):
        return _run_regex(ctx, pattern, include_dev, prefix)
    return _run_literal(ctx, pattern, include_dev, prefix)


def _run_regex(ctx: PluginContext, pattern: str, include_dev: bool,
               prefix: str) -> CmdResult:
    """Regex-mode search: compile once, render each hit with context snippets."""
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return CmdResult.fail(msg=f"Invalid regex: {e}")

    indexable = _indexable_commands(ctx)
    from termapy.profile import profile_command_view
    targets = profile_command_view(ctx.ns("active_profile"))
    matches: list[str] = []
    rendered = 0
    truncated = False
    for name in sorted(indexable):
        plugin = indexable[name]
        hit_fields: list[tuple[str, str, tuple[int, int]]] = []
        for label, text in _search_fields(name, plugin, include_dev, ctx):
            m = rx.search(text)
            if m:
                hit_fields.append((label, text, m.span()))
        if not hit_fields:
            continue
        matches.append(name)
        if rendered >= _MAX_SEARCH_RESULTS:
            truncated = True
            continue
        _render_hit(ctx, prefix, name, plugin, hit_fields,
                    is_target=(name in targets and name not in ctx.internal.plugins))
        rendered += 1
    return _finish(ctx, pattern, matches, truncated)


def _run_literal(ctx: PluginContext, pattern: str, include_dev: bool,
                 prefix: str) -> CmdResult:
    """Literal-mode search: multi-term AND + `-exclude` grammar, context snippets."""
    positives, _ = _parse_search_terms(pattern)
    indexable = _indexable_commands(ctx)
    from termapy.profile import profile_command_view
    targets = profile_command_view(ctx.ns("active_profile"))
    matches = _fuzzy_matches(
        pattern, indexable, include_dev=include_dev, ctx=ctx,
    )
    rendered = 0
    truncated = False
    ordered_names: list[str] = []
    for name, _field in matches:
        ordered_names.append(name)
        plugin = indexable[name]
        hit_fields: list[tuple[str, str, tuple[int, int]]] = []
        for label, text in _search_fields(name, plugin, include_dev, ctx):
            text_lc = text.lower()
            for term in positives:
                idx = text_lc.find(term.lower())
                if idx >= 0:
                    hit_fields.append(
                        (label, text, (idx, idx + len(term)))
                    )
                    break  # one span per field is enough for rendering
        if rendered >= _MAX_SEARCH_RESULTS:
            truncated = True
            continue
        _render_hit(ctx, prefix, name, plugin, hit_fields, underlines=positives,
                    is_target=(name in targets and name not in ctx.internal.plugins))
        rendered += 1
    return _finish(ctx, pattern, ordered_names, truncated)


def _finish(ctx: PluginContext, pattern: str, matches: list[str],
            truncated: bool) -> CmdResult:
    """Emit the summary line and return a scripting-friendly value."""
    if not matches:
        ctx.io.result(f"No matches for '{pattern}'.")
        return CmdResult.ok(value="")
    suffix = f" (showing first {_MAX_SEARCH_RESULTS})" if truncated else ""
    word = "matches" if len(matches) != 1 else "match"
    ctx.io.result(f"{len(matches)} {word}{suffix}.")
    return CmdResult.ok(value="\n".join(matches))


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
#
# raw_args=True because our literal grammar uses ``-term`` for exclusion,
# which would otherwise be consumed by the dispatch-level flag parser.
# The ``--dev`` switch is parsed by the handler itself.
COMMAND = Command(
    name="search",
    args="{--dev} <pattern>",
    help="Deep-search every command's metadata (name, help, args, flags, long help).",
    long_help="""\
/search hits every searchable field -- name, short help, args, flags,
long help. It's the deep counterpart to /help's forgiving lookup.

Scope: all REPL commands (plugins) plus target-device commands brought
in by /include. Device-command hits render without the / prefix and are
tagged "(target)" so they're easy to spot in results.

Grammar (literal mode, no regex metacharacters):
  /search timeout             all commands mentioning "timeout" somewhere.
  /search table crc           AND: must match both words.
  /search port baud -break    exclude matches that also mention "break".

Grammar (regex mode, auto-detected on metacharacters):
  /search ^proto\\.            commands starting with "proto."
  /search --dev ctx\\.result   also searches handler docstrings.

Returns matching command names as CmdResult.value (newline-joined),
suitable for $(VAR) <- capture in scripts.

See also: /grep (search scrollback text and print matching lines),
/find (search scrollback and navigate matches interactively).""",
    handler=_handler,
    raw_args=True,
)
