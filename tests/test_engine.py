"""Tests for ReplEngine internals: start_script, run_script, _coerce_type, properties."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from termapy.plugins import PluginContext, PluginInfo
from termapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """Create a basic ReplEngine with a temp config."""
    cfg = {"port": "COM4", "baudrate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "sub" / "test.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugins", "ss", "scripts"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output = []
    return ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c))), output


# -- _coerce_type ----------------------------------------------------------


class TestCoerceType:
    def test_bool_true_values(self):
        for val in ("true", "1", "yes", "on", "True", "YES"):
            actual = ReplEngine._coerce_type(val, False)
            assert actual is True  # all truthy strings coerce to True

    def test_bool_false_values(self):
        for val in ("false", "0", "no", "off", "False", "NO"):
            actual = ReplEngine._coerce_type(val, True)
            assert actual is False  # all falsy strings coerce to False

    def test_bool_invalid(self):
        with pytest.raises(ValueError):  # non-bool string raises
            ReplEngine._coerce_type("maybe", True)

    def test_int(self):
        actual = ReplEngine._coerce_type("42", 0)
        assert actual == 42  # string coerced to int
        assert isinstance(actual, int)  # type preserved as int

    def test_int_invalid(self):
        with pytest.raises(ValueError):  # non-numeric string raises
            ReplEngine._coerce_type("abc", 0)

    def test_float(self):
        actual = ReplEngine._coerce_type("3.14", 0.0)
        assert actual == 3.14  # string coerced to float
        assert isinstance(actual, float)  # type preserved as float

    def test_string(self):
        actual = ReplEngine._coerce_type("hello", "default")
        assert actual == "hello"  # string passes through unchanged


# -- start_script ----------------------------------------------------------


class TestStartScript:
    def test_no_filename(self, engine):
        # Arrange
        eng, output = engine

        # Act
        actual = eng.start_script("")

        # Assert
        assert actual is None  # returns None on missing filename
        assert any("Usage" in t for t, _ in output)  # shows usage message

    def test_file_not_found(self, engine):
        # Arrange
        eng, output = engine

        # Act
        actual = eng.start_script("nonexistent.txt")

        # Assert
        assert actual is None  # returns None when file missing
        assert any("not found" in t.lower() for t, _ in output)  # shows error

    def test_file_found_directly(self, engine, tmp_path):
        # Arrange
        eng, output = engine
        script = tmp_path / "test_script.txt"
        script.write_text("rev\n")

        # Act
        actual = eng.start_script(str(script))

        # Assert
        assert actual == script  # returns the script path
        assert eng._in_script is True  # marks script as running

    def test_file_found_in_scripts_dir(self, engine):
        # Arrange
        eng, output = engine
        scripts_dir = eng.scripts_dir
        scripts_dir.mkdir(exist_ok=True)
        script = scripts_dir / "init.txt"
        script.write_text("rev\n")

        # Act
        actual = eng.start_script("init.txt")

        # Assert
        assert actual == script  # resolves relative to scripts dir
        assert eng._in_script is True  # marks script as running

    def test_already_running(self, engine, tmp_path):
        # Arrange
        eng, output = engine
        eng._in_script = True
        script = tmp_path / "test.txt"
        script.write_text("rev\n")

        # Act
        actual = eng.start_script(str(script))

        # Assert
        assert actual is None  # returns None when already running
        assert any("already running" in t.lower() for t, _ in output)  # shows error


# -- Properties ------------------------------------------------------------


class TestProperties:
    def test_ss_dir(self, engine):
        eng, _ = engine
        actual = eng.ss_dir
        assert actual.name == "ss"  # correct subdir name
        assert actual.exists()  # directory exists

    def test_scripts_dir(self, engine):
        eng, _ = engine
        actual = eng.scripts_dir
        assert actual.name == "scripts"  # correct subdir name

    def test_ss_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.ss_dir == Path(".")  # falls back to cwd

    def test_scripts_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.scripts_dir == Path(".")  # falls back to cwd

    def test_echo_default_true(self, engine):
        eng, _ = engine
        assert eng.echo is True  # echo enabled by default

    def test_in_script_default_false(self, engine):
        eng, _ = engine
        assert eng.in_script is False  # no script running by default


# -- register_plugin / register_hook ----------------------------------------


class TestRegisterPlugin:
    def test_register_plugin(self, engine):
        # Arrange
        eng, _ = engine
        info = PluginInfo(name="test", args="", help="Test.", handler=lambda ctx, args: None)

        # Act
        eng.register_plugin(info)

        # Assert
        assert "test" in eng._plugins  # plugin registered by name

    def test_register_plugin_overrides(self, engine):
        # Arrange
        eng, _ = engine
        h1 = lambda ctx, args: None
        h2 = lambda ctx, args: None
        eng.register_plugin(PluginInfo(name="x", args="", help="", handler=h1))

        # Act
        eng.register_plugin(PluginInfo(name="x", args="", help="", handler=h2))

        # Assert
        assert eng._plugins["x"].handler is h2  # second handler replaced first

    def test_register_hook(self, engine):
        # Arrange
        eng, _ = engine
        handler = MagicMock()

        # Act
        eng.register_hook("mytest", "<arg>", "Test hook.", handler)

        # Assert
        assert "mytest" in eng._plugins  # hook registered as plugin
        assert eng._plugins["mytest"].args == "<arg>"  # args preserved
        assert eng._plugins["mytest"].source == "built-in"  # default source


# -- _apply_cfg -------------------------------------------------------------


class TestApplyCfg:
    def test_apply_cfg_with_callback(self, engine):
        # Arrange
        eng, output = engine
        callback_calls = []
        eng._after_cfg = lambda key, val: callback_calls.append((key, val))

        # Act
        eng._apply_cfg("baudrate", 9600)

        # Assert
        assert eng.cfg["baudrate"] == 9600  # config value updated
        assert callback_calls == [("baudrate", 9600)]  # callback invoked
        assert any("saved" in t for t, _ in output)  # success message shown

    def test_apply_cfg_write_error(self, engine):
        # Arrange
        eng, output = engine
        eng.config_path = "/nonexistent/path/cfg.json"

        # Act
        eng._apply_cfg("baudrate", 9600)

        # Assert
        assert any("Error" in t for t, _ in output)  # error message shown


# -- run_script --------------------------------------------------------------


class TestRunScript:
    def _make_engine(self, tmp_path, script_text, connected=True):
        """Create an engine with mock serial context and a script file."""
        cfg = {"port": "COM4", "baudrate": 115200, "line_ending": "\r", "encoding": "utf-8"}
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugins", "ss", "scripts"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        output = []
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
        serial_writes = []
        ctx = PluginContext(
            write=lambda t, c=None: output.append((t, c)),
            cfg=cfg,
            config_path=str(config_path),
            is_connected=lambda: connected,
            serial_write=lambda data: serial_writes.append(data),
            serial_wait_idle=lambda: None,
        )
        eng.set_context(ctx)
        script = tmp_path / "test.run"
        script.write_text(script_text)
        eng._in_script = True
        return eng, output, serial_writes, script

    def test_serial_commands(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "ATZ\nAT+INFO\n")

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 2  # both commands sent
        assert writes[0] == b"ATZ\r"  # first command with line ending
        assert writes[1] == b"AT+INFO\r"  # second command with line ending
        assert any("finished" in t for t, _ in output)  # completion message
        assert eng._in_script is False  # script flag cleared

    def test_comments_and_blanks_skipped(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "# comment\n\nATZ\n")

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 1  # only the serial command sent
        assert writes[0] == b"ATZ\r"  # comment and blank skipped

    def test_repl_command(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "!print hello\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("hello" in t for t, _ in output)  # REPL command executed
        assert len(writes) == 0  # nothing sent to serial

    def test_delay_command(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "!delay 1ms\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("Delay" in t for t, _ in output)  # delay confirmation shown

    def test_invalid_delay(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "!delay badvalue\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("Invalid" in t for t, _ in output)  # error message shown

    def test_script_stop(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "ATZ\nAT+INFO\n")
        eng._script_stop.set()

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 0  # no commands sent after stop
        assert any("stopped" in t.lower() for t, _ in output)  # stop message

    def test_not_connected_skips_serial(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "ATZ\n", connected=False)

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 0  # nothing sent when disconnected
        assert eng._in_script is False  # script flag cleared

    def test_script_error(self, tmp_path):
        # Arrange
        eng, output, _, _ = self._make_engine(tmp_path, "ATZ\n")
        bad_path = tmp_path / "nonexistent.run"

        # Act
        eng.run_script(bad_path)

        # Assert
        assert any("error" in t.lower() for t, _ in output)  # error message shown
        assert eng._in_script is False  # script flag cleared on error
