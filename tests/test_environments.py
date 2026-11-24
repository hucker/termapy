"""The capability table's rows: what each frontend publishes, and WEB as the TUI served in a browser.

``capabilities.BASE_ENVIRONMENTS`` is the one table of what a frontend
provides: a host reads its row through ``environment_capabilities`` and
``/help`` reads the attainable view, ``ENVIRONMENTS``.  The WEB row is the TUI
without the terminal it runs in and, served to the network, without a desktop
the viewer sees or a path outside the config folder, and ``hints_lacking`` is
what the web banner prints.  Each host publishing its row is pinned beside the
host: ``test_app_pilot``, ``test_cli`` and ``test_mcp_boundary``.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from termapy import env_flags
from termapy.plugins.capabilities import (
    BASE_ENVIRONMENTS,
    ENVIRONMENTS,
    REQUIREMENT_HINTS,
    CapabilitySet,
    MissingCapability,
    attainable,
    confined_text,
    environment_capabilities,
    hints_lacking,
)
from termapy.plugins.handles.fs import FilesystemHandle

pytestmark = pytest.mark.rule("web-is-an-environment")

# An address a browser on another computer reaches: not a loopback.
NETWORK_HOST = "192.168.1.50"


def test_web_is_the_tui_without_its_terminal_and_desktop(monkeypatch):
    """WEB equals TUI except console, never, and gui_apps and host-wide paths, only when the viewer sits at the server or an opt-in says so."""
    monkeypatch.delenv(env_flags.WEB_HOST_VARIABLE, raising=False)
    tui, web = environment_capabilities("TUI"), environment_capabilities("WEB")
    assert web.console is False, "a browser tab has no terminal to hand to /app.cli or /app.vt100"
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), (
        "not served by --web, the desktop is not the viewer's: only the override counts"
    )
    assert web.filesystem_unconfined is env_flags.WEB_FS_UNCONFINED, (
        "not served by --web, the viewer is not here: only the opt-in counts"
    )
    same = replace(
        web,
        console=tui.console,
        gui_apps=tui.gui_apps,
        filesystem_unconfined=tui.filesystem_unconfined,
    )
    assert same == tui, "nothing else differs"


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
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, NETWORK_HOST)

    # Act
    web = environment_capabilities("WEB")

    # Assert
    assert env_flags.web_served_locally() is False
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), "the override alone"


@pytest.mark.rule("web-network-confined")
@pytest.mark.parametrize("host", sorted(env_flags.LOOPBACK_HOSTS))
def test_served_on_this_machine_keeps_host_wide_file_access(monkeypatch, host):
    """--web on a loopback address: the viewer is the operator at this desktop, so a path may leave the config folder as at a terminal."""
    # Arrange -- what --web sets for the TUI it spawns
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, host)

    # Act
    web = environment_capabilities("WEB")

    # Assert
    assert web.filesystem_unconfined is True, "the operator's own machine"
    assert REQUIREMENT_HINTS["filesystem_unconfined"] not in hints_lacking("WEB", "TUI"), (
        "the banner lists no confinement"
    )


@pytest.mark.rule("web-network-confined")
def test_served_to_the_network_keeps_files_in_the_config_folder(monkeypatch, tmp_path):
    """--web on a network address: a remote browser, with no login, is confined to the config folder as MCP is, the banner says so, and an escape is refused."""
    # Arrange -- served to the network, no opt-in
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, NETWORK_HOST)
    monkeypatch.setattr(env_flags, "WEB_FS_UNCONFINED", False)

    # Act
    web = environment_capabilities("WEB")
    handle = FilesystemHandle(cap_dir=tmp_path / "cap", capabilities=web)

    # Assert
    assert web.filesystem_unconfined is False, "confined, as an MCP server is"
    assert REQUIREMENT_HINTS["filesystem_unconfined"] in hints_lacking("WEB", "TUI"), (
        "the banner names the confinement with what else the tab lacks"
    )
    with pytest.raises(MissingCapability, match="confined to the config folder"):
        handle.resolve("../outside.txt", "cap")


@pytest.mark.rule("web-network-confined")
def test_the_web_opt_in_lifts_the_confinement(monkeypatch):
    """TERMAPY_WEB_FS_UNCONFINED=1 in the server's shell gives a network-served session host-wide file access."""
    # Arrange
    monkeypatch.setenv(env_flags.WEB_HOST_VARIABLE, NETWORK_HOST)
    monkeypatch.setattr(env_flags, "WEB_FS_UNCONFINED", True)

    # Act
    web = environment_capabilities("WEB")

    # Assert
    assert web.filesystem_unconfined is True, "the operator opted in from the server's shell"
    assert web.gui_apps is (env_flags.GUI_OVERRIDE is True), "the desktop rule is its own"


@pytest.mark.rule("web-network-confined")
def test_a_confinement_refusal_names_every_opt_in(tmp_path):
    """A refused escape says the session is confined and names each confined host's opt-in from the one hint, never MCP alone."""
    # Arrange -- a session with no host-wide file access, whichever host it is
    handle = FilesystemHandle(cap_dir=tmp_path, capabilities=CapabilitySet())

    # Act
    with pytest.raises(MissingCapability) as refused_path:
        handle.guard_external_path("../x.txt")
    with pytest.raises(MissingCapability) as refused_name:
        handle.resolve("../x.txt", "cap")

    # Assert
    for text in (str(refused_path.value), str(refused_name.value)):
        assert confined_text() in text, "one sentence, from the capability's hint"
        assert "TERMAPY_MCP_FS_UNCONFINED" in text and "TERMAPY_WEB_FS_UNCONFINED" in text, (
            "both confined hosts' opt-ins, so the advice fits whichever host refused"
        )
        assert "MCP host is confined" not in text, "no host named as the only one"


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
