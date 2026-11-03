"""The words commands share explain themselves (registry idioms-one-table).

``--json``, ``--quiet``, the boolean words, ``toggle`` and ``cycle`` read the same
on every command, so no command's page explains them.  ``idioms.IDIOMS`` does, and
these pin each surface reading it: ``/help <word>``, ``/help.idioms``, and the
links and tooltips a synopsis gets from its own text.
"""

from __future__ import annotations

import asyncio
import json
import re
from importlib import resources
from pathlib import Path

import pytest
from pilot_wait import until
from textual.widgets import Button, Input

import termapy
from termapy.cli import CLITerminal
from termapy.defaults import CFG_HELP, DEFAULT_CFG, cfg_key_paths
from termapy.idioms import ANY_COMMAND_FLAGS, IDIOM_TOKENS, IDIOMS, idiom_for
from termapy.plugins import CmdResult, PluginInfo
from termapy.plugins.output_levels import LEVEL_FLAGS
from termapy.profile import profile_command_view
from termapy.scripting import BOOL_TOKENS_HELP

IDIOMS_BY_NAME = {idiom.name: idiom for idiom in IDIOMS}


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host over a throwaway config, every built-in registered."""
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


def _links(cli: CLITerminal, line: str) -> list:
    """``(text, link)`` for each link ``line`` prints, as a frontend that can click gets them."""
    found: list = []
    cli.repl.ctx.io._write_segments = lambda pieces, color: found.extend(
        (piece.text, piece.link) for piece in pieces if piece.link is not None
    )
    cli.repl.dispatch(line)
    return found


def _register(cli: CLITerminal, name: str, args: str = "", hidden: bool = False) -> None:
    """Register a command, as a plugin would, with this synopsis."""

    def _handler(ctx, _args):
        """A command for a lookup test."""
        return CmdResult.ok(value="")

    cli.repl.register_plugin(
        PluginInfo(name=name, args=args, help="A test command.", handler=_handler, hidden=hidden)
    )


class TestThePages:
    @pytest.mark.parametrize(
        ("word", "expected"),
        [
            ("boolean", "boolean"),
            ("bool", "boolean"),
            ("on", "boolean"),
            ("off", "boolean"),
            ("toggle", "toggle"),
            ("cycle", "cycle"),
            ("bare", "bare"),
            ("json", "json"),
            ("level", "level"),
            ("silent", "level"),
            ("quiet", "level"),
            ("normal", "level"),
            ("verbose", "level"),
        ],
    )
    def test_each_word_opens_its_page(self, cli, word, expected):
        """idioms-one-table: /help <word> answers with the idiom it names, for a script to read too."""
        # Act
        result = cli.repl.dispatch(f"help {word}")

        # Assert
        assert (result.success, result.value) == (True, expected)

    def test_every_flag_names_an_idiom_with_its_dashes(self):
        """idioms-one-table: each flag any command takes is explained by some row."""
        actual = {flag: idiom_for(flag) for flag in ANY_COMMAND_FLAGS}
        assert all(actual.values()), f"a flag no idiom explains: {actual}"

    def test_a_page_says_what_its_owner_says(self, cli, capsys):
        """idioms-one-table: the words on a page are the owner's, so they cannot drift from the reader."""
        # Act
        boolean = _page(cli, capsys, "/help boolean")
        level = _page(cli, capsys, "/help level")

        # Assert
        assert BOOL_TOKENS_HELP in boolean, "the boolean words are the reader's"
        for flag in LEVEL_FLAGS:
            assert flag in level, f"{flag} is on the level page"

    def test_a_page_opens_with_its_summary(self, cli, capsys):
        """The line a tooltip shows is the first thing the page says, in the configured prefix."""
        # Act
        out = _page(cli, capsys, "/help cycle")

        # Assert
        assert IDIOM_TOKENS["cycle"].summary in out
        assert "{prefix}" not in out, "the prefix is filled in"
        assert "/term.output cycle" in out, "the example shows it in use"

    def test_taken_by_is_the_registry(self, cli, capsys):
        """idioms-one-table: the list is the commands whose synopsis offers the word, never a claim."""
        # Arrange
        offers = re.compile(r"[{|]cycle[|}]")
        expected = {
            name
            for name, plugin in cli.repl._plugins.items()
            if not plugin.hidden and plugin.args and offers.search(plugin.args)
        }
        assert "term.eol" in expected, "precondition: a setting takes it"

        # Act
        out = _page(cli, capsys, "/help cycle")
        taken_by = out.split("TAKEN BY")[1].split("SEE ALSO")[0]

        # Assert
        actual = {name.strip().lstrip("/") for name in taken_by.split(",")}
        assert actual == expected


class TestALookup:
    def test_a_command_keeps_its_name(self, cli, capsys):
        """idioms-one-table: a plugin may call a command cycle, and then that is what /help cycle shows."""
        # Arrange
        _register(cli, "cycle")

        # Act
        result = cli.repl.dispatch("help cycle")

        # Assert
        assert result.value == "cycle"
        assert "A test command." in _page(cli, capsys, "/help cycle"), "the command's own page"

    def test_a_hidden_command_gives_way(self, cli):
        """A legacy alias does not take a word a page answers to: color is the key, verbose the idiom."""
        # Arrange
        assert cli.repl._plugins["color"].hidden, "precondition: /color is a hidden alias"
        assert cli.repl._plugins["verbose"].hidden, "precondition: /verbose is a hidden alias"

        # Act / Assert
        assert cli.repl.dispatch("help color").value == "color", "the config key"
        assert cli.repl.dispatch("help verbose").value == "level", "the idiom"

    def test_a_hidden_command_still_answers(self, cli, capsys):
        """With nothing else claiming the name, a hidden command's page is still there, and its dev page."""
        # Act
        page = _page(cli, capsys, "/help term.verbose")
        dev = _page(cli, capsys, "/help.dev term.verbose")

        # Assert
        assert "Legacy alias" in page
        assert "DESCRIPTION (developer)" in dev

    @pytest.mark.parametrize(
        ("typed", "meant"),
        [("output_levels", "output_level"), ("togle", "toggle"), ("cyclo", "cycle")],
    )
    def test_a_near_miss_suggests_what_it_meant(self, cli, typed, meant):
        """did-you-mean-one: a key or an idiom is suggested as a command is, not only commands."""
        # Act
        result = cli.repl.dispatch(f"help {typed}")

        # Assert
        assert not result.success
        assert meant in result.error, result.error


class TestTheList:
    def test_the_records_are_the_table(self, cli):
        """list-one-format: /help.idioms is one row per idiom, with the same records under --json."""
        # Act
        result = cli.repl.dispatch("help.idioms")

        # Assert
        names = [record["name"] for record in result.data]
        assert names == [idiom.name for idiom in IDIOMS]
        assert result.value == "\n".join(names), "a script reads the names"

    def test_a_row_opens_its_page(self, cli):
        """links-are-data: each row's name runs its page, since reading help changes nothing."""
        # Act
        links = dict(_links(cli, "help.idioms"))

        # Assert
        assert links["cycle"].command == "help cycle"
        assert links["cycle"].run is True

    def test_search_finds_an_idiom(self, cli, capsys):
        """/search reaches the table through the long_help built from it, with no search code of its own."""
        assert "/help.idioms" in _page(cli, capsys, "/search wrapping")

    def test_the_landscape_points_at_it(self, cli, capsys):
        """Bare /help names the list, the one place a reader who knows none of this will look."""
        assert "/help.idioms" in _page(cli, capsys, "/help")


class TestTheLinks:
    @pytest.mark.parametrize("word", list(IDIOM_TOKENS))
    def test_a_synopsis_word_links_to_its_page(self, cli, word):
        """idioms-one-table: on every command offering it, the word is a link running its page.

        Read from the synopsis with no declaration, so a new command is covered
        the day it is written.
        """
        # Arrange
        offers = re.compile(rf"[{{|]{word}[|}}]")
        names = [
            name
            for name, plugin in cli.repl._plugins.items()
            if not plugin.hidden and plugin.args and offers.search(plugin.args)
        ]
        assert names, "precondition: some command offers it"
        expected = IDIOM_TOKENS[word]

        for name in names:
            # Act
            links = dict(_links(cli, f"help {name}"))

            # Assert
            assert word in links, f"/help {name}: {word} is not a link"
            assert links[word].command == f"help {expected.name}", name

    def test_a_word_that_only_looks_like_one_is_not_a_link(self, cli):
        """Only a whole alternative in a group is an idiom: {online}, {off=<n>} and {on_x|y} stay text."""
        # Arrange
        _register(cli, "probe", "{online} {off=<n>} {on_x|y}")

        # Act
        links = dict(_links(cli, "help probe"))

        # Assert
        assert not set(links) & set(IDIOM_TOKENS), links

    def test_the_synopsis_still_reads_as_written(self, cli, capsys):
        """Splitting out the links changes nothing a frontend with no links prints."""
        assert "/term.eol {cr|lf|crlf|none|cycle}" in _page(cli, capsys, "/help term.eol")

    def test_the_commands_name_names_the_flags(self, cli):
        """Hovering a command's name says what each flag does, on a page with a synopsis or without one."""
        for command in ("term.eol", "term.info"):
            # Act
            link = dict(_links(cli, f"help {command}"))[f"/{command}"]

            # Assert
            assert link.run is False, "a click fills the input, as it always did"
            for flag in ANY_COMMAND_FLAGS:
                assert flag in link.tip, flag
            assert IDIOMS_BY_NAME["json"].summary in link.tip, "the answer, not a pointer to one"
            assert IDIOMS_BY_NAME["level"].summary in link.tip, "the answer, not a pointer to one"

    def test_the_tooltip_and_the_click(self, tmp_path):
        """idioms-one-table: in the TUI a word's tooltip says what it means, and the click opens the page."""
        from termapy.app import SerialTerminal

        # Arrange
        serial = DEFAULT_CFG["serial"]
        cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "quiet_start": True}
        path = tmp_path / "c" / "c.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(cfg))
        app = SerialTerminal(cfg, str(path))
        app.repl.global_root = tmp_path

        async def scenario() -> tuple[str, str]:
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.pause()
                field = app.query_one("#cmd", Input)
                field.value = "/help term.eol"
                field.focus()
                await pilot.pause()
                await pilot.press("enter")
                await until(pilot, lambda: "/term.eol {cr" in app._get_screen_text())
                link_id = next(
                    key for key, link in app._links.items() if link.command == "help cycle"
                )
                tooltip = app._describe_link(link_id)

                # Act
                app.action_click_link(link_id)
                await until(pilot, lambda: "/term.json" in app._get_screen_text())
                return tooltip, app._get_screen_text()

        # Assert
        tooltip, screen = asyncio.run(scenario())
        assert tooltip == f"{IDIOM_TOKENS['cycle'].summary}\nClick to run: /help cycle"
        assert "TAKEN BY" in screen, "the page lists what takes it"


class TestOnACustomButton:
    def test_a_button_switches_the_setting_on_every_click(self, tmp_path):
        """The pages say toggle and cycle suit a custom button, so a click flips and steps, and again."""
        from termapy.app import SerialTerminal

        # Arrange
        buttons = [
            {"enabled": True, "name": "Hex", "command": "/term.hex toggle", "tooltip": ""},
            {"enabled": True, "name": "Lvl", "command": "/term.output cycle", "tooltip": ""},
        ]
        serial = DEFAULT_CFG["serial"]
        cfg = {
            **DEFAULT_CFG,
            "serial": {**serial, "port": "DEMO"},
            "quiet_start": True,
            "custom_buttons": buttons,
        }
        path = tmp_path / "c" / "c.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(cfg))
        app = SerialTerminal(cfg, str(path))
        app.repl.global_root = tmp_path

        async def click(pilot, button_id: str, flag: str, expected) -> None:
            button = app.query_one(f"#{button_id}", Button)
            await until(pilot, lambda: not button.has_class("-active"))
            await pilot.click(button)
            await until(pilot, lambda: app.repl.ctx.ns("flags")[flag] == expected)

        async def scenario() -> None:
            async with app.run_test(size=(160, 50)) as pilot:
                await pilot.pause()
                flags = app.repl.ctx.ns("flags")
                assert flags["hex"] is False, "precondition: hex starts off"
                assert flags["output_level"] == "normal", "precondition: the default level"

                # Act / Assert -- until fails the test if a click does not land.
                await click(pilot, "btn-custom-0", "hex", True)
                await click(pilot, "btn-custom-0", "hex", False)
                await click(pilot, "btn-custom-1", "output_level", "verbose")
                await click(pilot, "btn-custom-1", "output_level", "silent")

        asyncio.run(scenario())


class TestALinkExplainsItself:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            ("help output_level", "How loud commands are (/term.output).\nValid: silent"),
            ("help serial.baud_rate", "Line speed in bits per second"),
            ("help term.hex", "Toggle hex display of incoming bytes."),
            ("help cycle", "Move a setting to its next value"),
            ("term.hex toggle", "Toggle hex display of incoming bytes."),
            ("term.hex.quiet", "Toggle hex display of incoming bytes."),
            ("help nosuchname", ""),
            ("nosuchname", ""),
        ],
    )
    def test_the_definition_comes_from_what_it_names(self, cli, command, expected):
        """link-explains-itself: a link to a key, a command or an idiom says what that is, with the prefix filled in."""
        # Act
        actual = cli.repl.link_summary(command)

        # Assert
        assert actual.startswith(expected), actual
        assert "{prefix}" not in actual

    def test_a_device_command_says_what_its_profile_says(self, cli):
        """link-explains-itself: a device command's definition is its profile's, as /help reads it."""
        # Arrange
        profile = resources.files("termapy.boards") / "pico" / "profile.json"
        cli.repl.dispatch(f"profile.load {profile}")
        view = profile_command_view(cli.repl.ctx.ns("active_profile"))
        name, device = next(
            (name, device) for name, device in view.items() if name not in cli.repl._plugins
        )

        # Act
        actual = cli.repl.link_summary(f"help {name}")

        # Assert
        assert actual == device.help

    def test_a_hint_that_is_a_function_stays_out(self, cli):
        """A tooltip runs as the mouse moves, so a key whose valid values come from looking is not asked."""
        actual = [
            path
            for path in cfg_key_paths()
            if callable(CFG_HELP[path.rpartition(".")[2]][1])
            and "Valid:" in cli.repl.help_summary(path)
        ]
        assert actual == []

    @pytest.mark.parametrize("name", ["output_level", "term.hex", "cycle", "color", "verbose"])
    def test_the_tooltip_and_the_page_open_alike(self, cli, capsys, name):
        """link-explains-itself: the tooltip is the page's first line, so the two share a lookup order."""
        # Arrange
        expected = cli.repl.help_summary(name).split("\n")[0]

        # Act
        page = _page(cli, capsys, f"/help {name}")

        # Assert
        assert expected, "precondition: the name has a definition"
        assert expected in page.splitlines()[1], page.splitlines()[1]

    def test_a_config_key_on_a_page_says_what_it_is(self, tmp_path):
        """The link on /help term.output's CONFIG row hovers with the definition, then what a click does."""
        from termapy.app import SerialTerminal

        # Arrange
        serial = DEFAULT_CFG["serial"]
        cfg = {**DEFAULT_CFG, "serial": {**serial, "port": "DEMO"}, "quiet_start": True}
        path = tmp_path / "c" / "c.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(cfg))
        app = SerialTerminal(cfg, str(path))
        app.repl.global_root = tmp_path

        async def scenario() -> str:
            async with app.run_test(size=(120, 60)) as pilot:
                await pilot.pause()
                field = app.query_one("#cmd", Input)
                field.value = "/help term.output"
                field.focus()
                await pilot.pause()
                await pilot.press("enter")
                await until(pilot, lambda: "CONFIG" in app._get_screen_text())
                link_id = next(
                    key for key, link in app._links.items() if link.command == "help output_level"
                )
                return app._describe_link(link_id)

        # Act
        actual = asyncio.run(scenario())

        # Assert
        assert actual.startswith("How loud commands are"), actual
        assert "Valid: silent" in actual, "the values save the trip"
        assert actual.endswith("Click to run: /help output_level"), (
            "and the click still offers more"
        )


class TestAPluginGetsAllOfIt:
    @pytest.fixture
    def plugin(self, cli):
        """The example in writing-plugins.md, loaded as a real plugin, so the page cannot drift from the code."""
        page = Path(termapy.__file__).parent / "help" / "writing-plugins.md"
        section = page.read_text(encoding="utf-8").split("## Links: what a click does")[1]
        code = section.split("```python\n")[1].split("```")[0]
        folder = Path(cli.repl.config_path).parent / "plugin"
        folder.mkdir()
        (folder / "sensors.py").write_text(code, encoding="utf-8")
        cli._fire_app_start()
        return cli

    def test_a_tables_column_declares_the_click(self, plugin):
        """links-are-data: a plugin's Column(run=) makes each row a link that runs at a click."""
        # Act
        links = dict(_links(plugin, "sensors"))

        # Assert
        assert links["temp"].command == "sensors.read temp"
        assert links["temp"].run is True

    def test_a_line_of_pieces_carries_its_links(self, plugin):
        """A plugin imports Link and Piece from termapy.plugins and builds a line with them."""
        # Act
        links = dict(_links(plugin, "sensors.read temp"))

        # Assert
        assert links["/sensors"].command == "sensors"
        assert links["eol"].command == "help eol"

    def test_its_help_line_is_the_tooltip(self, plugin):
        """link-explains-itself: the plugin wrote one help= line and every link to it says that."""
        assert plugin.repl.link_summary("sensors.read temp") == "Read one sensor."
        assert plugin.repl.link_summary("help sensors.read") == "Read one sensor."

    def test_its_table_is_data_for_a_script(self, plugin):
        """list-one-format: the same records reach --json, with no link in them."""
        actual = plugin.repl.dispatch("sensors.list").data
        assert actual == [{"name": "temp", "query": "TEMP?"}, {"name": "volt", "query": "VOLT?"}]
