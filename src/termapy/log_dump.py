"""Implementation of /log.dump -- print the session log to the terminal.

With no argument, prints the entire log.  With a positive integer
argument N, prints only the last N lines (tail -N).

Like ``log_show.py`` and ``log_fingerprint.py``, lives as a plain
module (not an auto-loaded plugin) because ``/log`` is an app-hook
namespace -- registering a ``log.*`` plugin would be wiped by the
frontend's ``/log.clear`` hook registration.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_log_path
from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _log_path(ctx: PluginContext) -> str:
    """Resolve the session log path from ctx.cfg / ctx.config_path."""
    configured = ctx.cfg.get("log_file", "") if ctx.cfg else ""
    if configured:
        return str(Path(configured).resolve())
    if ctx.config_path:
        return cfg_log_path(ctx.config_path)
    return ""


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Print the session log (all, or last N lines if an integer is given)."""
    path = _log_path(ctx)
    if not path:
        return CmdResult.fail(msg="No log file configured.")
    p = Path(path)
    if not p.exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")

    n: int | None = None
    arg = args.strip()
    if arg:
        try:
            n = int(arg)
        except ValueError:
            return CmdResult.fail(
                msg=f"Usage: {ctx.engine.prefix}log.dump [N]  (N = last N lines)"
            )
        if n <= 0:
            return CmdResult.fail(msg="N must be a positive integer.")

    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return CmdResult.fail(msg=f"Read error: {e}")

    if n is not None:
        lines = lines[-n:]

    for line in lines:
        ctx.io.output(line)
    return CmdResult.ok(value=str(len(lines)))


HANDLER = _handler
HELP = "Print the session log to the terminal; /log.dump <N> for the last N lines."
LONG_HELP = (
    "With no argument, prints the entire session log.  With a positive "
    "integer N, prints only the last N lines -- useful when the log is "
    "long and only the tail matters."
)
ARGS = "{count}"
