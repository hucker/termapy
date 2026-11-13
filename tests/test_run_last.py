"""Tests for /run.last -- list the lines typed at the prompt, and replay the newest of them.

Driven through a real ``CLITerminal`` on the DEMO port: ``_dispatch_typed`` is
the one feed of the typed lines, the path the CLI prompt and the TUI's Enter
both take, so each test types its lines through it.  The TUI's own wiring is
in ``tests/test_app_pilot.py::TestRunLast``; the CLI prompt loop end to end is
``test_the_cli_prompt_feeds_and_replays`` below.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from termapy.cli import CLITerminal
from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CmdResult, PluginInfo
from termapy.plugins.capabilities import state_message


def _write_cfg(folder, name: str) -> str:
    """A DEMO-port config on disk; returns its path."""
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    cfg = {**DEFAULT_CFG, "serial": {**default_serial, "port": "DEMO"}, "eol": "\r"}
    path = folder / name / f"{name}.cfg"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return str(path)


@pytest.fixture
def cli(tmp_path):
    """A started CLITerminal on a DEMO config, not connected."""
    path = _write_cfg(tmp_path, "one")
    with open(path, encoding="utf-8") as file:
        cfg = json.load(file)
    terminal = CLITerminal(cfg, path, no_color=True, term_width=120)
    # A temp global root keeps the checkout's termapy_cfg/plugin/ out.
    terminal.repl.global_root = tmp_path
    terminal._fire_app_start()
    return terminal


def _type(cli, *lines: str) -> list[CmdResult]:
    """Type each line at the prompt, as the CLI's loop does."""
    return [cli._dispatch_typed(line) for line in lines]


def _kept(cli) -> list[str]:
    """The active config's typed lines, as text."""
    return [line.text for line in cli.repl.typed_lines.lines(cli.repl.config_path)]


class TestList:
    def test_numbers_lines_that_succeeded_newest_first(self, cli):
        """Bare /run.last numbers each line that succeeded, newest = 1, and lists a failed one unnumbered with its error."""
        # Arrange
        _type(cli, "/print a", "/nosuch", "/print b")

        # Act
        [result] = _type(cli, "/run.last")

        # Assert
        expected = [
            {"number": 2, "command": "/print a", "success": True, "error": ""},
            {
                "number": None,
                "command": "/nosuch",
                "success": False,
                "error": "Unknown command: nosuch",
            },
            {"number": 1, "command": "/print b", "success": True, "error": ""},
        ]
        assert result.data == expected, "oldest first; only the lines that succeeded are numbered"

    def test_the_table_names_the_config(self, cli, capsys):
        """The listing's title names the config, since each config keeps its own lines."""
        # Arrange
        _type(cli, "/print a")
        capsys.readouterr()

        # Act
        _type(cli, "/run.last")

        # Assert
        actual = capsys.readouterr().out
        assert "typed in one - 1 line" in actual, "the title says whose lines these are"

    @pytest.mark.rule("typed-lines-one-feed")
    def test_run_last_is_never_kept(self, cli):
        """No spelling of /run.last, and no command around it, joins the lines it lists."""
        # Arrange
        _type(cli, "/print a")

        # Act
        _type(
            cli,
            "/run.last",
            "/run.last 1",
            "/RUN.LAST",
            "/run.last.verbose",
            "/repeat count=2 cmd=/run.last",
        )

        # Assert
        assert _kept(cli) == ["/print a"], "a line that read the typed lines is not one of them"

    @pytest.mark.rule("typed-lines-one-feed")
    def test_a_script_line_is_never_kept(self, cli):
        """A typed /run is kept, the script's own lines are not."""
        # Arrange
        run_dir = cli.repl.scripts_dir
        run_dir.mkdir(exist_ok=True)
        (run_dir / "two.run").write_text("/print in-script\n/print also\n", encoding="utf-8")

        # Act
        _type(cli, "/run two")

        # Assert
        assert _kept(cli) == ["/run two"], "only the line typed at the prompt"


class TestReplay:
    def test_a_replayed_run_runs_the_script_and_its_steps(self, cli, capsys):
        """Replaying a typed /run runs the script again, every step of it, and lists nothing new."""
        # Arrange
        run_dir = cli.repl.scripts_dir
        run_dir.mkdir(exist_ok=True)
        (run_dir / "two.run").write_text("/print step-one\n/print step-two\n", encoding="utf-8")
        _type(cli, "/run two")
        capsys.readouterr()

        # Act
        [result] = _type(cli, "/run.last 1")

        # Assert
        out = capsys.readouterr().out
        assert result.success, f"the replay succeeded: {result.error}"
        assert out.index("step-one") < out.index("step-two"), "the script ran both steps, in order"
        assert _kept(cli) == ["/run two"], "the list is still the one typed line"

    def test_replays_the_newest_in_the_order_typed(self, cli, capsys):
        """/run.last 2 runs numbered lines 2..1 oldest first and leaves older ones alone."""
        # Arrange
        _type(cli, "/print first", "/print second", "/print third")
        capsys.readouterr()

        # Act
        [result] = _type(cli, "/run.last 2")

        # Assert
        out = capsys.readouterr().out
        assert result.success, f"the replay succeeded: {result.error}"
        assert result.value == 2, "the value is how many lines ran"
        assert "first" not in out, "line 3 is older than the replay asked for"
        assert out.index("second") < out.index("third"), "the lines run in the order typed"

    def test_a_failed_line_is_skipped(self, cli, capsys):
        """A line that failed when typed is not replayed: the numbers skip it."""
        # Arrange
        _type(cli, "/print kept", "/nosuch")
        capsys.readouterr()

        # Act
        [result] = _type(cli, "/run.last 1")

        # Assert
        out = capsys.readouterr().out
        assert result.success, f"the replay succeeded: {result.error}"
        assert "kept" in out, "line 1 is the newest line that succeeded"
        assert "nosuch" not in out, "the failed line never runs again"

    @pytest.mark.rule("typed-lines-one-feed")
    def test_replayed_lines_are_not_kept_and_a_second_replay_repeats_the_first(self, cli, capsys):
        """Two replays in a row run the same lines, and neither adds them to the list."""
        # Arrange
        _type(cli, "/print again")
        capsys.readouterr()

        # Act
        first, second = _type(cli, "/run.last 1", "/run.last 1")

        # Assert
        out = capsys.readouterr().out
        assert first.success and second.success, "both replays succeeded"
        assert out.count("again") >= 2, "each replay ran the line"
        assert _kept(cli) == ["/print again"], "a replayed line is not a typed one"

    def test_echoes_each_command_and_puts_echo_back(self, cli, capsys):
        """A replay echoes each command, though the CLI's echo_repl is off, and leaves it off."""
        # Arrange
        flags = cli.ctx.ns("flags")
        assert flags["echo_repl"] is False, "the CLI starts with echo_repl off"
        _type(cli, "/print echoed")
        capsys.readouterr()

        # Act
        _type(cli, "/run.last 1")

        # Assert
        out = capsys.readouterr().out
        assert "/print echoed" in out, "the command line is shown as it runs"
        assert flags["echo_repl"] is False, "echo_repl is the user's again after the replay"

    def test_asking_for_more_than_there_are_fails(self, cli):
        """A count past the numbered lines fails with how many there are, and runs nothing."""
        # Arrange
        _type(cli, "/print a", "/print b")

        # Act
        [result] = _type(cli, "/run.last 3")

        # Assert
        assert result.error == "Only 2 commands to replay"

    def test_with_nothing_typed_fails(self, cli):
        """A replay before anything succeeded at the prompt says there is nothing to replay."""
        # Arrange
        _type(cli, "/nosuch")

        # Act
        [result] = _type(cli, "/run.last 1")

        # Assert
        assert result.error == "No commands to replay."

    def test_stops_at_the_first_line_that_fails(self, cli, capsys):
        """A line that fails now ends the replay with its failure; the lines after it never run."""
        # Arrange -- a device line that went out while connected
        assert cli._connect(), "DEMO port should connect"
        _type(cli, "AT", "/print after")
        cli._disconnect()
        capsys.readouterr()

        # Act
        [result] = _type(cli, "/run.last 2")

        # Assert
        out = capsys.readouterr().out
        assert result.error == state_message("serial_connected", cli.prefix), "AT's own failure"
        assert "after" not in out, "nothing runs after the failure"
        assert out.count(result.error) == 1, "the failure is reported once"

    def test_a_stop_between_lines_ends_the_replay(self, cli, capsys):
        """A stop between lines (Escape) ends the replay, says how far it got, and is consumed."""
        # Arrange -- a command that stops whatever is running, as Escape does
        stop = cli.repl._script_stop

        def _handler(ctx, args: str) -> CmdResult:
            """Set the stop event, as Escape does."""
            stop.set()
            return CmdResult.ok(value="")

        cli.repl.register_plugin(
            PluginInfo(name="escape", args="", help="Press Escape.", handler=_handler)
        )
        _type(cli, "/escape", "/print after")
        stop.clear()
        capsys.readouterr()

        # Act
        [result] = _type(cli, "/run.last 2")

        # Assert
        out = capsys.readouterr().out
        assert result.value == 1, "one line ran before the stop"
        assert "Replay stopped after 1 of 2 commands." in out, "the stop is reported"
        assert "/print after" not in out, "the line after the stop never ran (it would echo)"
        assert not stop.is_set(), "the replay was outermost, so it consumed the stop"


class TestOnlyAtThePrompt:
    @pytest.mark.rule("typed-lines-one-feed")
    def test_a_script_cannot_run_it(self, cli, capsys):
        """A script that lists or replays is refused by the gate, and the typed lines never run."""
        # Arrange
        _type(cli, "/print typed-line")
        run_dir = cli.repl.scripts_dir
        run_dir.mkdir(exist_ok=True)
        (run_dir / "replay.run").write_text("/run.last\n/run.last 1\n", encoding="utf-8")
        capsys.readouterr()

        # Act
        _type(cli, "/run replay")

        # Assert
        out = capsys.readouterr().out
        refusal = state_message("script_idle", cli.prefix)
        assert out.count(refusal) == 2, "both script lines are refused"
        assert "typed-line" not in out, "nothing typed was listed or replayed"

    @pytest.mark.rule("typed-lines-one-feed")
    def test_a_replay_inside_a_replay_is_refused(self, cli):
        """A replayed line that starts a replay of its own is refused; the next replay still runs."""
        # Arrange -- a command that dispatches its target, empty when typed
        target: list[str] = []

        def _handler(ctx, args: str) -> CmdResult:
            """Dispatch the target line, if one is set."""
            if not target:
                return CmdResult.ok(value="")
            return ctx.dispatch(target[0], report=False)

        cli.repl.register_plugin(
            PluginInfo(name="later", args="", help="Run the target.", handler=_handler)
        )
        _type(cli, "/later")
        target.append("/run.last 1")

        # Act
        [nested] = _type(cli, "/run.last 1")
        target.clear()
        [after] = _type(cli, "/run.last 1")

        # Assert
        assert nested.error == "A replay is already running.", "the inner replay is refused"
        assert after.success, f"a replay after the refusal runs: {after.error}"


class TestPerConfig:
    def test_lines_follow_the_config(self, cli, tmp_path):
        """A config switch shows that config's lines; switching back brings the first config's back."""
        # Arrange
        one = cli.repl.config_path
        two = _write_cfg(tmp_path, "two")
        _type(cli, "/print in-one")

        # Act
        cli._switch_to_cfg_path(two)
        in_two = _kept(cli)
        _type(cli, "/print in-two")
        cli._switch_to_cfg_path(one)
        back_in_one = _kept(cli)

        # Assert
        assert in_two == [], "a config nobody typed in starts empty"
        assert back_in_one == ["/print in-one"], "the first config's lines come back"

    @pytest.mark.rule("typed-lines-one-feed")
    def test_a_config_switch_is_kept_where_it_was_typed(self, cli, tmp_path):
        """A typed /cfg.load is kept in the list of the config it was typed in, not the one it loads."""
        # Arrange
        one = cli.repl.config_path
        two = _write_cfg(tmp_path, "two")

        # Act
        [result] = _type(cli, f"/cfg.load {two}")

        # Assert
        assert result.success, f"the config loaded: {result.error}"
        assert _kept(cli) == [], "nothing was typed in the config just loaded"
        actual = [line.text for line in cli.repl.typed_lines.lines(one)]
        assert actual == [f"/cfg.load {two}"], "the line is in the list it was typed in"


@pytest.mark.slow
def test_the_cli_prompt_feeds_and_replays(tmp_path):
    """Lines typed at the CLI prompt are listed by /run.last and replayed by /run.last N."""
    # Arrange
    cfg_root = tmp_path / "termapy_cfg"
    cfg_root.mkdir()
    typed = "/print alpha\n/print beta\n/run.last\n/run.last 1\n"

    # Act
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; "
            "sys.argv = ['termapy', '--cli', '--demo', '--no-color', "
            f"'--cfg-dir', {str(cfg_root)!r}]; "
            "from termapy.entry import main; main()",
        ],
        input=typed,
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Assert
    out = result.stdout
    assert "typed in demo - 2 lines" in out, f"the typed lines are listed:\n{out}\n{result.stderr}"
    assert out.count("beta") == 4, "beta: its output, its row, the replay's echo and output"
