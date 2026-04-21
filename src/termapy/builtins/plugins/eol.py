"""Legacy alias: /show_line_endings -> /term.line_endings.

Hidden forwarder with a one-time deprecation note.  The actual
handler lives in ``term.py``.
"""

from __future__ import annotations

from termapy.legacy import make_forwarder
from termapy.plugins import Command


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="show_line_endings",
    args="{on|off}",
    help="Toggle visible \\r \\n markers in serial output for line-ending troubleshooting.",
    handler=make_forwarder("show_line_endings", "term.line_endings"),
    hidden=True,
)
