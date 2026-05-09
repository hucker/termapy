"""Legacy alias: /line_no -> /term.line_no.

Hidden forwarder.  The real TUI handler for /term.line_no is
installed by ``app.py`` via ``register_hook``.  In non-TUI
environments ``/term.line_no``'s capability gate (``tui_mode``)
fails dispatch before reaching the handler with a clear message.
"""

from __future__ import annotations

from termapy.legacy import make_forwarder
from termapy.plugins import Command


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="line_no",
    args="{on|off}",
    help="Toggle line numbers on or off.",
    handler=make_forwarder("line_no", "term.line_no"),
    hidden=True,
)
