"""Built-in plugin: navigate matches in the scrollback.

``/find <pattern>`` searches the scrollback like ``/grep`` does --
same case-insensitive regex semantics, same ANSI-stripped display --
but instead of printing a list of matches it scrolls the viewport
to the first match and shows an ephemeral FindBar in the TUI's
bottom row.  ``/find.next`` and ``/find.prev`` step through the
remaining matches; ``/find.clear`` or bare ``/find`` closes the bar.

The bar is rendered by the TUI; the plugin only stores match state
and asks the TUI to refresh via ``ctx.internal.update_find_bar``.
CLI/MCP hosts leave that callback ``None`` so this command becomes
a no-op there (users have ``/grep`` for the same scrollback search,
which IS supported in non-TUI hosts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError
from termapy.scripting import find_matches

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Search state, in ctx.ns("find") ──────────────────────────────────────────


@dataclass
class _Active:
    """Active search session, kept in the namespace so it persists across dispatches."""

    pattern: str
    matches: list[tuple[int, str]] = field(default_factory=list)
    current: int = 0  # 0-based index into matches; meaningless when matches=[]
    # The full scrollback at /find time.  Stashed so the UI's
    # _update_find_bar can build the highlighted frozen view from
    # a single snapshot instead of querying screen text again.
    scrollback_text: str = ""


# The namespace holds ``active`` (absent when no search is open) and
# ``max_count`` (absent until /find.max_count sets it), so emptying it, as a
# config load does, ends the search and restores the cap.
_FIND_NS = "find"


# Per-session cap on how many matches a single /find may carry.
# A pattern that matches more than this is almost certainly a
# /grep request and the frozen-view UX collapses to noise.
# Override at runtime via /find.max_count <N>.
_DEFAULT_MAX_COUNT = 100


def _max_count(ctx: PluginContext) -> int:
    """The match cap: what ``/find.max_count`` set, or the default."""
    return ctx.ns(_FIND_NS).get("max_count", _DEFAULT_MAX_COUNT)


def current_state(ctx: PluginContext) -> dict | None:
    """Snapshot for the TUI to render.  ``None`` means hide the bar.

    Carries the full match list and the captured scrollback text so
    the UI can build the highlighted frozen-view in one pass without
    asking the plugin for more data.

    Args:
        ctx: Plugin context holding the search.
    """
    active = ctx.ns(_FIND_NS).get("active")
    if active is None:
        return None
    total = len(active.matches)
    line_no, snippet = active.matches[active.current] if total else (None, "")
    return {
        "pattern": active.pattern,
        "total": total,
        "matches": list(active.matches),
        "scrollback_text": active.scrollback_text,
        "index": active.current if total else -1,
        "line_no": line_no,
        "snippet": snippet,
    }


def _refresh(ctx: PluginContext) -> None:
    """Push current state to the TUI's FindBar (no-op in CLI/MCP)."""
    cb = ctx.internal.update_find_bar
    if cb is not None:
        cb(current_state(ctx))


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """``/find <pattern>`` -- start a search, or close one with no args.

    No args closes any active search (same as /find.clear).  With a
    pattern, computes matches against the current scrollback and
    seeks to the first one.  Identical regex semantics to /grep.
    """
    pattern = args.strip()
    if not pattern:
        dismiss(ctx)
        return CmdResult.ok(value="closed")

    prefix = ctx.prefix
    find_cmd = f"{prefix}find"

    def _is_find_noise(line: str) -> bool:
        # Skip the user's echoed /find command line so we don't
        # match our own input.  /find itself writes nothing to
        # scrollback, so there's no other noise to filter.
        return find_cmd in line

    text = ctx.ui.get_screen_text()
    matches, err = find_matches(text, pattern, is_noise=_is_find_noise)
    if err is not None:
        if "No scrollback" in err:
            return CmdResult.fail(
                msg=f"Find not available: no scrollback in CLI mode (try {ctx.prefix}grep instead)",
            )
        if "Pattern required" in err:
            raise UsageError("Pattern required")
        return CmdResult.fail(msg=err)

    # Per-session cap so a pattern like ``/find .`` doesn't try to
    # highlight 10K lines.  Override with /find.max_count <N>.
    max_count = _max_count(ctx)
    if len(matches) > max_count:
        return CmdResult.fail(
            msg=(
                f"Too many matches ({len(matches)} > {max_count}).  "
                f"Refine the pattern, raise the cap with "
                f"`{prefix}find.max_count {len(matches) + 100}`, or "
                f"use {prefix}grep to print them all."
            ),
        )

    ctx.ns(_FIND_NS)["active"] = _Active(
        pattern=pattern,
        matches=matches,
        current=0,
        scrollback_text=text,
    )
    _refresh(ctx)
    return CmdResult.ok(value=len(matches))


def _step(ctx: PluginContext, delta: int) -> CmdResult:
    """Walk one match forward (+1) or back (-1), wrapping at the ends."""
    active = ctx.ns(_FIND_NS).get("active")
    if active is None or not active.matches:
        return CmdResult.fail(msg="No active find.")
    active.current = (active.current + delta) % len(active.matches)
    _refresh(ctx)
    return CmdResult.ok(value=active.current + 1)


def _handler_next(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.next`` -- step to the next match (wraps at end)."""
    return _step(ctx, +1)


def _handler_prev(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.prev`` -- step to the previous match (wraps at start)."""
    return _step(ctx, -1)


def dismiss(ctx: PluginContext) -> None:
    """Close the find bar and drop the match list.

    The published entry point for this action.  ``/find.clear`` is the
    command form; ``app.py`` calls this directly when live data arrives on
    ``#output``, which is the user's signal that they are done with the
    frozen view.  Kept public so the app never has to reach for a private
    handler.

    Args:
        ctx: Plugin context, used to refresh the find bar.
    """
    ctx.ns(_FIND_NS).pop("active", None)
    _refresh(ctx)


def on_config_load(ctx: PluginContext) -> None:
    """A load already emptied the namespace, so the bar only has to hear the search is gone."""
    _refresh(ctx)


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.clear`` -- close the find bar, drop the match list."""
    dismiss(ctx)
    return CmdResult.ok(value="closed")


def _handler_max_count(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.max_count <N>`` -- raise or lower the per-session cap.

    Bare invocation reports the current value.  Otherwise the arg
    must be a positive integer.  Setting is in-memory only; goes back
    to ``_DEFAULT_MAX_COUNT`` on the next config load.
    """
    max_count = _max_count(ctx)
    raw = args.strip()
    if not raw:
        ctx.io.result(f"{ctx.prefix}find.max_count = {max_count}")
        return CmdResult.ok(value=max_count)
    try:
        n = int(raw)
    except ValueError:
        raise UsageError(f"Invalid count: {raw!r}  (current = {max_count})") from None
    if n < 1:
        return CmdResult.fail(msg="Max count must be >= 1")
    ctx.ns(_FIND_NS)["max_count"] = n
    ctx.io.result(f"{ctx.prefix}find.max_count = {n}")
    return CmdResult.ok(value=n)


_LONG_HELP = """\
Matched lines are highlighted (reverse video) in a
frozen snapshot of the scrollback; arrow buttons in the bottom row
step through them.

Usage:
  {prefix}find <pattern>           Start a search; show the highlighted view.
  {prefix}find                     Close the search.
  {prefix}find.next                Step to the next match (wraps at end).
  {prefix}find.prev                Step to the previous match (wraps at start).
  {prefix}find.clear               Same as {prefix}find with no args.
  {prefix}find.max_count <N>       Raise/lower the match cap until a config load (default 100).
  {prefix}find.max_count           Show the current cap.

The search regex is case-insensitive (same as {prefix}grep).  Matches are
computed once at {prefix}find time; new lines arriving on the live log
automatically dismiss the frozen view -- rerun {prefix}find to refresh
against the latest scrollback.

Requires the TUI -- the highlighted-view widget is what makes this
useful.  In CLI / MCP modes, use {prefix}grep instead.

See also: {prefix}grep (print all matches in one listing), {prefix}search (search
command help instead of scrollback).
"""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="terminal",
    name="find",
    args="{pattern}",
    help="Navigate scrollback matches with an interactive find bar.",
    intro="Search the scrollback for matching lines and navigate them interactively.",
    long_help=_LONG_HELP,
    handler=_handler,
    needs=CapabilitySet.INTERACTIVE,
    sub_commands={
        "next": Command(
            help="Step to the next find match.",
            handler=_handler_next,
        ),
        "prev": Command(
            help="Step to the previous find match.",
            handler=_handler_prev,
        ),
        "clear": Command(
            help="Close the find bar.",
            handler=_handler_clear,
        ),
        "max_count": Command(
            args="{N}",
            help="Show or set the match cap until the next config load (default 100).",
            handler=_handler_max_count,
            state=True,
        ),
    },
)
