"""What a session changed from its config file, and saving it (registry ``session-changes-one-diff``).

``config.session_changes`` is the one answer, through
``TerminalHost.session_changes``; the setting buttons' ``*`` and the config
editor's prompt read it, and ``TerminalHost.save_session_changes`` writes
it.  Driven on the connected DEMO rig (``cli``), which starts with
``--no-color``, a launch overlay, over a config whose ``color`` is on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.rule("session-changes-one-diff")


def _paths(cli) -> list[str]:
    """The paths of what the session changed, in order."""
    return [change.path for change in cli.session_changes()]


def test_a_fresh_load_has_no_changes(cli):
    """Right after a load nothing differs, the launch's --no-color included."""
    assert cli.session_changes() == [], "a load is where counting starts"


def test_each_kind_of_setter_is_a_change(cli):
    """A config setter, a port setter and a flag setter each count, in CFG_KEYS order, with both values."""
    # Arrange
    for line in ("/term.timestamps on", "/port.baud_rate 9600", "/term.echo on"):
        assert cli.repl.dispatch_full(line).success, line

    # Act
    actual = [(change.path, change.saved, change.session) for change in cli.session_changes()]

    # Assert
    expected = [
        ("serial.baud_rate", 115200, 9600),
        ("echo", False, True),
        ("timestamps", False, True),
    ]
    assert actual == expected, "echo is read from its flag; its config value stays as loaded"


def test_a_change_undone_is_no_change(cli):
    """On then off again, twice over, leaves nothing to save."""
    seen = []
    for _ in range(2):
        cli.repl.dispatch_full("/term.timestamps on")
        cli.repl.dispatch_full("/term.timestamps off")
        seen.append(_paths(cli))
    assert seen == [[], []], "back where the file is, nothing differs"


def test_a_launch_overlay_is_never_a_change(cli):
    """--no-color seeded color off over the file's on: color on, or off again, is no change."""
    seen = []
    for line in ("/term.color on", "/term.color off"):
        cli.repl.dispatch_full(line)
        seen.append(_paths(cli))
    assert seen == [[], []], "on is the file's value and off is the launch's"


def test_a_save_writes_the_changes_over_the_file_as_written(cli):
    """Save puts the session's values in and leaves the rest as the file has it, a $(env.X) template included."""
    # Arrange
    path = Path(cli.config_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["title"] = "$(env.PATH)"
    path.write_text(json.dumps(raw), encoding="utf-8")
    for line in ("/term.timestamps on", "/term.echo on"):
        cli.repl.dispatch_full(line)

    # Act
    saved = cli.save_session_changes()

    # Assert
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert [change.path for change in saved] == ["echo", "timestamps"], "what was written"
    actual = (on_disk["echo"], on_disk["timestamps"], on_disk["title"])
    assert actual == (True, True, "$(env.PATH)"), "the changes in, the template kept"
    assert cli.cfg["echo"] is True, "a flag key's config value is brought up to its flag"


def test_a_second_save_writes_nothing(cli):
    """Twice in a row: after a save nothing is left, and a second save leaves the file byte for byte."""
    # Arrange
    cli.repl.dispatch_full("/term.eol crlf")
    cli.save_session_changes()
    before = Path(cli.config_path).read_bytes()

    # Act
    second = cli.save_session_changes()

    # Assert
    actual = (second, cli.session_changes(), Path(cli.config_path).read_bytes())
    assert actual == ([], [], before), "the save made the file the new starting point"


def test_a_load_ends_the_changes(cli):
    """A load of the same config starts counting again from its file."""
    # Arrange
    cli.repl.dispatch_full("/term.timestamps on")

    # Act
    result = cli._switch_to_cfg_path(cli.config_path)

    # Assert
    assert result.success, result.error
    assert cli.session_changes() == [], "a load ends where a fresh start would"


@pytest.mark.rule("quick-config-port-is-name")
def test_a_quick_sessions_port_is_never_a_change(tmp_path):
    """In a quick session the port is the config's name: a port set for the session is not offered for saving, another setting is."""
    import copy

    from termapy.cli import CLITerminal
    from termapy.defaults import DEFAULT_CFG

    # Arrange
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**copy.deepcopy(DEFAULT_CFG), "serial": {**default_serial, "port": "DEMO"}}
    config_path = tmp_path / "quick-DEMO" / "quick-DEMO.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=120)
    terminal.repl.global_root = tmp_path

    # Act
    for line in ("/cfg port COM9", "/term.timestamps on"):
        assert terminal.repl.dispatch_full(line).success, line

    # Assert
    assert _paths(terminal) == ["timestamps"], "the port is the name; timestamps is a change"
