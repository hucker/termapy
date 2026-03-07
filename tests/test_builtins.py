"""Tests for built-in REPL commands dispatched through ReplEngine."""

import json

import pytest

from termapy.plugins import EngineAPI, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """Create a ReplEngine with a temp config file and capture output."""
    cfg = {
        "port": "COM4",
        "baudrate": 115200,
        "echo_cmd": False,
        "line_ending": "\r",
        "os_cmd_enabled": False,
    }
    config_path = tmp_path / "test_cfg.json"
    config_path.write_text(json.dumps(cfg, indent=4))

    output = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(cfg, str(config_path), write)
    engine_api = EngineAPI(
        prefix="!!",
        plugins=engine._plugins,
        get_echo=lambda: engine._echo,
        set_echo=lambda val: setattr(engine, '_echo', val),
        get_seq_counters=lambda: engine._seq_counters,
        set_seq_counters=lambda val: setattr(engine, '_seq_counters', val),
        reset_seq=engine._reset_seq,
        in_script=lambda: engine._in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
    )
    ctx = PluginContext(
        write=write,
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
    )
    engine.set_context(ctx)
    return engine, cfg, config_path, output


# -- !!echo ----------------------------------------------------------------


class TestEcho:
    def test_echo_on(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._echo = False

        # Act
        engine.dispatch("echo on")

        # Assert
        assert engine._echo is True  # echo enabled
        assert any("on" in t for t, _ in output)  # confirmation shown

    def test_echo_off(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("echo off")

        # Assert
        assert engine._echo is False  # echo disabled
        assert any("off" in t for t, _ in output)  # confirmation shown

    def test_echo_toggle(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        assert engine._echo is True  # starts enabled

        # Act / Assert — toggle off
        engine.dispatch("echo")
        assert engine._echo is False  # toggled off

        # Act / Assert — toggle on
        engine.dispatch("echo")
        assert engine._echo is True  # toggled back on


# -- !!print ---------------------------------------------------------------


class TestPrint:
    def test_print_text(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print Hello, world!")
        assert ("Hello, world!", None) in output  # text printed verbatim

    def test_print_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print")
        assert ("", None) in output  # empty string printed


# -- !!seq -----------------------------------------------------------------


class TestSeq:
    def test_seq_show_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("seq")
        assert any("No counters" in t for t, _ in output)  # empty state message

    def test_seq_show_with_counters(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 3, 2: 7}

        # Act
        engine.dispatch("seq")

        # Assert
        assert any("seq1=3" in t for t, _ in output)  # counter 1 shown
        assert any("seq2=7" in t for t, _ in output)  # counter 2 shown

    def test_seq_reset(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 5}

        # Act
        engine.dispatch("seq reset")

        # Assert
        assert engine._seq_counters == {}  # counters cleared
        assert any("reset" in t.lower() for t, _ in output)  # confirmation shown


# -- !!stop ----------------------------------------------------------------


class TestStop:
    def test_stop_no_script(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("stop")
        assert any("No script" in t for t, _ in output)  # no-op message

    def test_stop_with_script(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._in_script = True

        # Act
        engine.dispatch("stop")

        # Assert
        assert engine._script_stop.is_set()  # stop event set
        assert any("Stopping" in t for t, _ in output)  # confirmation shown


# -- !!help ----------------------------------------------------------------


class TestHelp:
    def test_help_lists_commands(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [t for t, _ in output]
        assert any("help" in t for t in texts)  # help command listed
        assert any("cfg" in t for t in texts)  # cfg command listed

    def test_help_specific_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help echo")

        # Assert
        texts = [t for t, _ in output]
        assert any("echo" in t.lower() for t in texts)  # echo help shown

    def test_help_unknown_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help nonexistent")

        # Assert
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red


# -- !!show ----------------------------------------------------------------


class TestShow:
    def test_show_cfg(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("show $cfg")

        # Assert
        texts = [t for t, _ in output]
        assert any("COM4" in t for t in texts)  # config value shown
        assert any("end" in t for t in texts)  # end marker shown

    def test_show_file(self, repl_env, tmp_path):
        # Arrange
        engine, _, _, output = repl_env
        test_file = tmp_path / "test.txt"
        test_file.write_text("line one\nline two", encoding="utf-8")

        # Act
        engine.dispatch(f"show {test_file}")

        # Assert
        texts = [t for t, _ in output]
        assert any("line one" in t for t in texts)  # first line shown
        assert any("line two" in t for t in texts)  # second line shown

    def test_show_missing_file(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("show /nonexistent/file.txt")

        # Assert
        assert any("not found" in t.lower() for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red

    def test_show_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show")
        assert any("Usage" in t for t, _ in output)  # usage message

    def test_show_unknown_special(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show $bogus")
        assert any("Unknown special" in t for t, _ in output)  # error for bad $name


# -- !!os ------------------------------------------------------------------


class TestOs:
    def test_os_disabled(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("os echo hi")
        assert any("disabled" in t for t, _ in output)  # blocked by default

    def test_os_enabled(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os echo hello_from_os")

        # Assert
        texts = [t for t, _ in output]
        assert any("hello_from_os" in t for t in texts)  # shell output captured

    def test_os_no_args(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os")

        # Assert
        assert any("Usage" in t for t, _ in output)  # usage message


# -- dispatch edge cases ---------------------------------------------------


class TestDispatch:
    def test_unknown_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("totally_unknown_cmd")
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red

    def test_empty_dispatch_shows_help(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("")
        texts = [t for t, _ in output]
        assert any("help" in t.lower() for t in texts)  # empty triggers help

    def test_command_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("ECHO off")
        assert engine._echo is False  # uppercase command works
