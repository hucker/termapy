"""A command declares the config keys it uses, with how, and every surface derives from it (registry cfg-declared).

``Command.cfg`` is validated at load and copied to ``PluginInfo``;
``cfg_users`` turns it around; ``/help <cmd>`` renders a CONFIG section,
``/help <key>`` a key page, ``/search`` indexes the keys and the MCP
catalog carries them.  The guard that holds a module's reads to its
declarations is in ``test_architecture.py``; the editor's Used-by line
is in ``test_dialogs_config_editor.py``.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import Command, PluginInfo, cfg_users

pytestmark = pytest.mark.rule("cfg-declared")


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host over a throwaway config with one non-default value, every built-in registered."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": ""}, "max_grep_lines": 250}
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


def _handler(ctx, args):
    """A handler for a declaration test."""
    return None


class TestTheDeclaration:
    def test_a_leaf_resolves_to_its_path_and_the_phrase_is_kept(self):
        """A bare leaf of a nested key is stored as its path, and the phrase as typed, trimmed."""
        # Act
        command = Command(help="h", handler=_handler, cfg={"baud_rate": " set for the session "})

        # Assert
        assert command.cfg == {"serial.baud_rate": "set for the session"}

    @pytest.mark.rule("cfg-declared-read")
    def test_an_unknown_cfg_key_fails_at_construction(self):
        """A key DEFAULT_CFG does not hold fails loud at load, naming the key."""
        with pytest.raises(ValueError, match="unknown config key 'nope' in cfg="):
            Command(name="x", help="h", handler=_handler, cfg={"nope": "reads it"})

    @pytest.mark.rule("cfg-declared-read")
    def test_an_empty_phrase_fails_at_construction(self):
        """A key with nothing said about how it is used fails loud at load."""
        with pytest.raises(ValueError, match="cfg= must say how 'eol' is used"):
            PluginInfo(name="x", args="", help="h", handler=_handler, cfg={"eol": " "})

    def test_the_loader_copies_the_declaration_to_every_built_in(self, cli):
        """A loaded built-in carries its Command's cfg on its PluginInfo, path-keyed; one that declares none has an empty mapping."""
        plugins = cli.repl._plugins
        assert plugins["grep"].cfg == {"max_grep_lines": "caps the matches shown"}
        assert plugins["port.baud_rate"].cfg == {
            "serial.baud_rate": "set for the session; bare shows it",
            "serial.custom_baud": "set true for the session by custom=true; bare says (custom)",
        }
        assert plugins["cls"].cfg == {}

    def test_a_hook_lands_its_declaration(self, cli):
        """register_hook(cfg=) reaches the PluginInfo as a Command's does."""
        # Act
        cli.repl.register_hook("probe", "", "A probe.", _handler, cfg={"title": "names the window"})

        # Assert
        assert cli.repl._plugins["probe"].cfg == {"title": "names the window"}

    def test_cfg_users_turns_the_declarations_around(self, cli):
        """Key path -> the commands that declared it with their phrases, sorted."""
        # Act
        users = cfg_users(cli.repl._plugins)

        # Assert
        assert users["max_grep_lines"] == [("grep", "caps the matches shown")]
        names = [name for name, _ in users["eol_markers"]]
        assert names == ["term.eol.markers", "term.info"], "the two commands that declare it"


class TestHelpPages:
    def test_a_command_page_has_a_config_section_with_the_live_value(self, cli, capsys):
        """/help <cmd> lists each declared key with the config's value and the phrase; --verbose adds where a change lasts."""
        # Act
        page = _page(cli, capsys, "/help grep")
        full = _page(cli, capsys, "/help grep --verbose")

        # Assert
        assert "CONFIG" in page
        assert "max_grep_lines = 250  caps the matches shown" in page, (
            "the config's value, not the default"
        )
        assert "A change by /cfg <key> <value> lasts the session" in full
        assert "(/cfg.edit) saves it to c.cfg." in full, "the file is named"

    def test_a_command_without_declarations_has_no_config_section(self, cli, capsys):
        """A command that declares no keys shows no CONFIG section."""
        assert "CONFIG" not in _page(cli, capsys, "/help cls")

    def test_a_key_page_names_its_users_and_how(self, cli, capsys):
        """/help <key> shows the key's meaning, its value with the default, its file and spelling there, and each user with its phrase."""
        # Act
        page = _page(cli, capsys, "/help max_grep_lines")

        # Assert
        assert "max_grep_lines - Most lines /grep shows for one search." in page
        assert "max_grep_lines = 250  (default 100)" in page
        assert "FILE" in page
        assert 'c.cfg  "max_grep_lines": 250' in page, "the file and the key as it is spelled there"
        assert "/cfg.edit opens it in the editor" in page and "/help.open config" in page
        assert "USED BY" in page
        assert "/grep  caps the matches shown" in page
        assert "A change by /cfg max_grep_lines <value> lasts the session" in page
        assert "saves it to c.cfg." in page

    def test_a_bare_leaf_resolves_to_the_nested_key(self, cli, capsys):
        """/help baud_rate is the serial.baud_rate page, with the setters and readers that declared it."""
        # Act
        page = _page(cli, capsys, "/help baud_rate")

        # Assert
        assert "serial.baud_rate - Line speed" in page
        assert '"serial": {"baud_rate": 115200}' in page, "a nested key's spelling in the file"
        assert "/port.baud_rate" in page and "set for the session; bare shows it" in page
        assert "/mcp.info" in page, "a reader is listed beside the setters"

    def test_a_command_wins_over_a_key_of_the_same_name(self, cli, capsys):
        """/help port is the command page; /help serial.port is the key page."""
        assert "SYNOPSIS" in _page(cli, capsys, "/help port"), "the command"
        key_page = _page(cli, capsys, "/help serial.port")
        assert "USED BY" in key_page and "/port.disconnect" in key_page, "the key"


class TestRowLinks:
    @pytest.mark.rule("links-are-data")
    def test_a_link_covers_only_the_name_never_the_padding(self, cli):
        """In a CONFIG row and a USED BY row the linked piece is exactly the key or command name; the column padding is a plain piece."""
        from termapy.builtins.commands.help import _cfg_rows, _used_by_rows

        # Arrange -- names of different lengths, so the short one is padded
        uses = {"serial.baud_rate": "on the page", "eol": "sent"}
        users = [("mcp.info", "in the summary"), ("port.baud_rate", "set")]

        # Act
        rows = _cfg_rows(cli.repl.ctx, uses) + _used_by_rows("/", users)

        # Assert
        linked = [piece.text for row in rows for piece in row if piece.link is not None]
        assert linked == ["serial.baud_rate", "eol", "/mcp.info", "/port.baud_rate"]
        assert all(text == text.strip() for text in linked), "no padding inside a link"
        lines = ["".join(piece.text for piece in row) for row in rows]
        assert lines[2] == "  /mcp.info        in the summary", "the column still lines up"
        assert lines[3] == "  /port.baud_rate  set"


class TestSearchAndCatalog:
    def test_search_finds_a_command_by_a_key_it_declares(self, cli, capsys):
        """/search <key> hits the declaring command on its cfg field, showing the phrase."""
        # Act
        out = _page(cli, capsys, "/search max_grep_lines")

        # Assert
        assert "/grep" in out
        assert "(cfg) max_grep_lines: caps the matches shown" in out

    def test_the_catalog_carries_the_declaration_only_when_there_is_one(self, cli):
        """An MCP entry has cfg = {path: phrase} for a declaring command and no cfg key otherwise."""
        from termapy.mcp.catalog import build_catalog

        # Act
        entries = {entry["name"]: entry for entry in build_catalog(cli.repl.ctx)["commands"]}

        # Assert
        assert entries["/grep"]["cfg"] == {"max_grep_lines": "caps the matches shown"}
        assert "cfg" not in entries["/cls"], "omitted when none are declared, as safety is"
