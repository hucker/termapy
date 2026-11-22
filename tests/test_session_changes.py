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


@pytest.mark.rule("on-connect-is-the-config")
def test_what_the_configs_own_commands_set_is_no_change(cli):
    """A setter an on-connect command runs, a config key, a port setting or a flag, is the config's: nothing is unsaved."""
    # Act
    with cli._config_owns_its_changes():
        for line in ("/term.timestamps on", "/port.baud_rate 9600", "/term.echo on"):
            assert cli.repl.dispatch_full(line).success, line

    # Assert
    assert cli.session_changes() == [], "the config's own commands moved the baseline with them"


@pytest.mark.rule("on-connect-is-the-config")
def test_a_change_before_or_after_the_on_connect_run_still_counts(cli):
    """Only what the run moved is folded: a setting changed before it and one changed after it are both unsaved."""
    # Arrange -- a flag the CLI has (/term.line_no is the TUI's alone)
    assert cli.repl.dispatch_full("/term.echo on").success

    # Act
    with cli._config_owns_its_changes():
        cli.repl.dispatch_full("/term.timestamps on")
    cli.repl.dispatch_full("/term.hex on")

    # Assert
    assert sorted(_paths(cli)) == ["echo", "hex"], "the user's two changes, not the config's one"


@pytest.mark.rule("on-connect-is-the-config")
def test_two_on_connect_runs_in_a_row_leave_nothing_unsaved(cli):
    """A reconnect runs the commands again: twice over, each run is folded and nothing is unsaved."""
    seen = []
    for _ in range(2):
        with cli._config_owns_its_changes():
            cli.repl.dispatch_full("/term.timestamps on")
            cli.repl.dispatch_full("/term.echo on")
        seen.append(_paths(cli))
    assert seen == [[], []], "the second run moves nothing and folds nothing"


@pytest.mark.rule("cfg-save-one-way")
def test_cfg_save_writes_what_the_session_changed(cli):
    """/cfg.save writes a config key and a flag key, answers how many with their values, and counting starts again."""
    # Arrange
    cli.repl.dispatch_full("/term.timestamps on")
    cli.repl.dispatch_full("/term.echo on")

    # Act
    result = cli.repl.dispatch_full("/cfg.save")

    # Assert
    on_disk = json.loads(Path(cli.config_path).read_text(encoding="utf-8"))
    assert (result.success, result.value) == (True, 2), result.error
    assert result.data == [
        {"key": "echo", "value": True},
        {"key": "timestamps", "value": True},
    ], "each saved key with its value, in CFG_KEYS order"
    assert (on_disk["echo"], on_disk["timestamps"]) == (True, True), "both reached the file"
    assert cli.session_changes() == [], "the file and the session agree again"


@pytest.mark.rule("cfg-save-one-way")
def test_cfg_save_twice_in_a_row_writes_once(cli, capsys):
    """The second save in a row finds nothing: it answers 0 and says so."""
    # Arrange
    cli.repl.dispatch_full("/term.timestamps on")
    first = cli.repl.dispatch_full("/cfg.save")
    capsys.readouterr()

    # Act
    second = cli.repl.dispatch_full("/cfg.save")

    # Assert
    assert (first.value, second.value) == (1, 0), "the first saved one setting, the second none"
    assert "Nothing to save." in capsys.readouterr().out


@pytest.mark.rule("leave-asks-unsaved")
def test_a_cli_load_asks_before_dropping_unsaved_settings(cli, capsys):
    """At a terminal, /cfg.load with a setting unsaved asks first; under --yes the answer is yes and the load goes on."""
    # Arrange -- someone at the terminal, answering yes as -y does
    cli.can_ask = True
    cli._assume_yes = True
    cli.repl.dispatch_full("/term.timestamps on")
    capsys.readouterr()

    # Act
    result = cli._switch_to_cfg_path(cli.config_path)

    # Assert
    out = capsys.readouterr().out
    assert result.success, result.error
    assert "Load without saving timestamps? (/cfg.save keeps them) [y/N] y (--yes)" in out, out
    assert cli.session_changes() == [], "the load dropped the change, as answered"


@pytest.mark.rule("leave-asks-unsaved")
def test_a_cli_exit_asks_only_where_someone_can_answer(cli, capsys):
    """/exit with a setting unsaved leaves without a word on a pipe, and asks at a terminal."""
    # Arrange
    cli.repl.dispatch_full("/term.timestamps on")
    capsys.readouterr()

    # Act -- a pipe first (the rig's stdin), then a terminal answering yes
    cli.repl.dispatch_full("/exit")
    piped = (capsys.readouterr().out, cli._exit_requested)
    cli._exit_requested = False
    cli.can_ask, cli._assume_yes = True, True
    cli.repl.dispatch_full("/exit")
    asked = (capsys.readouterr().out, cli._exit_requested)

    # Assert
    assert "without saving" not in piped[0] and piped[1], "no one to ask: it leaves"
    assert "Exit without saving timestamps?" in asked[0] and asked[1], "asked, then left on yes"
