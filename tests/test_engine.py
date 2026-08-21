"""Tests for ReplEngine internals: start_script, run_script, properties."""

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from termapy.plugins import CapabilitySet, PluginContext, PluginInfo, TransformInfo
from termapy.repl import ReplEngine, _parse_flags, _resolve_flag


@pytest.fixture
def engine(tmp_path):
    """Create a basic ReplEngine with a temp config."""
    cfg = {"port": "COM4", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    # Seed the engine-reserved `flags` namespace with the defaults that
    # app.py._build_context would set in production.
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex"] = False
    # Bare engines default to CONFINED caps (no host); these script tests
    # model the OPERATOR, so grant filesystem_unconfined like a real host
    # (both the ctx field and the fs handle hold the CapabilitySet).
    from termapy.plugins import CapabilitySet
    caps = CapabilitySet(filesystem_unconfined=True)
    eng.ctx.capabilities = caps
    eng.ctx.fs.capabilities = caps
    return eng, output


# -- start_script ----------------------------------------------------------


class TestStartScript:
    def test_no_filename(self, engine):
        # Arrange
        eng, output = engine

        # Act
        path, result = eng.start_script("")

        # Assert
        assert path is None, "no path on missing filename"
        assert not result.success, "failure result"
        assert "Usage" in result.error, "error message"

    def test_file_not_found(self, engine):
        # Arrange
        eng, output = engine

        # Act
        path, result = eng.start_script("nonexistent.txt")

        # Assert
        assert path is None, "no path when file missing"
        assert not result.success, "failure result"
        assert "not found" in result.error.lower(), "error message"

    def test_file_found_directly(self, engine, tmp_path):
        # Arrange
        eng, output = engine
        script = tmp_path / "test_script.txt"
        script.write_text("rev\n")

        # Act
        path, result = eng.start_script(str(script))

        # Assert
        assert path == script, "returns the script path"
        assert result.success, "success result"
        assert eng._script_depth == 1, "marks script as running"

    def test_file_found_in_scripts_dir(self, engine):
        # Arrange
        eng, output = engine
        scripts_dir = eng.scripts_dir
        scripts_dir.mkdir(exist_ok=True)
        script = scripts_dir / "init.txt"
        script.write_text("rev\n")

        # Act
        path, result = eng.start_script("init.txt")

        # Assert
        assert path == script, "resolves relative to scripts dir"
        assert result.success, "success result"
        assert eng._script_depth == 1, "marks script as running"

    def test_max_depth_exceeded(self, engine, tmp_path):
        # Arrange
        eng, output = engine
        eng._script_depth = eng._max_script_depth
        script = tmp_path / "test.txt"
        script.write_text("rev\n")

        # Act
        path, result = eng.start_script(str(script))

        # Assert
        assert path is None, "no path when max depth reached"
        assert not result.success, "failure result"
        assert "too deep" in result.error.lower(), "error message"


# -- Properties ------------------------------------------------------------


class TestProperties:
    def test_ss_dir(self, engine):
        eng, _ = engine
        actual = eng.ss_dir
        assert actual.name == "ss", "correct subdir name"
        assert actual.exists(), "directory exists"

    def test_scripts_dir(self, engine):
        eng, _ = engine
        actual = eng.scripts_dir
        assert actual.name == "run", "correct subdir name"

    def test_ss_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.ss_dir == Path("."), "falls back to cwd"

    def test_scripts_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.scripts_dir == Path("."), "falls back to cwd"

    def test_echo_default_true(self, engine):
        eng, _ = engine
        assert eng.echo is True, "echo enabled by default"

    def test_in_script_default_false(self, engine):
        eng, _ = engine
        assert eng.in_script is False, "no script running by default"


# -- register_plugin / register_hook ----------------------------------------


class TestRegisterPlugin:
    def test_register_plugin(self, engine):
        # Arrange
        eng, _ = engine
        info = PluginInfo(
            name="test", args="", help="Test.", handler=lambda ctx, args: None
        )

        # Act
        eng.register_plugin(info)

        # Assert
        assert "test" in eng._plugins, "plugin registered by name"

    def test_register_plugin_overrides(self, engine):
        # Arrange
        eng, _ = engine
        def h1(ctx, args):
            return None
        def h2(ctx, args):
            return None
        eng.register_plugin(PluginInfo(name="x", args="", help="", handler=h1))

        # Act
        eng.register_plugin(PluginInfo(name="x", args="", help="", handler=h2))

        # Assert
        assert eng._plugins["x"].handler is h2, "second handler replaced first"

    def test_register_hook(self, engine):
        # Arrange
        eng, _ = engine
        handler = MagicMock()

        # Act
        eng.register_hook("my_test", "<arg>", "Test hook.", handler)

        # Assert
        assert "my_test" in eng._plugins, "hook registered as plugin"
        assert eng._plugins["my_test"].args == "<arg>", "args preserved"
        assert eng._plugins["my_test"].source == "built-in", "default source"


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
        assert eng.cfg["baud_rate"] == 9600, "config value updated"
        assert callback_calls == [("baud_rate", 9600)], "callback invoked"
        assert any("session" in t for t, _ in output), "success message shown"


# -- run_script --------------------------------------------------------------


class TestRunScript:
    def _make_engine(self, tmp_path, script_text, connected=True):
        """Create an engine with mock serial context and a script file."""
        cfg = {
            "port": "COM4",
            "baud_rate": 115200,
            "eol": "\r",
            "encoding": "utf-8",
        }
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        output = []
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
        serial_writes = []
        from termapy.plugins import IOHandle, SerialHandle

        ctx = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: connected,
                write=lambda data: serial_writes.append(data),
                wait_idle=lambda **kw: None,
            ),
        )
        eng.set_context(ctx)
        # Seed the `flags` namespace (would be done by app.py._build_context).
        flags = ctx.ns("flags")
        flags["echo"] = True
        flags["echo_repl"] = True
        flags["output_level"] = "verbose"
        flags["hex"] = False
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
        assert len(writes) == 2, "both commands sent"
        assert writes[0] == b"ATZ\r", "first command with line ending"
        assert writes[1] == b"AT+INFO\r", "second command with line ending"
        assert any("finished" in t for t, _ in output), "completion message"
        assert eng._script_depth == 0, "script flag cleared"

    def test_comments_and_blanks_skipped(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "# comment\n\nATZ\n")

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 1, "only the serial command sent"
        assert writes[0] == b"ATZ\r", "comment and blank skipped"

    def test_repl_command(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "/print hello\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("hello" in t for t, _ in output), "REPL command executed"
        assert len(writes) == 0, "nothing sent to serial"

    def test_echo_repl_forced_off_in_script_then_restored(self, tmp_path):
        # Arrange -- interactive echo_repl on; a script's slash-commands
        # should be quiet (forced off), with the interactive value
        # restored when the outermost script ends.  _make_engine pre-sets
        # depth=1 to exercise run_script in isolation; reset to 0 so we
        # drive the REAL outermost entry via start_script.
        eng, output, writes, script = self._make_engine(tmp_path, "/print hi\n")
        flags = eng.ctx.ns("flags")
        eng._script_depth = 0
        # start_script resolves through the fs sandbox; grant operator
        # (unconfined) caps so the absolute script path is accepted.
        caps = CapabilitySet(filesystem_unconfined=True)
        eng.ctx.capabilities = caps
        eng.ctx.fs.capabilities = caps
        assert flags["echo_repl"] is True, "precondition: interactive echo_repl on"

        # Act -- outermost entry forces echo_repl off for the run...
        path, result = eng.start_script(str(script))
        forced = flags["echo_repl"]
        eng.run_script(path)  # ...and the exit restores it

        # Assert
        assert result.success, "script started"
        assert forced is False, "echo_repl forced off for the script body"
        assert flags["echo_repl"] is True, \
            "interactive echo_repl restored after the outermost script"

    def test_delay_command(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "/delay 1ms\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("Delay" in t for t, _ in output), "delay confirmation shown"

    def test_invalid_delay(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "/delay bad_value\n")

        # Act
        eng.run_script(script)

        # Assert
        assert any("Invalid" in t for t, _ in output), "error message shown"

    def test_script_stop(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(tmp_path, "ATZ\nAT+INFO\n")
        eng._script_stop.set()

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 0, "no commands sent after stop"
        assert any("stopped" in t.lower() for t, _ in output), "stop message"

    def test_not_connected_skips_serial(self, tmp_path):
        # Arrange
        eng, output, writes, script = self._make_engine(
            tmp_path, "ATZ\n", connected=False
        )

        # Act
        eng.run_script(script)

        # Assert
        assert len(writes) == 0, "nothing sent when disconnected"
        assert eng._script_depth == 0, "script flag cleared"

    def test_script_error(self, tmp_path):
        # Arrange
        eng, output, _, _ = self._make_engine(tmp_path, "ATZ\n")
        bad_path = tmp_path / "nonexistent.run"

        # Act
        eng.run_script(bad_path)

        # Assert
        assert any("error" in t.lower() for t, _ in output), "error message shown"
        assert eng._script_depth == 0, "script flag cleared on error"


# -- Transform chains -------------------------------------------------------


class TestTransformChains:
    def test_builtin_env_var_transform_loaded(self, engine):
        # Arrange
        eng, _ = engine

        # Assert
        assert eng.has_repl_transforms is True, "env_var + var REPL transforms loaded"
        assert eng.has_serial_transforms is True, "var serial transform loaded"
        names = [transform_info.name for transform_info in eng._transform_infos]
        assert "env_var" in names, "env_var transform registered"
        assert "var" in names, "var transform registered"

    def test_repl_transform_registered(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        eng.register_transform(
            TransformInfo(
                name="upper",
                help="test",
                repl=lambda s: s.upper(),
            )
        )

        # Assert
        assert eng.has_repl_transforms is True, "REPL transform registered"
        assert eng.has_serial_transforms is True, "var serial transform already loaded"

    def test_serial_transform_registered(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        eng.register_transform(
            TransformInfo(
                name="strip_atz",
                help="test",
                serial=lambda s: s.replace("ATZ", "AT"),
            )
        )

        # Assert
        assert eng.has_serial_transforms is True, "serial transform registered"
        assert eng.has_repl_transforms is True, "env_var + var REPL transforms loaded"

    def test_repl_transform_applied(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(
            TransformInfo(
                name="upper",
                help="test",
                repl=lambda s: s.upper(),
            )
        )

        # Act
        actual = eng.transform_repl("hello world")

        # Assert
        expected = "HELLO WORLD"
        assert actual == expected, "REPL transform uppercased"

    def test_serial_transform_applied(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(
            TransformInfo(
                name="replacer",
                help="test",
                serial=lambda s: s.replace("$port", "COM4"),
            )
        )

        # Act
        actual = eng.transform_serial("connect $port")

        # Assert
        expected = "connect COM4"
        assert actual == expected, "serial transform replaced $port"

    def test_chain_order_matches_registration(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(
            TransformInfo(
                name="first",
                help="test",
                repl=lambda s: s + " [A]",
            )
        )
        eng.register_transform(
            TransformInfo(
                name="second",
                help="test",
                repl=lambda s: s + " [B]",
            )
        )

        # Act
        actual = eng.transform_repl("cmd")

        # Assert
        expected = "cmd [A] [B]"
        assert actual == expected, "transforms applied in registration order"

    def test_transforms_independent(self, engine):
        # Arrange - one transform with both repl and serial functions
        eng, _ = engine
        eng.register_transform(
            TransformInfo(
                name="dual",
                help="test",
                repl=lambda s: "REPL:" + s,
                serial=lambda s: "SER:" + s,
            )
        )

        # Act
        actual_repl = eng.transform_repl("test")
        actual_serial = eng.transform_serial("test")

        # Assert - each path applies only its own function
        assert actual_repl == "REPL:test", "repl transform applied, not serial"
        assert actual_serial == "SER:test", "serial transform applied, not repl"

    def test_identity_when_no_transforms(self, engine):
        # Arrange
        eng, _ = engine

        # Act
        actual_repl = eng.transform_repl("hello")
        actual_serial = eng.transform_serial("hello")

        # Assert
        assert actual_repl == "hello", "passthrough with no transforms"
        assert actual_serial == "hello", "passthrough with no transforms"

    def test_both_chains_on_one_transform(self, engine):
        # Arrange
        eng, _ = engine
        eng.register_transform(
            TransformInfo(
                name="dual",
                help="test",
                repl=lambda s: s.upper(),
                serial=lambda s: s.lower(),
            )
        )

        # Act
        actual_repl = eng.transform_repl("Hello")
        actual_serial = eng.transform_serial("Hello")

        # Assert
        assert actual_repl == "HELLO", "REPL uppercased"
        assert actual_serial == "hello", "serial lowercased"

    def test_transform_infos_tracked(self, engine):
        # Arrange
        eng, _ = engine
        before = len(eng._transform_infos)
        eng.register_transform(
            TransformInfo(
                name="vars",
                help="Expand variables.",
                repl=lambda s: s,
            )
        )

        # Assert
        assert len(eng._transform_infos) == before + 1, "new transform added"
        assert eng._transform_infos[-1].name == "vars", "correct name tracked"


# -- dispatch_full -------------------------------------------------------------


class TestDispatchFull:
    """Tests for the full command dispatch pipeline."""

    @pytest.fixture
    def dispatch_env(self, tmp_path):
        """Create an engine with capture lists for all dispatch callbacks."""
        cfg = {
            "port": "COM4",
            "baud_rate": 115200,
            "eol": "\r",
            "encoding": "utf-8",
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

        from termapy.plugins import IOHandle, SerialHandle

        ctx = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: True,
                write=lambda data: serial_writes.append(data),
            ),
        )
        eng.set_context(ctx)
        # Seed the `flags` namespace (would be done by app.py._build_context).
        flags = ctx.ns("flags")
        flags["echo"] = True
        flags["echo_repl"] = True
        flags["output_level"] = "verbose"
        flags["hex"] = False

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

        return (
            eng,
            output,
            logged,
            echoed,
            statuses,
            serial_writes,
            raw_writes,
            do_dispatch,
        )

    def test_serial_command_sent(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("ATZ")

        # Assert
        assert writes == [b"ATZ\r"], "command encoded with line ending"
        assert len(logged) == 0, "serial commands not logged through dispatch"

    def test_empty_line_sends_line_ending(self, dispatch_env):
        # Arrange -- send_bare_enter forwards an empty line; it should send
        # just the configured line ending (not error on /term.send's empty
        # guard, which was the pre-fix behavior in non-request mode).
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("")

        # Assert
        assert writes == [b"\r"], "empty bare line sends the configured line ending"

    def test_repl_command_dispatched(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/help")

        # Assert
        assert len(writes) == 0, "not sent to serial"
        assert any(">" in d for d, _ in logged), "logged as REPL command"
        assert any("/help" in t for t in echoed), "echoed"

    def test_raw_bypass(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/raw hello world")

        # Assert
        assert raw == ["hello world"], "sent raw, no transforms"
        assert len(writes) == 0, "not through normal serial_write"

    def test_echo_off_suppresses_output(self, dispatch_env):
        # Arrange -- /help is a REPL command, so its echo is gated by
        # echo_repl (device-command echo lives on the separate `echo` flag).
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng.ctx.ns("flags")["echo_repl"] = False

        # Act
        do("/help")

        # Assert
        assert len(echoed) == 0, "no REPL echo when echo_repl disabled"

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
        assert len(writes) == 0, "nothing sent"
        assert any("Not connected" in t for t, _ in statuses), "error shown"

    def test_serial_write_error(self, dispatch_env):
        # Arrange — bare-line input goes through /term.send via dispatch_full
        # fallthrough.  dispatch_full bridges its serial_write callback to
        # ctx.serial.write for the duration of the call, so passing a bad
        # serial_write here exercises the handler's error path.  The error
        # comes back via CmdResult.fail and dispatch displays it through
        # the engine's write callback (output), not the _status callback.
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
        assert any(
            "Send error" in t for t, _ in output
        ), "error reported via engine's write callback"

    def test_echo_input_config(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng._cfg_data["echo"] = True
        eng._cfg_data["echo_fmt"] = "> {cmd}"

        # Act
        do("ATZ")

        # Assert
        assert any("> ATZ" in t for t in echoed), "input echoed"

    def test_custom_line_ending(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        eng._cfg_data["eol"] = "\r\n"

        # Act
        do("ATZ")

        # Assert
        assert writes == [b"ATZ\r\n"], "uses configured line ending"

    def test_repl_command_echo_silent_suppressed(self, dispatch_env):
        # Arrange
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env

        # Act
        do("/echo.silent off")

        # Assert - .silent commands should not be echoed even with echo on
        assert not any("echo.silent" in t for t in echoed), "suppressed"

    def test_device_echo_follows_echo_flag_not_echo_repl(self, dispatch_env):
        # Arrange - device-command echo is gated by `echo`; turning REPL
        # echo off must not silence it (the two echoes are independent).
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        flags = eng.ctx.ns("flags")
        flags["echo"] = True
        flags["echo_repl"] = False

        # Act
        do("ATZ")

        # Assert
        assert any("ATZ" in t for t in echoed), "device command echoed on echo flag"

    def test_device_echo_off_when_echo_flag_off(self, dispatch_env):
        # Arrange - echo off suppresses device echo even with echo_repl on.
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        flags = eng.ctx.ns("flags")
        flags["echo"] = False
        flags["echo_repl"] = True

        # Act
        do("ATZ")

        # Assert
        assert not any("ATZ" in t for t in echoed), "no device echo when echo off"

    def test_repl_echo_follows_echo_repl_not_echo_flag(self, dispatch_env):
        # Arrange - REPL/slash echo is gated by echo_repl; device echo off
        # must not silence it (the two echoes are independent).
        eng, output, logged, echoed, statuses, writes, raw, do = dispatch_env
        flags = eng.ctx.ns("flags")
        flags["echo"] = False
        flags["echo_repl"] = True

        # Act
        do("/help")

        # Assert
        assert any("/help" in t for t in echoed), "REPL command echoed on echo_repl flag"


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
        assert actual == "OK", "found in buffer"

    def test_timeout_returns_none(self, engine):
        """No match within timeout returns None."""
        # Arrange
        eng, _ = engine

        # Act
        actual = eng.wait_for_match(lambda line: "NOPE" in line, timeout=0.05)

        # Assert
        assert actual is None, "timed out"

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
        assert result[0] == "OK", "matched via feed_lines"

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
        assert actual == "+TEMP: 23.5C", "regex matched"


class TestFeedLines:
    def test_strips_ansi(self, engine):
        """Feed lines strips ANSI escape codes before buffering."""
        # Arrange
        eng, _ = engine

        # Act
        eng.feed_lines(["\x1b[32mOK\x1b[0m"])

        # Assert
        actual = list(eng._recent_lines)
        assert actual == ["OK"], "ANSI stripped"

    def test_buffers_without_predicate(self, engine):
        """Lines are buffered even when no predicate is active."""
        # Arrange
        eng, _ = engine
        assert eng._expect_predicate is None, "no predicate"

        # Act
        eng.feed_lines(["line1", "line2"])

        # Assert
        actual = list(eng._recent_lines)
        assert actual == ["line1", "line2"], "buffered"


class TestParseFlags:
    """Pure-function tests for the flag parser."""

    def test_no_declared_flags_passthrough(self):
        """With no declared flags, args are returned untouched."""
        # Arrange
        declared: dict[str, str] = {}

        # Act
        remaining, active, err = _parse_flags("foo --bar baz", declared)

        # Assert
        assert remaining == "foo --bar baz", "args unchanged"
        assert active == set(), "no flags recorded"
        assert err is None, "no error"

    def test_known_flag_stripped(self):
        """A declared flag is removed from args and recorded in the active set."""
        # Arrange
        declared = {"--table": "Use lookup table."}

        # Act
        remaining, active, err = _parse_flags("crc16-cms --table", declared)

        # Assert
        assert remaining == "crc16-cms", "flag stripped from args"
        assert active == {"--table"}, "flag recorded"
        assert err is None, "no error"

    def test_unknown_flag_returns_error_with_suggestion(self):
        """A typo close to a declared flag yields a 'did you mean' hint."""
        # Arrange
        declared = {"--table": "Use lookup table."}

        # Act
        _, _, err = _parse_flags("crc16-cms --tablet", declared)

        # Assert
        assert err is not None, "unknown flag should error"
        assert "--tablet" in err, "names the bad flag"
        assert "--table" in err, "suggests the close match"

    def test_alias_resolves_to_canonical(self):
        """Short aliases point at the canonical flag name."""
        # Arrange
        declared = {"--verbose": "Verbose mode.", "-v": "--verbose"}

        # Act
        remaining, active, err = _parse_flags("/run test.run -v", declared)

        # Assert
        assert "-v" not in remaining, "alias stripped"
        assert active == {"--verbose"}, "alias resolved to canonical"
        assert err is None, "no error"

    def test_double_dash_stops_parsing(self):
        """Tokens after ``--`` are treated as literal even if they look like flags."""
        # Arrange
        declared = {"--foo": "Foo."}

        # Act
        remaining, active, err = _parse_flags("a -- --foo b", declared)

        # Assert
        assert remaining == "a --foo b", "post-`--` tokens preserved"
        assert active == set(), "no flags consumed"
        assert err is None, "no error"

    def test_negative_number_not_a_flag(self):
        """Leading dash on a numeric token is not a flag lookup."""
        # Arrange
        declared = {"--foo": "Foo."}

        # Act
        remaining, active, err = _parse_flags("-5 --foo", declared)

        # Assert
        assert remaining == "-5", "negative number stays positional"
        assert active == {"--foo"}, "real flag still consumed"
        assert err is None, "no error"

    def test_resolve_flag_passthrough(self):
        """Canonical flag lookup returns the same name."""
        # Arrange
        declared = {"--table": "Use lookup table."}

        # Act
        actual = _resolve_flag("--table", declared)

        # Assert
        assert actual == "--table", "canonical resolves to itself"


class TestDispatchFlags:
    """Dispatch-level flag integration tests (parser + ctx.flag wiring)."""

    @pytest.fixture
    def flag_env(self, tmp_path):
        """Engine with a flag-declaring plugin registered; captures calls."""
        cfg = {"port": "COM4", "baud_rate": 115200}
        config_path = tmp_path / "test.cfg"
        config_path.write_text(json.dumps(cfg))

        output = []
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
        from termapy.plugins import IOHandle

        ctx = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(
                _write=lambda t, c=None: output.append((t, c)),
                _write_markup=lambda t: output.append((t, "markup")),
            ),
        )
        eng.set_context(ctx)

        # Capture what the handler saw.
        calls: list[tuple[str, set[str]]] = []

        def handler(ctx, args):
            calls.append((args, set(ctx.active_flags)))

        eng.register_plugin(
            PluginInfo(
                name="flag_test",
                args="<positional>",
                help="Test flag parsing.",
                handler=handler,
                flags={
                    "--table": "Use lookup table.",
                    "--verbose": "Verbose.",
                    "-v": "--verbose",
                },
            )
        )
        return eng, ctx, calls, output

    def test_flag_stripped_before_handler(self, flag_env):
        """Handler receives args without the flag token."""
        # Arrange
        eng, ctx, calls, _ = flag_env

        # Act
        eng.dispatch("flag_test crc16-cms --table")

        # Assert
        actual_args, actual_flags = calls[0]
        assert actual_args == "crc16-cms", "flag stripped"
        assert actual_flags == {"--table"}, "flag recorded on ctx"

    def test_ctx_flag_helper_returns_bool(self, flag_env):
        """ctx.flag() reports presence of a declared flag."""
        # Arrange
        eng, ctx, calls, _ = flag_env
        observed: list[bool] = []

        def handler(ctx, args):
            observed.append(ctx.flag("--table"))

        eng.register_plugin(
            PluginInfo(
                name="ft2",
                args="",
                help="h",
                handler=handler,
                flags={"--table": "Use table."},
            )
        )

        # Act
        eng.dispatch("ft2 --table")
        eng.dispatch("ft2")

        # Assert
        actual = observed
        expected = [True, False]
        assert actual == expected, f"{actual} == {expected}"

    def test_alias_resolves_via_ctx_flag(self, flag_env):
        """Short alias ``-v`` sets the canonical ``--verbose`` on the context."""
        # Arrange
        eng, ctx, calls, _ = flag_env

        # Act
        eng.dispatch("flag_test target -v")

        # Assert
        _, actual_flags = calls[0]
        assert actual_flags == {"--verbose"}, "alias canonicalized"

    def test_unknown_flag_is_failure_not_crash(self, flag_env):
        """Typos on declared-flag commands fail cleanly with a suggestion."""
        # Arrange
        eng, ctx, calls, output = flag_env

        # Act
        result = eng.dispatch("flag_test x --tablet")

        # Assert
        assert result.success is False, "unknown flag fails"
        assert "--tablet" in result.error, "names the bad flag"
        assert "--table" in result.error, "suggests the real flag"
        assert len(calls) == 0, "handler not called"

    def test_active_flags_reset_between_dispatches(self, flag_env):
        """ctx.active_flags is cleared after each command so state doesn't leak."""
        # Arrange
        eng, ctx, calls, _ = flag_env

        # Act
        eng.dispatch("flag_test x --table")
        # Next call on a command with no declared flags should see empty set.
        eng.register_plugin(
            PluginInfo(
                name="no_flags",
                args="",
                help="h",
                handler=lambda c, a: calls.append((a, set(c.active_flags))),
            )
        )
        eng.dispatch("no_flags")

        # Assert - the second handler invocation observed a clean slate.
        _, second_flags = calls[-1]
        assert second_flags == set(), "flags reset before next dispatch"


class TestDispatchCapabilities:
    """Dispatch-level capability gate tests."""

    @pytest.fixture
    def cap_env(self, tmp_path):
        """Engine with a configurable context capability set."""
        cfg = {"port": "COM4", "baud_rate": 115200}
        config_path = tmp_path / "test.cfg"
        config_path.write_text(json.dumps(cfg))

        output = []
        eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
        from termapy.plugins import IOHandle

        ctx = PluginContext(
            cfg=cfg,
            config_path=str(config_path),
            io=IOHandle(
                _write=lambda t, c=None: output.append((t, c)),
                _write_markup=lambda t: output.append((t, "markup")),
            ),
        )
        eng.set_context(ctx)
        return eng, ctx, output

    def test_no_needs_runs_anywhere(self, cap_env):
        """A command with the default empty needs runs in a default env."""
        # Arrange
        eng, ctx, _ = cap_env
        called = []
        eng.register_plugin(
            PluginInfo(
                name="plain",
                args="",
                help="h",
                handler=lambda c, a: called.append(True),
            )
        )

        # Act
        result = eng.dispatch("plain")

        # Assert
        assert result.success is True, "default needs met by default env"
        assert called == [True], "handler invoked"

    def test_missing_capability_fails_with_message(self, cap_env):
        """Command gated when env lacks a declared capability."""
        # Arrange
        eng, ctx, _ = cap_env
        ctx.capabilities = CapabilitySet()  # explicit baseline, no extras
        called = []
        eng.register_plugin(
            PluginInfo(
                name="blocker",
                args="",
                help="h",
                handler=lambda c, a: called.append(True),
                needs=CapabilitySet(block_until=True),
            )
        )

        # Act
        result = eng.dispatch("blocker")

        # Assert
        assert result.success is False, "capability gap fails dispatch"
        assert "block_until" in result.error, "names the missing capability"
        assert called == [], "handler never invoked"

    def test_satisfied_capability_runs(self, cap_env):
        """Command runs when env provides the declared capability."""
        # Arrange
        eng, ctx, _ = cap_env
        ctx.capabilities = CapabilitySet(block_until=True)
        called = []
        eng.register_plugin(
            PluginInfo(
                name="blocker",
                args="",
                help="h",
                handler=lambda c, a: called.append(True),
                needs=CapabilitySet(block_until=True),
            )
        )

        # Act
        result = eng.dispatch("blocker")

        # Assert
        assert result.success is True, "env satisfies needs"
        assert called == [True], "handler invoked"

    def test_restricted_baseline_gates_ordinary_command(self, cap_env):
        """Flipping a baseline capability off gates commands that didn't
        declare anything special.
        """
        # Arrange - sandbox environment without serial_io.
        eng, ctx, _ = cap_env
        ctx.capabilities = CapabilitySet(serial_io=False)
        called = []
        # Ordinary command: default CapabilitySet() has serial_io=True.
        eng.register_plugin(
            PluginInfo(
                name="sender",
                args="",
                help="h",
                handler=lambda c, a: called.append(True),
            )
        )

        # Act
        result = eng.dispatch("sender")

        # Assert - sandbox's baseline gap is detected.
        assert result.success is False, "sandbox gates the command"
        assert "serial_io" in result.error, "reports baseline gap"
        assert called == [], "handler never invoked"


class TestOneScriptAtATime:
    """T6: two outermost scripts must not run against one device.

    ``_run_script`` is a non-exclusive worker and the picker launch path
    took no lock, so a second launch simply started a second run -- two
    command streams interleaving on one port and one shared
    ``PluginContext``.  The engine now owns the rule for every frontend:
    the thread executing the run owns it, nesting is that same thread, and
    anyone else is refused.
    """

    def _engine(self, tmp_path, script_text="/print hi\n"):
        """A run-ready engine at depth 0 with operator filesystem caps."""
        # Reuse the run_script harness -- same engine shape, same script on
        # disk.  It pre-sets depth=1 so run_script can be driven in
        # isolation; these tests drive the real outermost entry instead.
        eng, output, writes, script = TestRunScript()._make_engine(
            tmp_path, script_text
        )
        eng._script_depth = 0
        caps = CapabilitySet(filesystem_unconfined=True)
        eng.ctx.capabilities = caps
        eng.ctx.fs.capabilities = caps
        return eng, output, script

    def test_a_second_launch_from_another_thread_is_refused(self, tmp_path):
        # Arrange -- a run in progress, owned by this thread
        eng, _, script = self._engine(tmp_path)
        path, first = eng.start_script(str(script))
        assert first.success and path is not None, "precondition: first run started"

        # Act -- the picker path, a button, or another frontend
        box: list = []
        other = threading.Thread(target=lambda: box.append(eng.start_script(str(script))))
        other.start()
        other.join(5)

        # Assert
        second_path, second = box[0]
        assert second_path is None, "the second launch must not get a script path"
        assert second.success is False, (
            "a second outermost script would interleave two command streams "
            "onto one device"
        )
        assert "already running" in second.error, (
            f"the refusal should say why, got {second.error!r}"
        )
        assert eng._script_depth == 1, "the refused launch must not bump the depth"

    def test_nesting_on_the_owning_thread_is_allowed(self, tmp_path):
        # Arrange
        eng, _, script = self._engine(tmp_path)
        eng.start_script(str(script))

        # Act -- what a nested /run does: same thread, inline
        nested_path, nested = eng.start_script(str(script))

        # Assert
        assert nested.success is True, "a nested /run on the owning thread is legal"
        assert nested_path is not None, "nesting resolves the script path"
        actual = eng._script_depth
        assert actual == 2, f"nesting deepens the run, got depth {actual}"

    def test_ownership_is_released_when_the_run_ends(self, tmp_path):
        # Arrange -- one complete run, start to finish
        eng, _, script = self._engine(tmp_path)
        path, _ = eng.start_script(str(script))
        eng.run_script(path)
        assert eng._script_depth == 0, "precondition: the run finished"

        # Act -- a different thread launches the next one
        box: list = []
        other = threading.Thread(target=lambda: box.append(eng.start_script(str(script))))
        other.start()
        other.join(5)

        # Assert
        _, result = box[0]
        assert result.success is True, (
            "ownership must be released at the end of the run, or the first "
            "script permanently locks out every later one"
        )

    def test_the_running_thread_owns_the_run_not_the_launching_one(self, tmp_path):
        """The TUI launches on a worker and runs on a @work thread.

        If ownership stayed with the launching thread, every nested ``/run``
        would be refused as a second script.
        """
        # Arrange
        eng, _, script = self._engine(tmp_path, "/print outer\n")
        path, _ = eng.start_script(str(script))  # launched on THIS thread

        # Act -- the run happens somewhere else, then nests from there
        nested: list = []

        def run_elsewhere() -> None:
            eng.run_script(path)
            # depth is back to 0 here; nesting mid-run is covered above.
            nested.append(eng.start_script(str(script)))

        worker = threading.Thread(target=run_elsewhere)
        worker.start()
        worker.join(5)

        # Assert
        _, result = nested[0]
        assert result.success is True, (
            "the thread that executes the script owns it; a launch from that "
            "thread must not be mistaken for a competing frontend"
        )


def _wire_script_slots(eng) -> None:
    """Wire the ctx.internal script slots a real host provides.

    ``TestRunScript._make_engine`` builds a PluginContext with the io and
    serial handles only, so ``ctx.internal`` keeps its inert defaults --
    ``in_script`` always False and ``script_stop`` a no-op.  ``/stop`` and
    ``/repeat`` both read those, so without this they cannot interact at
    all.  These three lines are exactly what
    ``TerminalHost._build_internal_handle`` sets in production.
    """
    internal = eng.ctx.internal
    internal.in_script = lambda: eng.in_script
    internal.script_stop = lambda: eng._script_stop.set()
    internal.script_stop_event = eng._script_stop
    # /repeat re-dispatches its cmd= through ctx.dispatch, which is an inert
    # lambda on a bare engine; route it back into the engine the way
    # dispatch_full does for a slash command.
    eng.ctx.dispatch = lambda line: eng.dispatch(
        line[len(eng.prefix):] if line.startswith(eng.prefix) else line
    )


class TestRepeatDoesNotEraseAScriptStop:
    """T3: ``/repeat`` cleared the engine's shared stop event.

    A stop requested mid-repeat was acknowledged in the UI, erased by
    ``/repeat`` on its way out, and the enclosing script ran to completion.
    """

    def test_a_stop_inside_repeat_aborts_the_script(self, tmp_path):
        # Arrange -- /stop fires on the first iteration; the line after the
        # repeat must never run.
        eng, output, writes, script = TestRunScript()._make_engine(
            tmp_path, "/repeat count=5 cmd=/stop\n/print SHOULD-NOT-RUN\n"
        )
        _wire_script_slots(eng)

        # Act
        eng.run_script(script)

        # Assert
        text = " ".join(t for t, _ in output)
        assert "SHOULD-NOT-RUN" not in text, (
            "/repeat must leave the script's stop flag set so the enclosing "
            f"run aborts. Output was: {text!r}"
        )
        assert eng._script_stop.is_set(), (
            "the stop belongs to the script; /repeat must not consume it"
        )

    def test_interactive_repeat_still_clears_a_stale_stop(self, tmp_path):
        """Outside a script /repeat IS the outermost cancellable operation.

        A stale ``set()`` from an earlier Escape would otherwise abort the
        next ``/repeat`` before its first iteration.
        """
        # Arrange -- no script running, stop flag left set by an earlier run
        eng, output, writes, _ = TestRunScript()._make_engine(tmp_path, "")
        _wire_script_slots(eng)
        eng._script_depth = 0
        eng._script_stop.set()

        # Act
        result = eng.dispatch("repeat count=2 cmd=/print tick")

        # Assert
        assert result.success is True, "an interactive repeat runs"
        actual = result.value
        assert actual == "2", f"both iterations should run, got {actual!r}"


# -- Universal --json flag --------------------------------------------------


class TestJsonFlag:
    """``--json`` on any command renders the result as a JSON envelope."""

    def _last_envelope(self, output):
        """Parse the last written line as the JSON envelope."""
        assert output, "an envelope line was written"
        text, _color = output[-1]
        return json.loads(text)

    def test_converted_command_envelope(self, engine):
        # Arrange
        eng, output = engine
        eng.dispatch("var.set PORT COM9")
        output.clear()

        # Act
        result = eng.dispatch("var --json")

        # Assert
        envelope = self._last_envelope(output)
        expected_keys = {
            "cmd", "success", "error", "value", "data", "output_lines",
            "elapsed_s",
        }
        assert set(envelope.keys()) == expected_keys, (
            "terminal envelope is the fixed seven-key core"
        )
        assert envelope["success"] is True, "dispatch succeeded"
        assert envelope["data"]["user"] == {"PORT": "COM9"}, (
            "structured namespaces in data"
        )
        assert envelope["output_lines"] == [], (
            "converted command: data only, no captured prose"
        )
        assert result.data is not None, "CmdResult carries data too"

    def test_unconverted_command_answer_is_captured(self, engine):
        # Arrange -- /term.info has no data producer; in JSON mode its
        # whole answer must arrive in the envelope, not print above it.
        # (/help would be the natural pick but its landscape reads
        # ctx.internal.plugins, which only a HOST wires -- empty in this
        # bare engine.)
        eng, output = engine
        output.clear()

        # Act
        eng.dispatch("term.info --json")

        # Assert
        assert len(output) == 1, (
            "one line total: the envelope IS the answer, no prose printed"
        )
        envelope = self._last_envelope(output)
        assert envelope["data"] is None, "no structured form for /term.info"
        assert len(envelope["output_lines"]) > 5, (
            "the kv listing captured into the envelope"
        )
        assert any(
            "echo" in line and "[" not in line
            for line in envelope["output_lines"]
        ), "captured lines are the real content with markup flattened"

    def test_unknown_command_is_enveloped(self, engine):
        # Arrange
        eng, output = engine

        # Act
        result = eng.dispatch("nope_not_a_command --json")

        # Assert
        envelope = self._last_envelope(output)
        assert envelope["success"] is False, "failure carried in the field"
        assert "Unknown command" in envelope["error"], "error in the field"
        assert envelope["data"] is None, "no data for a failed lookup"
        assert not any(
            color == "red" for _text, color in output
        ), "no separate red error line: error is a field, not a second render"
        assert result.success is False, "CmdResult still reports failure"

    def test_flag_stripped_from_args(self, engine):
        # Arrange -- /print echoes its args; the flag must not be in them
        eng, output = engine

        # Act
        eng.dispatch("print hello --json")

        # Assert
        envelope = self._last_envelope(output)
        assert envelope["value"] == "hello", "--json removed before the handler"
        assert "--json" not in envelope["cmd"], "cmd shows the effective call"

    def test_wants_data_restored_after_dispatch(self, engine):
        # Arrange
        eng, output = engine

        # Act
        eng.dispatch("var --json")

        # Assert
        assert eng.ctx.wants_data is False, (
            "per-call flag restored; the session default is prose"
        )

    def test_silent_suppresses_the_envelope(self, engine):
        # Arrange
        eng, output = engine
        output.clear()

        # Act -- silent gates the result channel the envelope rides on
        result = eng.dispatch("var --json --silent")

        # Assert
        assert output == [], "--silent wins: nothing rendered"
        assert result.data is not None, "the data still exists on the result"

    def test_quiet_still_emits_the_envelope(self, engine):
        # Arrange -- result channel shows at quiet+
        eng, output = engine
        output.clear()

        # Act
        eng.dispatch("var --json --quiet")

        # Assert
        envelope = self._last_envelope(output)
        assert envelope["success"] is True, "envelope rides the result channel"


class TestRequestModeSessionJson:
    """``request_mode`` is the session dial: termapy commands envelope too."""

    @pytest.fixture(autouse=True)
    def _front_end_not_mcp(self, monkeypatch):
        """Isolate FRONT_END: a sibling MCP test file on this worker leaves
        the module-global at "mcp", and the render gate would then skip the
        envelope these tests assert on."""
        from termapy import variables as _variables
        monkeypatch.setattr(
            _variables, "_LAUNCH_VARS", dict(_variables._LAUNCH_VARS)
        )
        _variables.set_launch_var("FRONT_END", "test")

    def test_termapy_command_envelopes_without_flag(self, engine):
        # Arrange
        eng, output = engine
        eng._apply_cfg("request_mode", True)
        output.clear()

        # Act -- no --json flag; the session mode alone does it
        result = eng.dispatch("var")

        # Assert
        text, _color = output[-1]
        envelope = json.loads(text)
        assert envelope["success"] is True, "termapy command enveloped"
        assert "data" in envelope, "structured data field present"
        assert result.data is not None, "converted command produced data"

    def test_off_returns_to_prose(self, engine):
        # Arrange
        eng, output = engine
        eng._apply_cfg("request_mode", True)
        eng._apply_cfg("request_mode", False)
        output.clear()

        # Act
        eng.dispatch("print hello")

        # Assert -- plain prose, not an envelope
        text, _color = output[-1]
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)

    def test_mcp_frontend_skips_the_render(self, engine, monkeypatch):
        # Arrange -- MCP delivers data on its outer response envelope;
        # rendering here too would duplicate it into output_lines.
        from termapy import variables as _variables
        eng, output = engine
        monkeypatch.setattr(
            _variables, "_LAUNCH_VARS", dict(_variables._LAUNCH_VARS)
        )
        _variables.set_launch_var("FRONT_END", "mcp")
        eng._apply_cfg("request_mode", True)
        output.clear()

        # Act
        result = eng.dispatch("var")

        # Assert -- data still produced (wants_data is MCP's session
        # default), but no envelope line printed by the dispatcher
        assert all(
            not text.lstrip().startswith("{") for text, _color in output
        ), "no dispatcher-rendered envelope under MCP"
        assert result.value, "value still flows to the MCP response"
