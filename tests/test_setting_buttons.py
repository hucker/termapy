"""A setting button shows its setting as the bare command answers it (registry ``setting-button-reads-its-command``).

The engine half on the connected DEMO rig: which setting a button's command
names (``ReplEngine.setting_command``) and the text its ``{value}`` shows
(``setting_text``).  The TUI half, the labels after a click, a typed command
and a disconnect, is ``TestHardwareButtons`` in ``test_app_pilot.py``.
"""

from __future__ import annotations

import pytest

from termapy.defaults import DEFAULT_CFG
from termapy.repl import SETTING_NO_VALUE
from termapy.scripting import value_text

pytestmark = pytest.mark.rule("setting-button-reads-its-command")


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("port.dtr toggle", "port.dtr"),
        ("term.timestamps TOGGLE", "term.timestamps"),
        ("term.eol cycle", "term.eol"),
        ("port.baud_rate cycle", "port.baud_rate"),
        ("port.break", None),
        ("port.dtr on", None),
        ("port.dtr", None),
        ("port.list toggle", None),
        ("teapot toggle", None),
    ],
)
def test_only_a_toggle_or_cycle_on_a_state_command_names_a_setting(cli, line, expected):
    """A button shows a value only when its click flips or advances a setting the bare command reports."""
    assert cli.repl.setting_command(line) == expected, (
        "an action, a set, a query, a listing or an unknown name shows no value"
    )


@pytest.mark.parametrize(
    ("change", "command"),
    [
        ("/port.dtr 0", "port.dtr toggle"),
        ("/port.dtr 1", "port.dtr toggle"),
        ("/term.timestamps on", "term.timestamps toggle"),
        ("/term.timestamps off", "term.timestamps toggle"),
        ("/term.eol crlf", "term.eol cycle"),
        ("/port.baud_rate cycle", "port.baud_rate cycle"),
    ],
)
def test_the_value_is_what_the_bare_command_answers(cli, change, command):
    """After a change the button's text is value_text of the bare command's value, the line a person reads."""
    # Arrange
    assert cli.repl.dispatch_full(change).success, change
    bare = cli.repl.dispatch_full(f"/{command.split()[0]}")

    # Act
    actual = cli.repl.setting_text(command)

    # Assert
    assert actual == value_text(bare.value), f"{command} shows what bare answers after {change}"


def test_reading_twice_prints_nothing_and_leaves_the_line(cli, capsys):
    """Two reads in a row answer the same, print nothing and leave DTR where the last command put it."""
    # Arrange
    assert cli.repl.dispatch_full("/port.dtr 0").success, "DTR drops"
    port = cli.engine.port_obj
    capsys.readouterr()

    # Act
    actual = [cli.repl.setting_text("port.dtr toggle") for _ in range(2)]

    # Assert
    assert actual == ["0", "0"], "a read is a query, never the click it reads for"
    assert port.dtr is False, "the line stays low"
    assert capsys.readouterr().out == "", "a button's read prints nothing"


def test_every_default_value_button_names_a_setting(cli):
    """Each shipped button whose name shows {value} toggles or cycles a setting, so none ships showing -."""
    # Arrange
    commands = [
        button["command"].replace("{prefix}", "")
        for button in DEFAULT_CFG["custom_buttons"]
        if "{value}" in button["name"]
    ]

    # Act
    actual = {command: cli.repl.setting_command(command) for command in commands}

    # Assert
    assert commands, "the defaults ship value buttons"
    assert all(actual.values()), f"a default button reads no setting: {actual!r}"


def test_no_port_shows_a_dash(cli):
    """With the port closed the gate refuses the read, and the button shows - rather than a stale level."""
    cli._disconnect()
    assert cli.repl.setting_text("port.dtr toggle") == SETTING_NO_VALUE, "no port, no level"
