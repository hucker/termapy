"""A family of commands says what it is for (registry family-has-intro).

``Command.intro`` is validated at construction and copied to ``PluginInfo``.
It shows above the generic subcommand list and first on the ``/help`` page,
through ``resolve_long_help``, so ``/search`` and the MCP catalog read it too.
The guard that every family declares one is in ``test_architecture.py``.
"""

from __future__ import annotations

import json

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import Command, resolve_long_help
from termapy.plugins.command import INTRO_WIDTH, intro_text

pytestmark = pytest.mark.rule("family-has-intro")


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


def _unwrapped(text: str) -> str:
    """``text`` with its wrapping and indentation taken out, so a sentence can be looked for."""
    return " ".join(text.split())


def _unused(ctx, args):
    """A handler for a declaration test."""
    return None


class TestTheDeclaration:
    @pytest.mark.rule("family-has-intro")
    def test_a_leaf_cannot_declare_one(self):
        """An intro introduces a family, so a command with no subcommands fails at load."""
        with pytest.raises(ValueError, match="intro= introduces a family"):
            Command(name="x", help="h", handler=_unused, intro="What it is for.")

    def test_a_family_can(self):
        """A command with subcommands takes one, and the loader carries it to the PluginInfo."""
        child = Command(help="c", handler=_unused)
        command = Command(name="x", help="h", intro="What it is for.", sub_commands={"c": child})
        assert command.intro == "What it is for."

    def test_the_built_ins_reach_the_table(self, cli):
        """A loaded family carries its intro; a leaf has none."""
        assert "XMODEM or YMODEM" in cli.repl._plugins["xfer"].intro
        assert cli.repl._plugins["term.send"].intro == ""

    def test_the_text_fills_the_prefix_and_wraps(self):
        """Shown, the intro has the live prefix and fits the page's width."""
        actual = intro_text("Use {prefix}mem to read. " + "word " * 40, "!")
        assert actual.startswith("Use !mem to read."), actual
        assert max(map(len, actual.splitlines())) <= INTRO_WIDTH

    def test_no_intro_is_no_text(self):
        """A command without one adds nothing to its page."""
        assert intro_text("", "/") == ""


class TestTheBareList:
    @pytest.mark.parametrize(
        ("line", "words"),
        [
            ("/dev", "registers as data"),
            ("/xfer", "XMODEM or YMODEM"),
            ("/app", "as a whole"),
            ("/xfer.xmodem", "128-byte blocks"),
            ("/proto.crc", "named algorithms"),
        ],
    )
    def test_it_says_what_the_family_is_for_above_the_list(self, cli, capsys, line, words):
        """A bare call that lists subcommands opens with the intro, then the list as before, a family or a lower node alike."""
        # Act
        out = _page(cli, capsys, line)

        # Assert
        assert words in _unwrapped(out), out
        flat = _unwrapped(out)
        assert flat.index(words) < flat.index("COMMAND DESCRIPTION"), "the intro comes first"
        assert f"{line} - " in out and "subcommand" in out, "the table is still there"

    def test_a_row_is_short(self, cli, capsys):
        """A row is the name and its one line: the full argument list, which ran off the screen, is on the help page."""
        out = _page(cli, capsys, "/dev")
        assert "/dev.import" in out, out
        assert "format=" not in out, f"a row carries no argument list:\n{out}"

    @pytest.mark.rule("list-one-format")
    def test_a_name_opens_that_commands_help(self, cli):
        """The click is declared on the column, so a row's name is a link to its help page."""
        # Arrange
        links = []
        cli.repl.ctx.io._write_segments = lambda pieces, color: links.extend(
            piece.link for piece in pieces if piece.link is not None
        )

        # Act
        cli.repl.dispatch("xfer")

        # Assert
        actual = {link.command: link.run for link in links}
        expected = {"help xfer.root": True, "help xfer.xmodem": True, "help xfer.ymodem": True}
        assert actual == expected, "reading a help page changes nothing, so the click runs it"

    def test_json_is_the_records_and_no_prose(self, cli, capsys):
        """--json answers with the same rows as data, and the intro stays on the help page."""
        # Act
        envelope = json.loads(_page(cli, capsys, "/xfer --json"))

        # Assert
        names = [record["command"] for record in envelope["data"]]
        assert names == ["xfer.root", "xfer.xmodem", "xfer.ymodem"], envelope
        assert envelope["output_lines"] == [], "no prose beside the records"
        assert envelope["value"] == 3, "the value is the row count, an integer in the envelope"

    def test_a_hidden_child_stays_off_the_list(self, cli, capsys):
        """A hidden alias dispatches but is not listed, as on the help page."""
        # Arrange
        cli.repl._plugins["xfer.root"].hidden = True

        # Act
        out = _page(cli, capsys, "/xfer")

        # Assert
        assert "/xfer.root" not in out, out
        assert "/xfer.xmodem" in out, out

    def test_the_space_form_still_redirects(self, cli, capsys):
        """An argument names a subcommand as before: /xfer root reaches /xfer.root and prints no intro."""
        out = _page(cli, capsys, "/xfer root")
        assert "XMODEM or YMODEM" not in out, out


class TestThePage:
    @pytest.mark.parametrize(
        ("name", "words"),
        [
            ("cap", "Save device output"),
            ("port", "Connect to, disconnect from"),
            ("run", "A script is a text file"),
            ("xfer", "Move files between"),
            ("port.chip", "Identify the USB-serial chip"),
            ("term.eol", "What ends a line"),
            ("edit.run", "Open a .run file from run/"),
        ],
    )
    def test_the_description_opens_with_the_intro(self, cli, capsys, name, words):
        """A family's page says what it is for first, at any level, including families that had no description at all."""
        # Act
        page = _page(cli, capsys, f"/help {name}")

        # Assert
        lines = page.splitlines()
        first = lines[lines.index("DESCRIPTION") + 1]
        assert first.strip().startswith(words), page

    @pytest.mark.parametrize(
        ("name", "sentence"),
        [
            ("app", "What belongs to termapy as a whole"),
            ("xfer", "Move files between the host"),
            ("find", "Search the scrollback for matching lines"),
            ("show", "Reads a file and prints its contents"),
            ("ping", "Sends a command to the device"),
            ("seq", "Sequence counters are used"),
        ],
    )
    def test_a_sentence_moved_to_the_intro_appears_once(self, cli, capsys, name, sentence):
        """What the intro says, the long help no longer repeats."""
        page = _unwrapped(_page(cli, capsys, f"/help {name}"))
        assert page.count(sentence) == 1, page

    def test_search_finds_a_word_only_the_intro_has(self, cli, capsys):
        """The intro is part of the long help, so /search indexes it with no change of its own."""
        out = _page(cli, capsys, "/search transcript")
        assert "/log" in out, out

    def test_the_mcp_catalog_reads_it_through_the_same_function(self, cli):
        """resolve_long_help is what the catalog calls, so an agent gets the intro too."""
        text = resolve_long_help(cli.repl._plugins["xfer"], cli.repl.ctx)
        assert text.startswith("Move files between"), text

    def test_the_status_line_follows_the_intro(self, cli, capsys):
        """A family with a live status line keeps it, directly under the intro."""
        page = _page(cli, capsys, "/help port")
        assert page.index("Connect to, disconnect") < page.index("Not connected."), page
