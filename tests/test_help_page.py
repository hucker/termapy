"""A help page shows what is about this command (registry help-page-specific).

What every page shares waits behind ``--verbose``, or a hover on the command's
name in the TUI, unless it is news: a status other than stable, a frontend that
cannot run the command, a requirement not met right now.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG

_SHARED = ("STATUS", "AVAILABLE", "SEE ALSO", "A change by")


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


class TestADefaultPage:
    def test_what_every_page_shares_stays_out(self, cli, capsys):
        """help-page-specific: a stable command that runs everywhere shows only what is its own."""
        # Act
        page = _page(cli, capsys, "/help term.hex")

        # Assert
        for section in _SHARED:
            assert section not in page, f"{section} waits behind --verbose"
        for section in ("NAME", "CURRENT STATE", "SYNOPSIS", "DESCRIPTION", "CONFIG"):
            assert section in page, f"{section} is about this command"

    def test_a_status_that_is_news_stays(self, cli, capsys):
        """help-page-specific: a command not yet stable says so on its page, front and center."""
        # Act
        page = _page(cli, capsys, "/help mem.info")

        # Assert
        assert "STATUS" in page
        assert "preview" in page

    def test_a_frontend_that_cannot_run_it_stays(self, cli, capsys):
        """help-page-specific: where a command cannot run is news, and the reason comes with it."""
        # Act
        page = _page(cli, capsys, "/help find")

        # Assert
        assert "AVAILABLE" in page
        assert "MCP: no" in page
        assert "(MCP does not provide: interactive)" in page

    def test_only_a_missing_requirement_shows(self, cli, capsys):
        """help-page-specific: the row answers why a command will not run now, so a met one stays out."""
        # Act
        closed = _page(cli, capsys, "/help port.dtr")
        cli.repl.dispatch("port.connect")
        opened = _page(cli, capsys, "/help port.dtr")

        # Assert
        assert "REQUIRED CAPABILITIES" in closed
        assert "(missing)" in closed
        assert "REQUIRED CAPABILITIES" not in opened, "met, so nothing to say"

    def test_a_family_keeps_its_subcommands(self, cli, capsys):
        """A family's list is what its page is for, so it stays."""
        assert "SUBCOMMANDS" in _page(cli, capsys, "/help term")


class TestTheWholePage:
    @pytest.mark.parametrize("line", ["/help term.hex --verbose", "/help.verbose term.hex"])
    def test_verbose_shows_it_for_one_call(self, cli, capsys, line):
        """help-page-specific: the flag and its suffix show everything, and the next page is short again."""
        # Act
        full = _page(cli, capsys, line)
        again = _page(cli, capsys, "/help term.hex")

        # Assert
        for section in _SHARED:
            assert section in full, section
            assert section not in again, f"{section}: --verbose lasts one call"

    def test_the_session_level_shows_it_too(self, cli, capsys):
        """help-page-specific: at /term.output verbose a page is whole, the level meaning what it says."""
        # Arrange
        cli.repl.dispatch("term.output verbose")

        # Act
        page = _page(cli, capsys, "/help term.hex")

        # Assert
        for section in _SHARED:
            assert section in page, section

    def test_the_developer_page_is_whole(self, cli, capsys):
        """help-page-specific: /help.dev is about the code, and keeps every section."""
        # Act
        page = _page(cli, capsys, "/help.dev term.hex")

        # Assert
        assert "STATUS" in page
        assert "AVAILABLE" in page


class TestAHoverHoldsTheRest:
    def test_a_commands_name_says_where_it_stands(self, cli):
        """help-page-specific: the name's tooltip carries what left the page, so it is one hover away."""
        # Act
        tip = _links(cli, "help term.hex")["/term.hex"].tip

        # Assert
        assert "Status: stable" in tip
        assert "TUI: yes" in tip
        assert "MCP: yes" in tip
        assert "--json" in tip, "and what any command also takes"
        assert "--verbose shows all of this" in tip, "and how to print it"

    def test_the_name_says_where_it_cannot_run(self, cli):
        """The same line is true of a command with a limit."""
        assert "MCP: no" in _links(cli, "help find")["/find"].tip

    def test_a_config_key_says_where_a_change_lasts(self, cli):
        """help-page-specific: the line that left CONFIG is on the key, with the key and the file named."""
        # Act
        tip = _links(cli, "help term.hex")["hex"].tip

        # Assert
        assert "A change by /cfg hex <value> lasts the session" in tip
        assert "saves it to c.cfg" in tip
