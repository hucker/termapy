"""A capability explains itself: /help capability lists them, /help <name> opens one, a REQUIRED CAPABILITIES row links there.

Every fact on the pages is the field's (registry ``capability-declared-once``):
the hint, whether it is session state, which frontends provide it.  The
commands needing one are read from the registry, as an idiom's TAKEN BY list
is, so the page never claims more than is true.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.idioms import IDIOMS
from termapy.plugins.capabilities import (
    ATTAINABLE,
    REQUIREMENT_HINTS,
    STATE_MESSAGES,
    provided_by,
)

pytestmark = [
    pytest.mark.rule("capability-declared-once"),
    pytest.mark.rule("link-explains-itself"),
]


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host over a throwaway config, every built-in registered, no port open."""
    serial = DEFAULT_CFG["serial"]
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}}
    path = tmp_path / "c" / "c.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return terminal


def _page(cli: CLITerminal, capsys, line: str) -> str:
    """What ``line`` prints."""
    capsys.readouterr()
    cli.repl.dispatch_full(line)
    return capsys.readouterr().out


def _links(cli: CLITerminal, line: str) -> dict:
    """``{text: link}`` for the links ``line`` prints, as a frontend that can click gets them."""
    found: dict = {}
    cli.repl.ctx.io._write_segments = lambda pieces, color: found.update(
        {piece.text: piece.link for piece in pieces if piece.link is not None}
    )
    cli.repl.dispatch(line)
    return found


def _section(page: str, title: str) -> str:
    """The text of one section of a page, up to the next all-caps heading."""
    body = page.split(f"\n{title}\n")[1]
    lines = []
    for line in body.splitlines():
        if line and not line.startswith(" "):
            break
        lines.append(line)
    return "\n".join(lines)


class TestOnePage:
    @pytest.mark.parametrize("name", list(REQUIREMENT_HINTS))
    def test_every_capability_opens_its_page_with_its_hint(self, cli, capsys, name):
        """/help <name> answers with the name, and the page opens on the field's own hint."""
        # Act
        result = cli.repl.dispatch(f"help {name}")
        page = _page(cli, capsys, f"/help {name}")

        # Assert
        assert (result.success, result.value) == (True, name)
        hint = REQUIREMENT_HINTS[name].replace("{prefix}", "/")
        assert page.splitlines()[1] == f"  {name} - {hint}", page.splitlines()[1]
        assert "{prefix}" not in page, "the prefix is filled in"

    def test_needed_by_is_the_registry(self, cli, capsys):
        """NEEDED BY is the commands whose needs declare the capability, never a claim on the page."""
        # Arrange
        expected = {name for name, plugin in cli.repl._plugins.items() if plugin.needs.gui_apps}
        assert "cfg.show" in expected, "precondition: a command needs it"

        # Act
        needed_by = _section(_page(cli, capsys, "/help gui_apps"), "NEEDED BY")

        # Assert
        actual = {name.strip().lstrip("/") for name in needed_by.split(",")}
        assert actual == expected

    def test_provided_by_is_the_table_with_the_current_frontend_marked(self, cli, capsys):
        """A frontend capability names the frontends from ENVIRONMENTS and marks this one."""
        # Act
        page = _page(cli, capsys, "/help console")

        # Assert
        assert "CLI (current)" in page, "this session is the CLI"
        for env in provided_by("console"):
            assert env in page.split("Provided by:")[1].splitlines()[0], env
        assert "WEB" not in page.split("Provided by:")[1].splitlines()[0], (
            "a browser tab has no console, so WEB is not named"
        )

    def test_session_state_says_what_the_gate_answers(self, cli, capsys):
        """A dynamic capability is session state: the page says so and quotes the gate's answer."""
        # Act
        page = _page(cli, capsys, "/help config_loaded")

        # Assert
        assert "config_loaded" in ATTAINABLE, "precondition: it is dynamic"
        assert "Provided by: any frontend, as session state" in page
        assert f"Until then: {STATE_MESSAGES['config_loaded']}" in page, "the gate's answer"
        assert "This session: yes" in page, "the rig has a config loaded"

    def test_this_session_reads_the_live_set(self, cli, capsys):
        """What this session has is the gate's own answer: the CLI has no screen to capture."""
        assert "This session: no" in _page(cli, capsys, "/help screen_capture")

    def test_a_near_miss_suggests_the_capability(self, cli):
        """A misspelled capability is suggested as a command or a key would be."""
        # Act
        result = cli.repl.dispatch("help gui_app")

        # Assert
        assert not result.success
        assert "gui_apps" in result.error, result.error


class TestTheList:
    def test_the_list_links_every_capability_to_its_page(self, cli):
        """/help capability names every restrictive capability, each a link running its page."""
        # Act
        links = _links(cli, "help capability")

        # Assert
        for name in REQUIREMENT_HINTS:
            assert name in links, f"{name} is not on the list"
            assert links[name].command == f"help {name}"
            assert links[name].run is True, "reading help changes nothing"

    def test_the_list_says_who_provides_each(self, cli, capsys):
        """Each row ends with the frontends providing it, or the session state it is."""
        # Act
        page = _page(cli, capsys, "/help capability")

        # Assert
        rows = {line.split()[0]: line for line in _section(page, "CAPABILITIES").splitlines()}
        assert rows["console"].endswith(f"[{' '.join(provided_by('console'))}]")
        assert rows["config_loaded"].endswith(f"[session state: {STATE_MESSAGES['config_loaded']}]")

    def test_the_idioms_list_has_the_row(self, cli):
        """/help.idioms lists capability beside the other shared words, and the alias opens it."""
        # Act
        names = [record["name"] for record in cli.repl.dispatch("help.idioms").data]

        # Assert
        assert "capability" in names
        assert [idiom.name for idiom in IDIOMS] == names
        assert cli.repl.dispatch("help capabilities").value == "capability"


class TestTheRows:
    @pytest.mark.rule("links-are-data")
    def test_a_required_capability_row_links_to_its_page(self, cli):
        """On a command's page the capability's name runs its page, and the hover adds who provides it."""
        # Act -- the CLI lacks screen_capture, so the row shows without --verbose
        links = _links(cli, "help ss.svg")

        # Assert
        assert links["screen_capture"].command == "help screen_capture"
        assert links["screen_capture"].run is True
        assert (
            links["screen_capture"].tip == f"Provided by: {' '.join(provided_by('screen_capture'))}"
        )

    @pytest.mark.rule("links-are-data")
    def test_the_section_title_links_to_the_idiom(self, cli):
        """The REQUIRED CAPABILITIES title opens /help capability, the page on the idea itself."""
        # Act
        links = _links(cli, "help ss.svg")

        # Assert
        assert links["REQUIRED CAPABILITIES"].command == "help capability"
        assert links["REQUIRED CAPABILITIES"].run is True

    def test_the_row_still_reads_as_it_did(self, cli, capsys):
        """Making the name a link changes nothing a frontend with no links prints."""
        # Act
        page = _page(cli, capsys, "/help ss.svg")

        # Assert
        hint = REQUIREMENT_HINTS["screen_capture"]
        assert f"  screen_capture - {hint} (missing)" in page

    def test_the_tooltip_is_the_hint(self, cli):
        """A link to a capability's page says what the capability is, read from the field."""
        assert cli.repl.link_summary("help gui_apps") == REQUIREMENT_HINTS["gui_apps"]
        assert cli.repl.link_summary("help tui_mode") == "the TUI (switch with /tui)", (
            "with the prefix filled in"
        )
