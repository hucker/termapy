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

from termapy.builtins.commands.grep import find_matches
from termapy.defaults import cmd_prefix
from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Module-level search state ────────────────────────────────────────────────


@dataclass
class _Active:
    """Active search session.  Module-level so it persists across dispatches."""

    pattern: str
    matches: list[tuple[int, str]] = field(default_factory=list)
    current: int = 0  # 0-based index into matches; meaningless when matches=[]
    # The full scrollback at /find time.  Stashed so the UI's
    # _update_find_bar can build the highlighted frozen view from
    # a single snapshot instead of querying screen text again.
    scrollback_text: str = ""


_active: _Active | None = None


# Per-session cap on how many matches a single /find may carry.
# A pattern that matches more than this is almost certainly a
# /grep request and the frozen-view UX collapses to noise.
# Override at runtime via /find.max_count <N>.
_DEFAULT_MAX_COUNT = 100
_max_count: int = _DEFAULT_MAX_COUNT


def current_state() -> dict | None:
    """Snapshot for the TUI to render.  ``None`` means hide the bar.

    Carries the full match list and the captured scrollback text so
    the UI can build the highlighted frozen-view in one pass without
    asking the plugin for more data.
    """
    if _active is None:
        return None
    total = len(_active.matches)
    line_no, snippet = (
        _active.matches[_active.current] if total else (None, "")
    )
    return {
        "pattern": _active.pattern,
        "total": total,
        "matches": list(_active.matches),
        "scrollback_text": _active.scrollback_text,
        "index": _active.current if total else -1,
        "line_no": line_no,
        "snippet": snippet,
    }


def _refresh(ctx: PluginContext) -> None:
    """Push current state to the TUI's FindBar (no-op in CLI/MCP)."""
    cb = ctx.internal.update_find_bar
    if cb is not None:
        cb(current_state())


# ── Handlers ─────────────────────────────────────────────────────────────────


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """``/find <pattern>`` -- start a search, or close one with no args.

    No args closes any active search (same as /find.clear).  With a
    pattern, computes matches against the current scrollback and
    seeks to the first one.  Identical regex semantics to /grep.
    """
    global _active
    pattern = args.strip()
    if not pattern:
        _active = None
        _refresh(ctx)
        return CmdResult.ok(value="closed")

    prefix = cmd_prefix(ctx.cfg)
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
                msg="Find not available: no scrollback in CLI mode "
                "(try /grep instead)",
            )
        if "Pattern required" in err:
            raise UsageError("Pattern required")
        return CmdResult.fail(msg=err)

    # Per-session cap so a pattern like ``/find .`` doesn't try to
    # highlight 10K lines.  Override with /find.max_count <N>.
    if len(matches) > _max_count:
        return CmdResult.fail(
            msg=(
                f"Too many matches ({len(matches)} > {_max_count}).  "
                f"Refine the pattern, raise the cap with "
                f"`{prefix}find.max_count {len(matches) + 100}`, or "
                f"use {prefix}grep to print them all."
            ),
        )

    _active = _Active(
        pattern=pattern, matches=matches, current=0,
        scrollback_text=text,
    )
    _refresh(ctx)
    return CmdResult.ok(value=str(len(matches)))


def _step(ctx: PluginContext, delta: int) -> CmdResult:
    """Walk one match forward (+1) or back (-1), wrapping at the ends."""
    if _active is None or not _active.matches:
        return CmdResult.fail(msg="No active find.")
    _active.current = (_active.current + delta) % len(_active.matches)
    _refresh(ctx)
    return CmdResult.ok(value=str(_active.current + 1))


def _handler_next(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.next`` -- step to the next match (wraps at end)."""
    return _step(ctx, +1)


def _handler_prev(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.prev`` -- step to the previous match (wraps at start)."""
    return _step(ctx, -1)


def _handler_clear(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.clear`` -- close the find bar, drop the match list."""
    global _active
    _active = None
    _refresh(ctx)
    return CmdResult.ok(value="closed")


def _handler_max_count(ctx: PluginContext, args: str) -> CmdResult:
    """``/find.max_count <N>`` -- raise or lower the per-session cap.

    Bare invocation reports the current value.  Otherwise the arg
    must be a positive integer.  Setting is in-memory only; resets
    to ``_DEFAULT_MAX_COUNT`` on next termapy launch.
    """
    global _max_count
    raw = args.strip()
    if not raw:
        ctx.io.result(f"/find.max_count = {_max_count}")
        return CmdResult.ok(value=str(_max_count))
    try:
        n = int(raw)
    except ValueError:
        raise UsageError(
            f"Invalid count: {raw!r}  (current = {_max_count})"
        ) from None
    if n < 1:
        return CmdResult.fail(msg="Max count must be >= 1")
    _max_count = n
    ctx.io.result(f"/find.max_count = {n}")
    return CmdResult.ok(value=str(n))


_LONG_HELP = """\
Search the scrollback for matching lines and navigate them
interactively.  Matched lines are highlighted (reverse video) in a
frozen snapshot of the scrollback; arrow buttons in the bottom row
step through them.

Usage:
  /find <pattern>           Start a search; show the highlighted view.
  /find                     Close the search.
  /find.next                Step to the next match (wraps at end).
  /find.prev                Step to the previous match (wraps at start).
  /find.clear               Same as /find with no args.
  /find.max_count <N>       Raise/lower per-session match cap (default 100).
  /find.max_count           Show the current cap.

The search regex is case-insensitive (same as /grep).  Matches are
computed once at /find time; new lines arriving on the live log
automatically dismiss the frozen view -- rerun /find to refresh
against the latest scrollback.

Requires the TUI -- the highlighted-view widget is what makes this
useful.  In CLI / MCP modes, use /grep instead.

See also: /grep (print all matches in one listing), /search (search
command help instead of scrollback).
"""


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="find",
    args="{pattern}",
    help="Navigate scrollback matches with an interactive find bar.",
    long_help=_LONG_HELP,
    handler=_handler,
    needs=CapabilitySet(interactive=True),
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
            help="Show or set per-session match cap (default 100).",
            handler=_handler_max_count,
        ),
    },
)
