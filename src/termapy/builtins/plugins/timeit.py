"""Built-in plugin: time how long a REPL command takes to execute."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from termapy.plugins import Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> None:
    """Time a REPL command and report elapsed seconds.

    Usage: /timeit <command> [args]
    Example: /timeit port.info

    Args:
        ctx: Plugin context.
        args: The command (without prefix) and its arguments.
    """
    line = args.strip()
    if not line:
        ctx.write("Usage: /timeit <command> [args]")
        return

    parts = line.split(None, 1)
    cmd_name = parts[0].lstrip(ctx.engine.prefix)
    cmd_args = parts[1] if len(parts) > 1 else ""

    if cmd_name == "timeit":
        ctx.write("Cannot time timeit (recursion prevented)")
        return

    plugin = ctx.engine.plugins.get(cmd_name)
    if not plugin:
        ctx.write(f"Unknown command: {cmd_name}")
        return

    start = time.perf_counter()
    plugin.handler(ctx, cmd_args)
    elapsed = time.perf_counter() - start

    ctx.write(f"  timeit: {elapsed:.6f}s")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="timeit",
    args="<command> [args]",
    help="Time how long a REPL command takes to execute.",
    handler=_handler,
)
