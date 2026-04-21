"""Legacy alias: /verbose -> /term.verbose.

Hidden forwarder with a one-time deprecation note.  The actual
handler lives in ``term.py``.
"""

from __future__ import annotations

from termapy.legacy import make_forwarder
from termapy.plugins import Command


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Show or toggle verbose status output. Use {prefix}verbose.quiet to set silently.",
    name="verbose",
    args="{on|off}",
    handler=make_forwarder("verbose", "term.verbose"),
    hidden=True,
)
