"""Tests for ReplEngine internals: start_script, run_script, _coerce_type, properties."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from termapy.plugins import PluginContext, PluginInfo, TransformInfo
from termapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """Create a basic ReplEngine with a temp config."""
    cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
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
        assert eng._script_depth == 1  # marks script as running

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
        assert eng._script_depth == 1  # marks script as running

    def test_max_depth_exceeded(self, engine, tmp_path):
        # Arrange
        eng, output = engine
        eng._script_depth = eng._max_script_depth
        script = tmp_path / "test.txt"
        script.write_text("rev\n")

        # Act
        actual = eng.start_script(str(script))

        # Assert
        assert actual is None  # returns None when max depth reached
        assert any("too deep" in t.lower() for t, _ in output)  # shows error


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
        assert actual.name == "run"  # correct subdir name

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
        eng._apply_cfg("baud_rate", 9600)

        # Assert
        assert eng.cfg["baud_rate"] == 9600  # config value updated
        assert callback_calls == [("baud_rate", 9600)]  # callback invoked
        assert any("session" in t for t, _ in output)  # success message shown


# -- run_script --------------------------------------------------------------


class TestRunScript:
    def _make_engine(self, tmp_path, script_text, connected=True):
        """Create an engine with mock serial context and a script file."""
        cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r", "encoding": "utf-8"}
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
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
        eng._script_depth = 1
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
        assert eng._script_depth == 0  # script flag cleared

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
        eng, output, writes, script = self._make_engine(tmp_path, "/print hello\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("hello" in t for t, _ in output)  # REPL command executed
        assert len(writes) == 0  # nothing sent to serial

    def test_delay_command(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "/delay 1ms\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("Delay" in t for t, _ in output)  # delay confirmation shown

    def test_invalid_delay(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "/delay badvalue\n")

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
        assert eng._script_depth == 0  # script flag cleared

    def test_script_error(self, tmp_path):
        # Arrange
        eng, output, _, _ = self._make_engine(tmp_path, "ATZ\n")
        bad_path = tmp_path / "nonexistent.run"

        # Act
        eng.run_script(bad_path)

        # Assert
        assert any("error" in t.lower() for t, _ in output)  # error message shown
        assert eng._script_depth == 0  # script flag cleared on error


# -- Transform chains -------------------------------------------------------


class TestTransformChains:
    def test_builtin_env_var_transform_loaded(self, engine):
        # Arrange
        eng, _ = engine

        # Assert
        assert eng.has_repl_transforms is True  # env_var + var REPL transforms loaded
        assert eng.has_serial_transforms is True  # var serial transform loaded
        names = [t.name for t in eng._transform_infos]
        assert "env_var" in names  # env_var transform registered
        assert "var" in names  # var transform registered

    def test_repl_transform_registered(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        eng.register_transform(TransformInfo(
            name="upper", help="test", repl=lambda s: s.upper(),
        ))

        # Assert
        assert eng.has_repl_transforms is True  # REPL transform registered
        assert eng.has_serial_transforms is True  # var serial transform already loaded

    def test_serial_transform_registered(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        eng.register_transform(TransformInfo(
            name="strip_atz", help="test",
            serial=lambda s: s.replace("ATZ", "AT"),
        ))

        # Assert
        assert eng.has_serial_transforms is True  # serial transform registered
        assert eng.has_repl_transforms is True  # env_var + var REPL transforms loaded

    def test_repl_transform_applied(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(TransformInfo(
            name="upper", help="test", repl=lambda s: s.upper(),
        ))

        # Act
        actual = eng.transform_repl("hello world")

        # Assert
        expected = "HELLO WORLD"
        assert actual == expected  # REPL transform uppercased

    def test_serial_transform_applied(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(TransformInfo(
            name="replacer", help="test",
            serial=lambda s: s.replace("$port", "COM4"),
        ))

        # Act
        actual = eng.transform_serial("connect $port")

        # Assert
        expected = "connect COM4"
        assert actual == expected  # serial transform replaced $port

    def test_chain_order_matches_registration(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(TransformInfo(
            name="first", help="test", repl=lambda s: s + " [A]",
        ))
        eng.register_transform(TransformInfo(
            name="second", help="test", repl=lambda s: s + " [B]",
        ))

        # Act
        actual = eng.transform_repl("cmd")

        # Assert
        expected = "cmd [A] [B]"
        assert actual == expected  # transforms applied in registration order

    def test_transforms_independent(self, engine):
        # Arrange — one transform with both repl and serial functions
        eng, _ = engine
        eng.register_transform(TransformInfo(
            name="dual", help="test",
            repl=lambda s: "REPL:" + s,
            serial=lambda s: "SER:" + s,
        ))

        # Act
        actual_repl = eng.transform_repl("test")
        actual_serial = eng.transform_serial("test")

        # Assert — each path applies only its own function
        assert actual_repl == "REPL:test"  # repl transform applied, not serial
        assert actual_serial == "SER:test"  # serial transform applied, not repl

    def test_identity_when_no_transforms(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        actual_repl = eng.transform_repl("hello")
        actual_serial = eng.transform_serial("hello")

        # Assert
        assert actual_repl == "hello"  # passthrough with no transforms
        assert actual_serial == "hello"  # passthrough with no transforms

    def test_both_chains_on_one_transform(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(TransformInfo(
            name="dual", help="test",
            repl=lambda s: s.upper(),
            serial=lambda s: s.lower(),
        ))

        # Act
        actual_repl = eng.transform_repl("Hello")
        actual_serial = eng.transform_serial("Hello")

        # Assert
        assert actual_repl == "HELLO"  # REPL uppercased
        assert actual_serial == "hello"  # serial lowercased

    def test_transform_infos_tracked(self, engine):
        # Arrange
        eng, _ = engine
        before = len(eng._transform_infos)
        eng.register_transform(TransformInfo(
            name="vars", help="Expand variables.", repl=lambda s: s,
        ))

        # Assert
        assert len(eng._transform_infos) == before + 1  # new transform added
        assert eng._transform_infos[-1].name == "vars"  # correct name tracked


# -- dispatch_full -------------------------------------------------------------


class TestDispatchFull:
    """Tests for the full command dispatch pipeline."""

    @pytest.fixture
    def dispatch_env(self, tmp_path):
        """Create an engine with capture lists for all dispatch callbacks."""
        cfg = {
            "port": "COM4", "baud_rate": 115200,
            "line_ending": "\r", "encoding": "utf-8",
        }
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)

        output = []
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))

        # Capture lists for callbacks
        logged = []
        echoed = []
        statuses = []
        serial_writes = []
        raw_writes = []

        ctx = PluginContext(
            write=lambda t, c=None: output.append((t, c)),
            cfg=cfg,
            config_path=str(config_path),
            is_connected=lambda: True,
            serial_write=lambda data: serial_writes.append(data),
        )
        eng.set_context(ctx)

        def do_dispatch(cmd, connected=True):
            eng.dispatch_full(
                cmd,
                log=lambda d, t: logged.append((d, t)),
                echo_markup=echoed.append,
                status=lambda t, c: statuses.append((t, c)),
                serial_write=serial_writes.append,
                serial_write_raw=raw_writes.append,
                is_connected=lambda: connected,
            )

        return eng, output, logged, echoed, statuses, serial_writes, raw_writes, do_dispatch

    def test_serial_command_sent(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("ATZ")

        # Assert
        assert writes == [b"ATZ\r"]  # command encoded with line ending
        assert len(logged) == 0  # serial commands not logged through dispatch

    def test_repl_command_dispatched(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/help")

        # Assert
        assert len(writes) == 0  # not sent to serial
        assert any(">" in d for d, _ in logged)  # logged as REPL command
        assert any("/help" in t for t in echoed)  # echoed

    def test_raw_bypass(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/raw hello world")

        # Assert
        assert raw == ["hello world"]  # sent raw, no transforms
        assert len(writes) == 0  # not through normal serial_write

    def test_echo_off_suppresses_output(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng._echo = False

        # Act
        do("/help")

        # Assert
        assert len(echoed) == 0  # no echo when disabled

    def test_not_connected_blocks_send(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        eng.dispatch_full(
            "ATZ",
            status=lambda t, c: statuses.append((t, c)),
            serial_write=writes.append,
            is_connected=lambda: False,
        )

        # Assert
        assert len(writes) == 0  # nothing sent
        assert any("Not connected" in t for t, _ in statuses)  # error shown

    def test_serial_write_error(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        def bad_write(data):
            raise OSError("port closed")

        # Act
        eng.dispatch_full(
            "ATZ",
            status=lambda t, c: statuses.append((t, c)),
            serial_write=bad_write,
            is_connected=lambda: True,
        )

        # Assert
        assert any("Send error" in t for t, _ in statuses)  # error reported

    def test_echo_input_config(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng._cfg_data["echo_input"] = True
        eng._cfg_data["echo_input_fmt"] = "> {cmd}"

        # Act
        do("ATZ")

        # Assert
        assert any("> ATZ" in t for t in echoed)  # input echoed

    def test_custom_line_ending(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng._cfg_data["line_ending"] = "\r\n"

        # Act
        do("ATZ")

        # Assert
        assert writes == [b"ATZ\r\n"]  # uses configured line ending

    def test_repl_command_echo_quiet_suppressed(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/echo.quiet off")

        # Assert — echo.quiet commands should not be echoed even with echo on
        assert not any("echo.quiet" in t for t in echoed)  # suppressed


# ── wait_for_match / feed_lines ──────────────────────────────────


class TestWaitForMatch:
    def test_immediate_from_buffer(self, engine):
        """Match found in recent_lines buffer returns immediately."""
        # Arrange
        eng, _ = engine
        eng._recent_lines.append("OK")

        # Act
        actual = eng.wait_for_match(lambda line: "OK" in line, timeout=0.05)

        # Assert
        assert actual == "OK"  # found in buffer

    def test_timeout_returns_none(self, engine):
        """No match within timeout returns None."""
        # Arrange
        eng, _ = engine

        # Act
        actual = eng.wait_for_match(lambda line: "NOPE" in line, timeout=0.05)

        # Assert
        assert actual is None  # timed out

    def test_match_via_feed_lines(self, engine):
        """Match found via feed_lines from another thread."""
        import threading

        # Arrange
        eng, _ = engine
        result = [None]

        def wait():
            result[0] = eng.wait_for_match(lambda line: "OK" in line, timeout=2.0)

        # Act
        t = threading.Thread(target=wait)
        t.start()
        import time
        time.sleep(0.05)  # let wait_for_match install predicate
        eng.feed_lines(["OK"])
        t.join(timeout=2.0)

        # Assert
        assert result[0] == "OK"  # matched via feed_lines

    def test_regex_predicate(self, engine):
        """Regex predicate matches correctly."""
        import re

        # Arrange
        eng, _ = engine
        eng._recent_lines.append("+TEMP: 23.5C")

        # Act
        actual = eng.wait_for_match(
            lambda line: bool(re.search(r"\d+\.\d+C", line)), timeout=0.05
        )

        # Assert
        assert actual == "+TEMP: 23.5C"  # regex matched


class TestFeedLines:
    def test_strips_ansi(self, engine):
        """Feed lines strips ANSI escape codes before buffering."""
        # Arrange
        eng, _ = engine

        # Act
        eng.feed_lines(["\x1b[32mOK\x1b[0m"])

        # Assert
        actual = list(eng._recent_lines)
        assert actual == ["OK"]  # ANSI stripped

    def test_buffers_without_predicate(self, engine):
        """Lines are buffered even when no predicate is active."""
        # Arrange
        eng, _ = engine
        assert eng._expect_predicate is None  # no predicate

        # Act
        eng.feed_lines(["line1", "line2"])

        # Assert
        actual = list(eng._recent_lines)
        assert actual == ["line1", "line2"]  # buffered
