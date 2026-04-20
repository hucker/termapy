"""Legacy alias: /echo -> /term.echo.

Hidden forwarder with a one-time deprecation note.  The actual
handler lives in ``term.py``.
"""

from __future__ import annotations

from termapy.legacy import make_forwarder
from termapy.plugins import Command


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="echo",
    args="{on|off}",
    help="Toggle REPL command echo. Use {prefix}echo.quiet to set silently.",
    handler=make_forwarder("echo", "term.echo"),
    hidden=True,
)
