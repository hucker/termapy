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
        engine, _, _, output = repl_env
        engine._echo = False
        engine.dispatch("echo on")
        assert engine._echo is True
        assert any("on" in t for t, _ in output)

    def test_echo_off(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("echo off")
        assert engine._echo is False
        assert any("off" in t for t, _ in output)

    def test_echo_toggle(self, repl_env):
        engine, _, _, output = repl_env
        assert engine._echo is True
        engine.dispatch("echo")
        assert engine._echo is False
        engine.dispatch("echo")
        assert engine._echo is True


# -- !!print ---------------------------------------------------------------


class TestPrint:
    def test_print_text(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print Hello, world!")
        assert ("Hello, world!", None) in output

    def test_print_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print")
        assert ("", None) in output


# -- !!seq -----------------------------------------------------------------


class TestSeq:
    def test_seq_show_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("seq")
        assert any("No counters" in t for t, _ in output)

    def test_seq_show_with_counters(self, repl_env):
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 3, 2: 7}
        engine.dispatch("seq")
        assert any("seq1=3" in t for t, _ in output)
        assert any("seq2=7" in t for t, _ in output)

    def test_seq_reset(self, repl_env):
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 5}
        engine.dispatch("seq reset")
        assert engine._seq_counters == {}
        assert any("reset" in t.lower() for t, _ in output)


# -- !!stop ----------------------------------------------------------------


class TestStop:
    def test_stop_no_script(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("stop")
        assert any("No script" in t for t, _ in output)

    def test_stop_with_script(self, repl_env):
        engine, _, _, output = repl_env
        engine._in_script = True
        engine.dispatch("stop")
        assert engine._script_stop.is_set()
        assert any("Stopping" in t for t, _ in output)


# -- !!help ----------------------------------------------------------------


class TestHelp:
    def test_help_lists_commands(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("help")
        texts = [t for t, _ in output]
        # Should list at least some built-in commands
        assert any("help" in t for t in texts)
        assert any("cfg" in t for t in texts)

    def test_help_specific_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("help echo")
        texts = [t for t, _ in output]
        assert any("echo" in t.lower() for t in texts)

    def test_help_unknown_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("help nonexistent")
        assert any("Unknown" in t for t, _ in output)
        assert output[-1][1] == "red"


# -- !!show ----------------------------------------------------------------


class TestShow:
    def test_show_cfg(self, repl_env):
        engine, cfg, config_path, output = repl_env
        engine.dispatch("show $cfg")
        texts = [t for t, _ in output]
        assert any("COM4" in t for t in texts)
        assert any("end" in t for t in texts)

    def test_show_file(self, repl_env, tmp_path):
        engine, _, _, output = repl_env
        test_file = tmp_path / "test.txt"
        test_file.write_text("line one\nline two", encoding="utf-8")
        engine.dispatch(f"show {test_file}")
        texts = [t for t, _ in output]
        assert any("line one" in t for t in texts)
        assert any("line two" in t for t in texts)

    def test_show_missing_file(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show /nonexistent/file.txt")
        assert any("not found" in t.lower() for t, _ in output)
        assert output[-1][1] == "red"

    def test_show_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show")
        assert any("Usage" in t for t, _ in output)

    def test_show_unknown_special(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show $bogus")
        assert any("Unknown special" in t for t, _ in output)


# -- !!os ------------------------------------------------------------------


class TestOs:
    def test_os_disabled(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("os echo hi")
        assert any("disabled" in t for t, _ in output)

    def test_os_enabled(self, repl_env):
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True
        engine.dispatch("os echo hello_from_os")
        texts = [t for t, _ in output]
        assert any("hello_from_os" in t for t in texts)

    def test_os_no_args(self, repl_env):
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True
        engine.dispatch("os")
        assert any("Usage" in t for t, _ in output)


# -- dispatch edge cases ---------------------------------------------------


class TestDispatch:
    def test_unknown_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("totally_unknown_cmd")
        assert any("Unknown" in t for t, _ in output)
        assert output[-1][1] == "red"

    def test_empty_dispatch_shows_help(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("")
        # Empty dispatch calls help handler
        texts = [t for t, _ in output]
        assert any("help" in t.lower() for t in texts)

    def test_command_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("ECHO off")
        assert engine._echo is False
