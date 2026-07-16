"""Custom Textual widgets for termapy's TUI.

Self-contained UI components -- each owns its own state, CSS, and behavior
and reaches into nothing outside itself, so it can be Pilot-tested in
isolation (see ``tests/test_status_bar.py``).  This is the home for
components carved out of ``app.py`` going forward.

Re-exported here so callers keep using ``from termapy.widgets import X``.
UI-layer (imports Textual).
"""

from termapy.widgets.checkbox import StrongCheckbox
from termapy.widgets.status_bar import StatusBar
from termapy.widgets.suggester import CommandSuggester

__all__ = ["CommandSuggester", "StatusBar", "StrongCheckbox"]
