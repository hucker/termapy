"""The grouped /help landscape: what a first session sees, and how a group is reached.

A command declares what it is FOR and ``/help`` lays itself out from the
declarations (registry ``help-group-declared``): the terminal group in
full, every other group one folded row that lists its members.  Folding
is display only -- every folded command still dispatches, still
completes, still answers ``/help <name>``.

The structural halves of the rule (every root declares a group, a family
shares its root's, the landscape never reads ``source``) are guarded in
``tests/test_architecture.py``; what follows is the behavior a user sees.
"""

from __future__ import annotations

import json

import pytest

from termapy.markup import markup_to_plain
from termapy.plugins import GROUP_TERMINAL, CmdResult, Command, PluginInfo
from termapy.plugins.views import Piece


def _engine(tmp_path):
    """A real CLI host's engine over a throwaway config, with every built-in registered."""
    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    config_path = tmp_path / "c" / "c.cfg"
    config_path.parent.mkdir()
    serial = DEFAULT_CFG["serial"]
    assert isinstance(serial, dict), "serial is a section"
    cfg = {**DEFAULT_CFG, "serial": {**serial, "port": ""}}
    config_path.write_text(json.dumps(cfg))
    return CLITerminal(cfg, str(config_path), no_color=True, term_width=120).repl


def _shown(eng, command: str) -> list[str]:
    """The plain lines a frontend prints while ``command`` runs."""
    lines: list[str] = []
    eng.ctx.io._write_markup = lambda markup: lines.append(markup_to_plain(markup))
    eng.ctx.io._write_segments = lambda pieces, color: lines.append(
        markup_to_plain("".join(piece.text for piece in pieces))
    )
    eng.dispatch(command)
    return lines


def _pieces(eng, command: str) -> list[list[Piece]]:
    """The lines of pieces a clicking frontend receives while ``command`` runs."""
    lines: list[list[Piece]] = []
    eng.ctx.io._write_segments = lambda pieces, color: lines.append(list(pieces))
    eng.dispatch(command)
    return lines


def test_the_landscape_shows_the_terminal_group_in_full(tmp_path):
    """A first session reads the terminal commands themselves, not a count."""
    # Arrange / Act
    shown = _shown(_engine(tmp_path), "help")

    # Assert
    assert any(line.strip() == "-- Terminal --" for line in shown), "the group is a section"
    for name in ("/port", "/log", "/run", "/help"):
        assert any(line.strip().startswith(f"{name} ") for line in shown), (
            f"{name} is a terminal command, so the landscape shows its row"
        )


def test_every_other_group_is_one_folded_row(tmp_path):
    """The groups a newcomer does not need yet cost one line each, not one line per command."""
    # Arrange / Act
    shown = _shown(_engine(tmp_path), "help")

    # Assert -- the folded groups are rows; their members are not
    assert any(line.strip() == "-- More --" for line in shown), "the folded groups have a section"
    folded = [line for line in shown if "/help.group devices" in line]
    assert len(folded) == 1, "the devices group is exactly one row"
    assert not any(line.strip().startswith("/mem ") for line in shown), (
        "a folded group's members are named on its row, never given rows of their own"
    )


def test_a_folded_row_names_its_commands(tmp_path):
    """The row is derived from the registry -- no table of descriptions to go stale."""
    # Arrange / Act
    shown = _shown(_engine(tmp_path), "help")

    # Act
    row = next(line for line in shown if "/help.group devices" in line)

    # Assert
    assert "6 commands" in row, "the row counts what it folds"
    for name in ("board", "dev", "firmware", "mem", "profile", "sym"):
        assert name in row, f"{name} is a devices command, so the row names it"


def test_a_folded_row_links_to_its_group(tmp_path):
    """Clicking a folded row opens the group: the link is data, never markup in the text."""
    # Arrange / Act
    lines = _pieces(_engine(tmp_path), "help")

    # Act
    row = next(
        pieces for pieces in lines if any("help.group devices" in piece.text for piece in pieces)
    )
    links = {piece.link.command for piece in row if piece.link is not None}

    # Assert
    assert links == {"help.group devices"}, (
        "every clickable piece of the row opens that group, so a click anywhere on it works"
    )


def test_expanding_a_group_lists_its_commands(tmp_path):
    """/help.group <name> is the full listing the fold row stands in for."""
    # Arrange / Act
    shown = _shown(_engine(tmp_path), "help.group devices")

    # Assert
    for name in ("/board", "/mem", "/sym"):
        assert any(line.strip().startswith(f"{name} ") for line in shown), (
            f"{name} has its own row once the group is expanded"
        )


def test_bare_help_group_lists_every_group(tmp_path):
    """Bare /help.group answers the groups themselves, the shown one included."""
    # Arrange / Act
    eng = _engine(tmp_path)
    shown = _shown(eng, "help.group")

    # Assert
    groups = {line.split()[0] for line in shown if line.strip()}
    for group in ("terminal", "devices", "protocol", "advanced"):
        assert group in groups, (
            f"{group} is a group of the loaded commands, so bare /help.group has its row"
        )


def test_a_star_expands_every_group(tmp_path):
    """/help.group * lists every visible root, each with its group, under a GROUP column."""
    # Arrange
    eng = _engine(tmp_path)
    roots = {name for name, plugin in eng.ctx.internal.plugins.items() if "." not in name}
    # Act
    result = eng.dispatch_full("/help.group *")
    # Assert
    assert result.success, result.error
    assert {record["command"] for record in result.data} == roots, "every visible root, once"
    assert {record["group"] for record in result.data} >= {"terminal", "devices"}, (
        "rows from more than one group"
    )


def test_a_glob_expands_the_groups_it_matches(tmp_path):
    """A pattern with a wildcard picks groups by the shared matcher; one match lists that group alone."""
    eng = _engine(tmp_path)
    result = eng.dispatch_full("/help.group dev*")
    assert result.success, result.error
    assert {record["group"] for record in result.data} == {"devices"}, "dev* is devices"


def test_a_glob_matching_nothing_fails_and_names_the_groups(tmp_path):
    """A pattern that picks no group fails like an unknown name, listing what exists."""
    result = _engine(tmp_path).dispatch_full("/help.group nope*")
    assert result.success is False, "nothing to expand is a failure"
    assert result.error.startswith("Unknown group: nope*"), result.error


@pytest.mark.rule("list-one-format")
def test_the_records_carry_group_command_level_and_help(tmp_path):
    """An expanded group's data is records of group, command, maturity and help."""
    # Arrange / Act
    result = _engine(tmp_path).dispatch_full("/help.group devices")
    # Assert
    board = next(record for record in result.data if record["command"] == "board")
    expected = {"group", "command", "maturity", "help"}
    assert set(board) == expected, "the record's fields"
    assert board["maturity"] == "prototype", "the level is data, not only display"


def test_a_styled_column_draws_its_cell_in_that_style():
    """A Column with style= gives its cell piece that style; the text stays the same."""
    from termapy.plugins.views import Column, TableView

    view = TableView(
        "t",
        "row",
        (Column("LEVEL", lambda record: record["level"], style=lambda record: "yellow"),),
        ({"level": "preview"},),
    )
    row_pieces = view.segment_lines()[-1][0]
    styled = [piece for piece in row_pieces if piece.text == "preview"]
    assert styled and styled[0].style == "yellow", "the cell carries its style"
    assert view.lines()[-1][0] == "preview", "plain consumers see the text alone"


def test_an_unknown_group_fails_and_names_the_groups(tmp_path):
    """A typo answers with what IS there, the project's Unknown <thing> shape."""
    # Arrange / Act
    result = _engine(tmp_path).dispatch("help.group nope")

    # Assert
    assert result.success is False, "an unknown group is a failure, not an empty listing"
    assert result.error.startswith("Unknown group: nope"), (
        f"the standard phrasing names the group that was asked for: {result.error!r}"
    )
    assert "terminal" in result.error, "and the message lists the groups that exist"


def test_folding_hides_nothing_from_dispatch(tmp_path):
    """A folded command still runs and still answers /help: the fold is display only."""
    # Arrange
    eng = _engine(tmp_path)

    # Act
    listed = eng.dispatch("help mem")

    # Assert
    assert listed.success is True, (
        "/mem folds under devices, and /help mem still answers -- folding is a layout, not a gate"
    )


def test_a_plugin_chooses_its_own_group(tmp_path):
    """A plugin declaring terminal is shown among the built-ins; nothing about origin decides."""
    # Arrange
    eng = _engine(tmp_path)
    eng.register_plugin(
        PluginInfo(
            name="mine",
            args="",
            help="A command of my own.",
            handler=lambda ctx, args: CmdResult.ok(value=""),
            source="global",
            group=GROUP_TERMINAL,
        )
    )

    # Act
    shown = _shown(eng, "help")
    terminal_at = next(i for i, line in enumerate(shown) if line.strip() == "-- Terminal --")
    more_at = next((i for i, line in enumerate(shown) if line.strip() == "-- More --"), len(shown))
    mine_at = next(i for i, line in enumerate(shown) if line.strip().startswith("/mine "))

    # Assert
    assert terminal_at < mine_at < more_at, (
        "a user plugin that declares the terminal group is IN it, between the section "
        "heading and the folded groups -- where it came from is not where it appears"
    )


def test_a_plugin_may_declare_a_group_of_its_own(tmp_path):
    """The vocabulary is open: an unrecognized group becomes its own folded row."""
    # Arrange
    eng = _engine(tmp_path)
    eng.register_plugin(
        PluginInfo(
            name="spin",
            args="",
            help="Spin the motor.",
            handler=lambda ctx, args: CmdResult.ok(value=""),
            source="global",
            group="motor",
        )
    )

    # Act
    shown = _shown(eng, "help")

    # Assert
    row = next((line for line in shown if "/help.group motor" in line), "")
    assert "spin" in row, (
        "a group no built-in uses is still a group: it gets its own folded row, because "
        "only the NAME's shape is validated, never its membership"
    )


@pytest.mark.parametrize(
    "group",
    ["UPPER", "two words", "with/slash", ""],
)
def test_a_malformed_group_name_fails_at_load(group):
    """A group's SHAPE is validated at registration, so a bad one fails loud, not in /help."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="invalid group"):
        Command(
            name="bad",
            help="A command with a malformed group.",
            handler=lambda ctx, args: CmdResult.ok(value=""),
            group=group,
        )
