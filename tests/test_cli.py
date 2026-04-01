"""Unit tests for CLITerminal — hooks, helpers, output, and dispatch."""

from __future__ import annotations

import json
import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from termapy.cli import CLITerminal, _parse_run_flags
from termapy.plugins import CmdResult


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def cli(tmp_path):
    """Create a CLITerminal with dummy config and mocked serial engine."""
    cfg = {"port": "COM99", "baud_rate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "test_cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap", "prof"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    with patch("termapy.cli.SerialEngine") as mock_engine_cls:
        mock_engine = MagicMock()
        mock_engine.is_connected = False
        mock_engine.serial_port = None
        mock_engine.port_obj = None
        mock_engine.last_error = ""
        mock_engine_cls.return_value = mock_engine
        terminal = CLITerminal(cfg, str(config_path), no_color=True, term_width=80)

    return terminal


# -- _parse_run_flags --------------------------------------------------------


class TestParseRunFlags:
    def test_no_flags(self):
        # Act
        script, verbose = _parse_run_flags("myscript.run")

        # Assert
        assert script == "myscript.run"  # script name preserved
        assert verbose is False  # no verbose flag

    def test_verbose_short(self):
        # Act
        script, verbose = _parse_run_flags("-v myscript.run")

        # Assert
        assert script == "myscript.run"  # flag stripped from script name
        assert verbose is True  # verbose enabled

    def test_verbose_long(self):
        # Act
        script, verbose = _parse_run_flags("myscript.run --verbose")

        # Assert
        assert script == "myscript.run"  # flag stripped
        assert verbose is True  # verbose enabled

    def test_empty(self):
        # Act
        script, verbose = _parse_run_flags("")

        # Assert
        assert script == ""  # empty input returns empty
        assert verbose is False


# -- Output methods ----------------------------------------------------------


class TestOutput:
    def test_write_plain(self, cli, capsys):
        # Act
        cli.write("hello world")

        # Assert
        actual = capsys.readouterr().out
        assert "hello world" in actual  # text appears in stdout

    def test_write_with_color(self, cli, capsys):
        # Act
        cli.write("error msg", "red")

        # Assert
        actual = capsys.readouterr().out
        assert "error msg" in actual  # text appears even with color arg

    def test_status_indented(self, cli, capsys):
        # Act
        cli.status("info line")

        # Assert
        actual = capsys.readouterr().out
        assert "info line" in actual  # status text appears

    def test_status_with_color(self, cli, capsys):
        # Act
        cli.status("warning", "yellow")

        # Assert
        actual = capsys.readouterr().out
        assert "warning" in actual  # colored status appears

    def test_raw_output(self, cli, capsys):
        # Act
        cli._raw("raw text")

        # Assert
        actual = capsys.readouterr().out
        assert "raw text" in actual  # raw text bypasses Rich

    def test_err_output(self, cli, capsys):
        # Act
        cli._err("error text")

        # Assert
        actual = capsys.readouterr().err
        assert "error text" in actual  # error goes to stderr


# -- Hook: delay -------------------------------------------------------------


class TestHookDelay:
    def test_short_delay(self, cli):
        # Act
        result = cli._hook_delay(cli.ctx, "10ms")

        # Assert
        assert result.success  # short delay completes ok

    def test_invalid_duration(self, cli):
        # Act
        result = cli._hook_delay(cli.ctx, "xyz")

        # Assert
        assert not result.success  # invalid duration fails

    def test_delay_quiet(self, cli):
        # Act
        result = cli._hook_delay_quiet(cli.ctx, "10ms")

        # Assert
        assert result.success  # quiet delay completes ok

    def test_delay_quiet_invalid(self, cli):
        # Act
        result = cli._hook_delay_quiet(cli.ctx, "bad")

        # Assert
        assert not result.success  # invalid duration fails


# -- Hook: color -------------------------------------------------------------


class TestHookColor:
    def test_color_on(self, cli):
        # Arrange
        cli.console.no_color = True

        # Act
        result = cli._hook_color(cli.ctx, "on")

        # Assert
        assert result.success  # command succeeds
        assert cli.console.no_color is False  # color enabled

    def test_color_off(self, cli):
        # Arrange
        cli.console.no_color = False

        # Act
        result = cli._hook_color(cli.ctx, "off")

        # Assert
        assert result.success  # command succeeds
        assert cli.console.no_color is True  # color disabled

    def test_color_toggle_show(self, cli, capsys):
        # Arrange
        cli.console.no_color = True

        # Act
        result = cli._hook_color(cli.ctx, "")

        # Assert
        assert result.success  # status query succeeds
        actual = capsys.readouterr().out
        assert "off" in actual  # reports current state


# -- Hook: raw ---------------------------------------------------------------


class TestHookRaw:
    def test_raw_not_connected(self, cli):
        # Arrange
        cli.engine.is_connected = False

        # Act
        result = cli._hook_raw(cli.ctx, "hello")

        # Assert
        assert not result.success  # fails when disconnected

    def test_raw_no_args(self, cli):
        # Arrange
        cli.engine.is_connected = True

        # Act
        result = cli._hook_raw(cli.ctx, "")

        # Assert
        assert not result.success  # fails with no text

    def test_raw_sends_data(self, cli):
        # Arrange
        cli.engine.is_connected = True
        cli.engine.serial_port = MagicMock()

        # Act
        result = cli._hook_raw(cli.ctx, "AT")

        # Assert
        assert result.success  # command succeeds
        cli.engine.serial_port.write.assert_called_once_with(b"AT")  # bytes sent


# -- Hook: run ---------------------------------------------------------------


class TestHookRun:
    def test_run_no_script_no_dir(self, cli, tmp_path):
        # Arrange — run/ dir exists but is empty
        # Act
        result = cli._hook_run(cli.ctx, "")

        # Assert
        assert result.success  # listing with no files is ok

    def test_run_lists_scripts(self, cli, capsys):
        # Arrange
        run_dir = Path(cli.config_path).parent / "run"
        (run_dir / "test1.run").write_text("/echo hello")
        (run_dir / "test2.run").write_text("/echo world")

        # Act
        result = cli._hook_run(cli.ctx, "")

        # Assert
        assert result.success  # listing succeeds
        actual = capsys.readouterr().out
        assert "test1.run" in actual  # first script listed
        assert "test2.run" in actual  # second script listed

    def test_run_file_not_found(self, cli):
        # Act
        result = cli._hook_run(cli.ctx, "nonexistent.run")

        # Assert
        assert not result.success  # missing script fails


# -- Hook: log.clear ---------------------------------------------------------


class TestHookLogClear:
    def test_no_log_file(self, cli):
        # Act
        result = cli._hook_log_clear(cli.ctx, "")

        # Assert
        assert not result.success  # no log file to delete

    def test_delete_log(self, cli, tmp_path):
        # Arrange
        log_path_str = str(tmp_path / "test_cfg" / "test.log")
        Path(log_path_str).write_text("log data")

        # Act — patch where cfg_log_path is imported (inside the method)
        with patch("termapy.config.cfg_log_path", return_value=log_path_str):
            result = cli._hook_log_clear(cli.ctx, "")

        # Assert
        assert result.success  # deletion succeeds
        assert not Path(log_path_str).exists()  # file removed


# -- Connect / Disconnect ---------------------------------------------------


class TestConnect:
    def test_connect_already_connected(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = True

        # Act
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Already connected" in actual  # warns already connected

    def test_connect_success(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = False
        cli.engine.connect.return_value = True
        cli.engine.port_obj = MagicMock()

        # Act
        with patch("termapy.config.connection_string", return_value="COM99 115200"):
            with patch("termapy.config.hardware_signals", return_value=""):
                cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Connected" in actual  # reports connection

    def test_connect_failure(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = False
        cli.engine.connect.return_value = False

        # Act
        cli._connect()

        # Assert
        actual = capsys.readouterr().out
        assert "Cannot connect" in actual  # reports failure

    def test_connect_with_port(self, cli):
        # Arrange
        cli.engine.is_connected = False
        cli.engine.connect.return_value = True
        cli.engine.port_obj = MagicMock()

        # Act
        with patch("termapy.config.connection_string", return_value="COM5 115200"):
            with patch("termapy.config.hardware_signals", return_value=""):
                cli._connect(port="COM5")

        # Assert
        assert cli.cfg["port"] == "COM5"  # port updated in config

    def test_disconnect_not_connected(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = False

        # Act
        cli._disconnect()

        # Assert
        actual = capsys.readouterr().out
        assert "Not connected" in actual  # warns not connected

    def test_disconnect_success(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = True

        # Act
        cli._disconnect()

        # Assert
        cli.engine.disconnect.assert_called_once()  # engine disconnect called
        actual = capsys.readouterr().out
        assert "Disconnected" in actual  # reports disconnection


# -- serial_write_raw --------------------------------------------------------


class TestSerialWriteRaw:
    def test_not_connected(self, cli, capsys):
        # Arrange
        cli.engine.is_connected = False

        # Act
        cli._serial_write_raw("AT")

        # Assert
        actual = capsys.readouterr().out
        assert "Not connected" in actual  # warns not connected

    def test_sends_with_line_ending(self, cli):
        # Arrange
        cli.engine.is_connected = True
        cli.engine.serial_port = MagicMock()
        cli.cfg["line_ending"] = "\r\n"

        # Act
        cli._serial_write_raw("AT")

        # Assert
        cli.engine.serial_port.write.assert_called_once_with(b"AT\r\n")  # appends line ending


# -- Capture helpers ---------------------------------------------------------


class TestCapture:
    def test_start_capture_already_active(self, cli, capsys):
        # Arrange
        cli.capture = MagicMock()
        cli.capture.active = True

        # Act
        actual = cli._start_capture(mode="text", path="/tmp/cap.txt")

        # Assert
        assert actual is False  # returns False when already active

    def test_start_capture_success(self, cli, capsys):
        # Arrange
        cli.capture = MagicMock()
        cli.capture.active = False
        cli.capture.start.return_value = True

        # Act
        actual = cli._start_capture(mode="text", path="/tmp/cap.txt")

        # Assert
        assert actual is True  # returns True on success

    def test_stop_capture(self, cli, capsys):
        # Arrange
        mock_result = MagicMock()
        mock_result.path = "/tmp/cap.txt"
        mock_result.size_label = "1.2 KB"
        cli.capture = MagicMock()
        cli.capture.stop.return_value = mock_result

        # Act
        cli._stop_capture()

        # Assert
        actual = capsys.readouterr().out
        assert "Capture complete" in actual  # reports completion


# -- apply_port_effects ------------------------------------------------------


class TestApplyPortEffects:
    def test_cfg_update(self, cli):
        # Arrange
        effects = {"cfg_update": {"baud_rate": 9600}}

        # Act
        cli._apply_port_effects(effects)

        # Assert
        assert cli.repl._cfg_data["baud_rate"] == 9600  # config updated

    def test_empty_effects(self, cli):
        # Act / Assert — no exception on empty effects
        cli._apply_port_effects({})


# -- Confirm -----------------------------------------------------------------


class TestConfirm:
    def test_confirm_yes(self):
        # Act
        with patch("builtins.input", return_value="y"):
            actual = CLITerminal._confirm("Continue?")

        # Assert
        assert actual is True  # y means yes

    def test_confirm_no(self):
        # Act
        with patch("builtins.input", return_value="n"):
            actual = CLITerminal._confirm("Continue?")

        # Assert
        assert actual is False  # n means no

    def test_confirm_empty(self):
        # Act
        with patch("builtins.input", return_value=""):
            actual = CLITerminal._confirm("Continue?")

        # Assert
        assert actual is False  # empty defaults to no

    def test_confirm_eof(self):
        # Act
        with patch("builtins.input", side_effect=EOFError):
            actual = CLITerminal._confirm("Continue?")

        # Assert
        assert actual is False  # EOF returns False


# -- History -----------------------------------------------------------------


class TestHistory:
    def test_save_and_load_history(self, cli):
        """Save then load history round-trip (if readline available)."""
        import importlib
        try:
            readline = importlib.import_module("readline")
        except ImportError:
            pytest.skip("readline not available")

        # Arrange
        readline.clear_history()
        readline.add_history("cmd1")
        readline.add_history("cmd2")

        # Act
        cli._save_history()
        readline.clear_history()
        cli._load_history()

        # Assert
        actual = readline.get_current_history_length()
        assert actual == 2  # both entries restored

    def test_load_missing_history(self, cli):
        """Loading from nonexistent file doesn't raise."""
        # Act / Assert — no exception
        cli._load_history()


# -- Hook registration ------------------------------------------------------


class TestHookRegistration:
    def test_hooks_registered(self, cli):
        """All expected CLI hooks are registered."""
        # Arrange
        expected = {
            "delay", "delay.quiet", "color", "run", "run.profile",
            "demo", "demo.force", "clr", "raw", "help.open",
            "log.clear", "tui", "cli",
        }

        # Assert
        for name in expected:
            assert name in cli.repl._plugins, f"hook {name} not registered"
