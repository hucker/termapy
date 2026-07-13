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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from termapy.plugins import CapabilitySet, CmdResult
from termapy.scripting import parse_bool

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
            p = ctx.prefix
            ctx.io._write(
                f"  Note: {p}{old_name} is legacy; use {p}{new_name}.",
                "yellow",
            )
        # Use ctx.internal.dispatch (bare REPL dispatch) rather than
        # ctx.dispatch (the full pipeline).  ctx.dispatch requires a
        # prefixed command -- un-prefixed input would be treated as
        # serial and sent to the device.  ctx.internal.dispatch takes the
        # name-and-args directly and routes through plugin lookup,
        # capability gates, and flag parsing.
        target = f"{new_name} {args}".strip()
        result = ctx.internal.dispatch(target)
        # ctx.internal.dispatch already wrote any error message to the user
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


# ── Central legacy forwarders ─────────────────────────────────────────────────
#
# The hidden alias commands that used to be one plugin file each
# (echo.py, line_no.py, eol.py, verbose.py) are registered centrally by
# ``ReplEngine._register_legacy_forwarders()`` from ``LEGACY_FORWARDERS``
# below, so they don't clutter ``builtins/commands/``.  Building the list
# at import time runs the ``make_forwarder`` side effects, so
# ``LEGACY_COMMANDS`` is populated for ``/run.legacy`` exactly as before.


def _verbose_forwarder(ctx: PluginContext, args: str) -> CmdResult:
    """Forward ``/verbose [on|off]`` to ``/term.output`` with arg translation.

    Unlike a plain name forward, the old boolean argument maps to the
    new level vocabulary: ``on`` -> ``verbose``, ``off`` -> ``normal``,
    bare -> query.
    """
    warned = ctx.ns("legacy_warned")
    if "verbose" not in warned:
        warned["verbose"] = True
        p = ctx.prefix
        ctx.io.output(
            f"  Note: {p}verbose is legacy; use {p}term.output "
            f"(verbose|normal).",
            "yellow",
        )
    body = args.strip()
    if not body:
        target = "term.output"
    else:
        val = parse_bool(body)
        if val is True:
            target = "term.output verbose"
        elif val is False:
            target = "term.output normal"
        else:
            return CmdResult.fail(msg=f"Invalid: {body} (use on or off)")
    result = ctx.internal.dispatch(target)
    if not result.success:
        return CmdResult(
            success=False,
            error="",
            elapsed_s=result.elapsed_s,
            value=result.value,
        )
    return result


@dataclass(frozen=True)
class LegacyForwarder:
    """A hidden legacy command alias registered centrally at engine load."""

    name: str
    args: str
    help: str
    handler: Callable
    needs: CapabilitySet


# Built at import so make_forwarder() populates LEGACY_COMMANDS now.
LEGACY_FORWARDERS: list[LegacyForwarder] = [
    LegacyForwarder(
        "echo",
        "{on|off}",
        "Toggle echo of device commands sent to the wire. "
        "Use {prefix}echo.silent to set without echoing.",
        make_forwarder("echo", "term.echo"),
        CapabilitySet(interactive=True),  # legacy alias for human typing
    ),
    LegacyForwarder(
        "line_no",
        "{on|off}",
        "Toggle line numbers on or off.",
        make_forwarder("line_no", "term.line_no"),
        CapabilitySet(),
    ),
    LegacyForwarder(
        "show_line_endings",
        "{on|off}",
        "Toggle visible \\r \\n markers in serial output for line-ending troubleshooting.",
        make_forwarder("show_line_endings", "term.line_endings"),
        CapabilitySet(interactive=True),
    ),
    LegacyForwarder(
        "verbose",
        "{on|off}",
        "Legacy alias for /term.output (silent/quiet/normal/verbose).",
        _verbose_forwarder,
        CapabilitySet(interactive=True),
    ),
    LegacyForwarder(
        "ver",
        "",
        "Legacy alias for /app.ver (installed version).",
        make_forwarder("ver", "app.ver"),
        CapabilitySet(),
    ),
    LegacyForwarder(
        "ver.latest",
        "",
        "Legacy alias for /app.ver.latest.",
        make_forwarder("ver.latest", "app.ver.latest"),
        CapabilitySet(),
    ),
    LegacyForwarder(
        "ver.info",
        "",
        "Legacy alias for /app.ver.info.",
        make_forwarder("ver.info", "app.ver.info"),
        CapabilitySet(),
    ),
]

# Args-aware verbose forwarding + the rename-only entries that used to
# live in verbose.py.  These feed /run.legacy's script scanner; the
# runtime forward for /verbose is _verbose_forwarder above.
LEGACY_COMMANDS["verbose"] = "term.output"
LEGACY_COMMANDS["term.verbose"] = "term.output"
# ``.quiet`` was the pre-output-level idiom for "set silently"; in the
# new model that behaviour is ``.silent`` (``.quiet`` now means "result
# only").  Forward old-idiom callers to the new spelling.
LEGACY_COMMANDS["echo.quiet"] = "echo.silent"
LEGACY_COMMANDS["term.echo.quiet"] = "term.echo.silent"
# "clear" should mean "empty visible state" -- /log.clear was the outlier
# that deleted the on-disk file.  Renamed to /log.delete to match the
# verb's meaning elsewhere; old name keeps working as a hidden forwarder.
LEGACY_COMMANDS["log.clear"] = "log.delete"
register_legacy_rewrite(r"^verbose\s+on\b", "term.output verbose")
register_legacy_rewrite(r"^verbose\s+off\b", "term.output normal")
register_legacy_rewrite(r"^term\.verbose\s+on\b", "term.output verbose")
register_legacy_rewrite(r"^term\.verbose\s+off\b", "term.output normal")
register_legacy_rewrite(
    r"^term\.verbose\.silent\s+on\b", "term.output.silent verbose"
)
register_legacy_rewrite(
    r"^term\.verbose\.silent\s+off\b", "term.output.silent normal"
)
register_legacy_rewrite(
    r"^term\.verbose\.quiet\s+on\b", "term.output.silent verbose"
)
register_legacy_rewrite(
    r"^term\.verbose\.quiet\s+off\b", "term.output.silent normal"
)
