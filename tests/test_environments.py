"""The capability table's rows: what each frontend publishes, and WEB as the TUI served in a browser.

``capabilities.BASE_ENVIRONMENTS`` is the one table of what a frontend
provides: a host reads its row through ``environment_capabilities`` and
``/help`` reads the attainable view, ``ENVIRONMENTS``.  The WEB row is the TUI
without the terminal it runs in and without a desktop the viewer sees, and
``hints_lacking`` is what the web banner prints.  Each host publishing its
row is pinned beside the host: ``test_app_pilot``, ``test_cli`` and
``test_mcp_boundary``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from termapy import env_flags
from termapy.plugins.capabilities import (
    BASE_ENVIRONMENTS,
    ENVIRONMENTS,
    REQUIREMENT_HINTS,
    attainable,
    environment_capabilities,
    hints_lacking,
)

pytestmark = pytest.mark.rule("web-is-an-environment")


def test_web_is_the_tui_without_its_terminal_and_desktop():
    """WEB equals TUI except console, never, and gui_apps, only when TERMAPY_GUI says the viewer sits at the server."""
    tui, web = environment_capabilities("TUI"), environment_capabilities("WEB")
    assert web.console is False, "a browser tab has no terminal to hand to /cli or /vt100"
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), (
        "detection looks at the server, so only the override counts"
    )
    assert replace(web, console=tui.console, gui_apps=tui.gui_apps) == tui, "nothing else differs"


def test_every_row_is_what_help_reads():
    """ENVIRONMENTS is each row with the dynamic capabilities granted, TUI first and WEB beside it."""
    assert list(ENVIRONMENTS) == ["TUI", "WEB", "CLI", "MCP"], "the AVAILABLE column order"
    actual = {name: attainable(caps) for name, caps in BASE_ENVIRONMENTS.items()}
    assert ENVIRONMENTS == actual, "one table, two views"


def test_the_web_banner_names_what_a_browser_tab_lacks():
    """hints_lacking lists the /help hints of what TUI has and WEB has not, in declaration order: the terminal first."""
    tui, web = environment_capabilities("TUI"), environment_capabilities("WEB")

    hints = hints_lacking("WEB", "TUI")

    assert hints[0] == REQUIREMENT_HINTS["console"], "the terminal comes first in the vocabulary"
    assert (REQUIREMENT_HINTS["gui_apps"] in hints) is (tui.gui_apps and not web.gui_apps), (
        "the desktop only when this machine has one the tab cannot see"
    )
    assert hints_lacking("TUI", "TUI") == [], "a row lacks nothing against itself"
