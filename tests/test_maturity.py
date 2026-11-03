"""maturity-declared-once: each command says how settled it is, and every surface reads that one value.

A family declares ``stable``, ``preview`` or ``prototype`` on its root
``Command``; subcommands inherit from the nearest ancestor that declares
one and may override it, and an undeclared root is stable.  These tests
pin the table, the inheritance, the levels the built-in families declare,
the surfaces that show it (``/help``, the MCP catalog, the once-a-session
prototype note), and the docs that must list every family that is not
stable: the README's Feature status table and a banner on each family's
help page.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import (
    MATURITY_COLORS,
    MATURITY_LEVELS,
    MATURITY_MEANINGS,
    Command,
    PluginInfo,
)
from termapy.plugins.loader import _flatten_command

REPO = Path(__file__).parent.parent
HELP = REPO / "src" / "termapy" / "help"

# The help page that documents each family that is not stable.  A family
# made preview or prototype without a page here fails the docs test below.
FAMILY_PAGES = {
    "mcp": "mcp-server.md",
    "dev": "devices.md",
    "sym": "symbols.md",
    "mem": "memory.md",
    "board": "boards.md",
    "firmware": "firmware.md",
}


def _noop(ctx, args):
    """A handler that does nothing, for declaration tests."""


@pytest.fixture
def cli(tmp_path) -> CLITerminal:
    """A CLI host over a throwaway config, every built-in registered."""
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": ""}}
    path = tmp_path / "c" / "c.cfg"
    path.parent.mkdir()
    path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path  # keep the checkout's plugin/ out of the boot
    return terminal


def _non_stable_roots(cli: CLITerminal) -> dict[str, str]:
    """``{root name: level}`` for every registered family root that is not stable."""
    return {
        name: info.maturity
        for name, info in cli.repl._plugins.items()
        if "." not in name and info.maturity != "stable"
    }


class TestTheTable:
    def test_the_levels_their_colors_and_meanings_are_one_table(self):
        """maturity-declared-once: three levels, each with a color and a meaning, nothing else."""
        assert MATURITY_LEVELS == ("stable", "preview", "prototype")
        expected_colors = {"stable": "green", "preview": "yellow", "prototype": "red"}
        assert MATURITY_COLORS == expected_colors, "green, yellow, red"
        assert set(MATURITY_MEANINGS) == set(MATURITY_LEVELS), "every level has a meaning"

    def test_an_unknown_level_fails_at_load(self):
        """maturity-declared-once: a level outside the table fails where it is declared."""
        with pytest.raises(ValueError, match="unknown maturity 'beta'"):
            Command(help="x", name="x", handler=_noop, maturity="beta")
        with pytest.raises(ValueError, match="unknown maturity 'beta'"):
            PluginInfo(name="x", args="", help="x", handler=_noop, maturity="beta")


class TestInheritance:
    def test_subcommands_inherit_and_any_may_override(self):
        """maturity-declared-once: the nearest declaration wins, in either direction."""
        # Arrange: a preview root; one child silent, one prototype with a
        # silent grandchild, one declared stable
        root = Command(
            help="root",
            name="fam",
            maturity="preview",
            sub_commands={
                "quiet": Command(help="inherits", handler=_noop),
                "new": Command(
                    help="prototype",
                    maturity="prototype",
                    sub_commands={"deep": Command(help="inherits prototype", handler=_noop)},
                ),
                "done": Command(help="stable", handler=_noop, maturity="stable"),
            },
        )
        # Act
        levels = {info.name: info.maturity for info in _flatten_command(root, "", "test")}
        # Assert
        expected = {
            "fam": "preview",
            "fam.quiet": "preview",
            "fam.new": "prototype",
            "fam.new.deep": "prototype",
            "fam.done": "stable",
        }
        assert levels == expected

    def test_an_undeclared_root_is_stable(self):
        """maturity-declared-once: a family that says nothing is stable, and so is everything under it."""
        root = Command(
            help="root", name="plain", sub_commands={"x": Command(help="x", handler=_noop)}
        )
        levels = {info.name: info.maturity for info in _flatten_command(root, "", "test")}
        assert set(levels.values()) == {"stable"}


class TestTheFamilies:
    def test_the_built_in_families_declare_their_levels(self, cli):
        """maturity-declared-once: MCP, devices, symbols and memory are preview; boards and firmware prototype."""
        actual = _non_stable_roots(cli)
        expected = {
            "mcp": "preview",
            "dev": "preview",
            "sym": "preview",
            "mem": "preview",
            "board": "prototype",
            "firmware": "prototype",
        }
        assert actual == expected, (
            "a family changed level: update README, its page and the decision log"
        )

    def test_the_readme_lists_every_family_that_is_not_stable(self, cli):
        """maturity-declared-once: README's Feature status table names exactly the families the code marks."""
        readme = (REPO / "README.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| [^|]+ \| `/(\w+)` \| (preview|prototype) \|$", readme, re.M))
        assert rows == _non_stable_roots(cli), "the README table and the declarations disagree"

    def test_each_family_page_carries_its_banner(self, cli):
        """maturity-declared-once: every non-stable family's help page opens with its status banner."""
        for family, level in _non_stable_roots(cli).items():
            assert family in FAMILY_PAGES, (
                f"/{family} is {level}: add its help page to FAMILY_PAGES"
            )
            page = (HELP / FAMILY_PAGES[family]).read_text(encoding="utf-8")
            assert f"**Status: {level}.**" in page, (
                f"{FAMILY_PAGES[family]} lacks its {level} banner"
            )


class TestSurfaces:
    def test_the_help_page_states_the_status(self, cli, capsys):
        """A command's help page has a STATUS section with its level and meaning."""
        capsys.readouterr()
        cli.repl.dispatch_full("/help sym")
        out = capsys.readouterr().out
        assert "STATUS" in out, "the section is there"
        assert f"preview - {MATURITY_MEANINGS['preview']}" in out, "the level and what it means"

    def test_the_group_table_names_each_level(self, cli, capsys):
        """/help.group is a table of levels, so its LEVEL column names each one, stable included."""
        # Arrange
        capsys.readouterr()
        # Act
        cli.repl.dispatch_full("/help.group devices")
        group = capsys.readouterr().out
        # Assert
        levels = {
            row.split()[0]: row.split()[1] for row in group.splitlines() if row.startswith("  /")
        }
        assert levels["/board"] == "prototype", "a prototype family"
        assert levels["/sym"] == "preview", "a preview family"
        assert levels["/profile"] == "stable", "and a stable one says so too"

    def test_a_row_is_quiet_about_its_level(self, cli, capsys):
        """maturity-declared-once: a row carries no level in its text: italic and a tooltip mark one not yet stable."""
        # Arrange
        pieces: list = []
        cli.repl.ctx.io._write_segments = lambda line, color: pieces.extend(line)

        # Act
        cli.repl.dispatch("help sym")
        cli.repl.dispatch("help term")

        # Assert
        named = {piece.text: piece for piece in pieces if piece.link is not None}
        preview, stable = named["sym.info"], named["term.hex"]
        assert "italic" in preview.style, "the inherited level marks the row"
        level, meaning = preview.link.tip
        assert level.text == "preview", "and names it"
        assert level.style == MATURITY_COLORS["preview"], "in its color"
        assert meaning.text == f" - {MATURITY_MEANINGS['preview']}", "and says what it means"
        assert "italic" not in stable.style, "stable is the rule, so it goes unmarked"
        assert stable.link.tip == "", "and says nothing"
        words = " ".join(piece.text for piece in pieces if piece.link is None).split()
        assert "preview" not in words, "no row prints the word"
        assert "stable" not in words, "or the other"

    def test_the_catalog_carries_each_level(self, cli):
        """The MCP catalog gives the server's level, every level's meaning, and each non-stable command's level."""
        from termapy.mcp.catalog import build_catalog

        catalog = build_catalog(cli.repl.ctx)
        entries = {entry["name"]: entry for entry in catalog["commands"]}
        assert catalog["maturity"]["server"] == "preview", "the server says it is a preview"
        assert catalog["maturity"]["levels"] == MATURITY_MEANINGS, "and what each level means"
        assert entries["/sym.search"]["maturity"] == "preview", "inherited from /sym"
        assert "maturity" not in entries["/port.list"], "omitted when stable, as safety is"

    def test_a_prototype_family_notes_itself_once_a_session(self, cli, capsys):
        """The first run of a prototype family prints one note; a second run and a preview family print none."""
        # Arrange
        capsys.readouterr()
        # Act: firmware twice, then board, then a preview family
        for line in ("/firmware.list", "/firmware.list", "/board.list", "/sym.info"):
            cli.repl.dispatch_full(line)
        # Assert
        notes = [
            line.strip()
            for line in capsys.readouterr().out.splitlines()
            if "is a prototype" in line
        ]
        expected = [
            "Note: /firmware is a prototype; it may change or be removed.",
            "Note: /board is a prototype; it may change or be removed.",
        ]
        assert notes == expected, "one note per prototype family, none for preview"
