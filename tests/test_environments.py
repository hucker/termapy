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


def test_web_is_the_tui_without_its_terminal_and_desktop(monkeypatch):
    """WEB equals TUI except console, never, and gui_apps, only when TERMAPY_GUI says the viewer sits at the server."""
    monkeypatch.delenv(env_flags.WEB_HOST_VARIABLE, raising=False)
    tui, web = environment_capabilities("TUI"), environment_capabilities("WEB")
    assert web.console is False, "a browser tab has no terminal to hand to /cli or /vt100"
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), (
        "not served by --web, the desktop is not the viewer's: only the override counts"
    )
    assert replace(web, console=tui.console, gui_apps=tui.gui_apps) == tui, "nothing else differs"


@pytest.mark.parametrize("host", sorted(env_flags.LOOPBACK_HOSTS))
def test_served_on_this_machine_the_desktop_is_the_viewers(monkeypatch, host):
    """--web on a loopback address: the browser is here, so WEB has gui_apps exactly as the TUI does."""
    # Arrange -- what --web sets for the TUI it spawns
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, host)

    # Act
    web = environment_capabilities("WEB")

    # Assert
    assert env_flags.web_served_locally() is True
    assert web.gui_apps is environment_capabilities("TUI").gui_apps, "the detection applies"
    assert REQUIREMENT_HINTS["gui_apps"] not in hints_lacking("WEB", "TUI"), "the banner agrees"


def test_served_to_the_network_the_desktop_is_not_the_viewers(monkeypatch):
    """--web on a network address: a remote browser cannot see this desktop, so only TERMAPY_GUI=1 grants gui_apps."""
    # Arrange
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, "192.168.1.50")

    # Act
    web = environment_capabilities("WEB")

    # Assert
    assert env_flags.web_served_locally() is False
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), "the override alone"


def test_not_served_by_web_says_so():
    """Without --web's variable the question has no answer, which the row treats as not local."""
    assert env_flags.web_served_locally() is None


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
