"""Implementation of /log.show -- open the session log in the system viewer.

Launches the platform's default handler (Notepad / TextEdit /
xdg-open) in a separate process, so it works in both CLI and TUI.

Kept as a plain module (mirrors ``log_fingerprint.py``) rather than an
auto-loaded plugin because ``/log`` is an app-hook namespace
(``/log.clear`` is registered from both frontends).  Both frontends
import and register it as a sibling hook.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.config import cfg_log_path, open_with_system
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
    """Open the session log in the system viewer."""
    path = _log_path(ctx)
    if not path:
        return CmdResult.fail(msg="No log file configured.")
    if not Path(path).exists():
        return CmdResult.fail(msg=f"Log file not found: {path}")
    open_with_system(path)
    ctx.io._write(f"  Opening {Path(path).name}", "green")
    return CmdResult.ok(value=path)


HANDLER = _handler
HELP = "Open the session log in the system viewer."
LONG_HELP = (
    "Launches the platform's default handler for the session log file "
    "(Notepad / TextEdit / xdg-open) in a separate process.  Use "
    "/log.dump to print the log to this terminal instead."
)
ARGS = ""
