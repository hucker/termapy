"""Built-in plugin: /log.dump -- print the session log to the terminal.

With no argument, prints the entire log.  With a positive integer
argument N, prints only the last N lines (tail -N).

Lives in builtins/commands/ so MCP and CLI and TUI all get it from
the shared plugin layer (previously was a hook registered only by
TUI and CLI, leaving MCP unable to read the session log).  The
prior concern about ``register_hook`` wiping the ``log.*`` subtree
is moot here: no host registers a bare ``/log`` hook, so the
``log.dump`` plugin entry survives whatever the host adds to
``log.delete`` / ``log.clear`` later.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_log_path
from termapy.plugins import CmdResult, Command

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


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="log.dump",
    args=ARGS,
    help=HELP,
    long_help=LONG_HELP,
    handler=HANDLER,
)
