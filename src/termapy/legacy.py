"""Legacy-command forwarding helper.

Old top-level commands that moved to a namespace (e.g. ``/echo`` ->
``/term.echo``) register a hidden forwarder built by
:func:`make_forwarder`.  The forwarder dispatches to the new command
and prints a one-time dim note the first time the legacy name is
used in a session -- so scripts keep working and users get nudged to
update.

``LEGACY_COMMANDS`` is the single source of truth for every rename.
It's populated as plugin modules import their forwarder, and consumed
by the ``/run.legacy`` tool to scan / rewrite script files.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# Populated by make_forwarder().  Maps old command name -> new name.
# ``/run.legacy`` reads this to report or rewrite legacy usage in
# script files.
LEGACY_COMMANDS: dict[str, str] = {}


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
            ctx.write(
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
