"""Legacy-command forwarding helper.

Old top-level commands that moved to a namespace (e.g. ``/echo`` ->
``/term.echo``) register a hidden forwarder built by
:func:`make_forwarder`.  The forwarder dispatches to the new command
and prints a one-time dim note the first time the legacy name is
used in a session -- so scripts keep working and users get nudged to
update.

Two rename tables back the migration tool:

  ``LEGACY_COMMANDS``   simple name renames (``/echo`` -> ``/term.echo``).
                        Populated automatically by :func:`make_forwarder`.

  ``LEGACY_REWRITES``   args-aware rewrites (``/verbose on`` ->
                        ``/term.output verbose``).  Plugins that need
                        this register entries via
                        :func:`register_legacy_rewrite`.

``/run.legacy`` consults both tables when scanning script files.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Callable

from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# Populated by make_forwarder().  Maps old command name -> new name.
# ``/run.legacy`` reads this to report or rewrite legacy usage in
# script files.
LEGACY_COMMANDS: dict[str, str] = {}


# Args-aware rewrites.  Each entry is ``(pattern, replacement)`` where
# ``pattern`` matches the body of a REPL line (after the prefix) and
# ``replacement`` follows ``re.sub`` substitution syntax (``\1`` etc.).
# Used by ``/run.legacy`` for renames that touch arguments, not just
# the command name.
LEGACY_REWRITES: list[tuple[re.Pattern[str], str]] = []


def register_legacy_rewrite(pattern: str, replacement: str) -> None:
    """Register an args-aware rewrite for ``/run.legacy``.

    Args:
        pattern: Regex matched against the prefix-stripped line.  Use
            ``\\b`` boundaries for safety -- ``r"^foo\\s+on\\b"`` not
            ``r"^foo on"``.
        replacement: ``re.sub`` replacement string.
    """
    LEGACY_REWRITES.append((re.compile(pattern), replacement))


def make_forwarder(old_name: str, new_name: str) -> Callable:
    """Return a handler that forwards ``/old_name ...`` to ``/new_name ...``.

    First call per session prints a dim deprecation note; subsequent
    calls are silent.  Also records the mapping in
    ``LEGACY_COMMANDS`` so ``/run.legacy`` can scan for it.
    """
    LEGACY_COMMANDS[old_name] = new_name

    def handler(ctx: PluginContext, args: str) -> CmdResult:
        warned = ctx.ns("legacy_warned")
        if old_name not in warned:
            warned[old_name] = True
            p = ctx.engine.prefix
            ctx.io._write(
                f"  Note: {p}{old_name} is legacy; use {p}{new_name}.",
                "yellow",
            )
        # Use engine.dispatch (bare REPL dispatch) rather than
        # ctx.dispatch (the full pipeline).  ctx.dispatch requires a
        # prefixed command -- un-prefixed input would be treated as
        # serial and sent to the device.  engine.dispatch takes the
        # name-and-args directly and routes through plugin lookup,
        # capability gates, and flag parsing.
        target = f"{new_name} {args}".strip()
        result = ctx.engine.dispatch(target)
        # engine.dispatch already wrote any error message to the user
        # via self.write(err_msg, "red").  Clear .error so the outer
        # dispatch layer that invoked us doesn't print the same
        # message a second time.  Preserve success/value for scripting.
        if not result.success:
            result = CmdResult(
                success=False,
                error="",
                elapsed_s=result.elapsed_s,
                value=result.value,
            )
        return result

    return handler
