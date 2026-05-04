"""Legacy alias: /verbose -> /term.output (with on/off arg translation).

Hidden forwarder with a one-time deprecation note.  Translates the
old boolean argument to the new level vocabulary:

    /verbose on   ->   /term.output verbose
    /verbose off  ->   /term.output normal
    /verbose      ->   /term.output             (query)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.legacy import LEGACY_COMMANDS, register_legacy_rewrite
from termapy.plugins import CapabilitySet, CmdResult, Command
from termapy.scripting import parse_bool

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# Record the rename so /run.legacy reports the canonical replacement.
# Both top-level (/verbose) and namespaced (/term.verbose) variants get
# args-aware rewrites; the bare-query form falls back to the simple
# table.
LEGACY_COMMANDS["verbose"] = "term.output"
LEGACY_COMMANDS["term.verbose"] = "term.output"
# ``.quiet`` was the pre-output-level idiom for "set silently" (suppress
# every channel including the result echo).  In the new model that
# behaviour belongs to ``.silent`` -- ``.quiet`` now means "result only".
# Forward old-idiom callers to the new spelling.
LEGACY_COMMANDS["echo.quiet"] = "echo.silent"
LEGACY_COMMANDS["term.echo.quiet"] = "term.echo.silent"
# "clear" should mean "empty visible state" -- /log.clear was the
# outlier that actually deleted the on-disk file.  Renamed to
# /log.delete to match the verb's meaning elsewhere (clear screen,
# clear vars, reset counters).  Old name keeps working as a hidden
# forwarder.
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


def _handler_verbose(ctx: PluginContext, args: str) -> CmdResult:
    """Forward /verbose [on|off] to /term.output with arg translation."""
    warned = ctx.ns("legacy_warned")
    if "verbose" not in warned:
        warned["verbose"] = True
        p = ctx.engine.prefix
        ctx.write(
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
    result = ctx.engine.dispatch(target)
    if not result.success:
        # engine.dispatch already wrote the error; clear .error so the
        # outer dispatch doesn't print it again.
        return CmdResult(
            success=False,
            error="",
            elapsed_s=result.elapsed_s,
            value=result.value,
        )
    return result


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="verbose",
    args="{on|off}",
    help="Legacy alias for /term.output (silent/quiet/normal/verbose).",
    handler=_handler_verbose,
    hidden=True,
    needs=CapabilitySet(interactive=True),  # legacy alias for human typing
)
