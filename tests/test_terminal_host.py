"""Unit tests for TerminalHost - context builders, serial I/O, hooks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from termapy.capture import CaptureEngine
from termapy.plugins import CmdResult, InternalHandle, PluginContext
from termapy.repl import ReplEngine
from termapy.serial_engine import SerialEngine
from termapy.terminal_host import TerminalHost


# -- Concrete stub for testing -----------------------------------------------


class _StubHost(TerminalHost):
    """Minimal concrete TerminalHost for testing base class methods."""

    def __init__(self, cfg: dict, config_path: str, engine, repl, capture):
        self.cfg = cfg
        self.config_path = config_path
        self.engine = engine
        self.repl = repl
        self.capture = capture
        self._output: list[str] = []
        self._markup: list[str] = []
        self._status_msgs: list[str] = []
        self._log_msgs: list[tuple[str, str]] = []

    def write(self, text: str, color: str = "") -> None:
        self._output.append(text)

    def write_markup(self, text: str) -> None:
        self._markup.append(text)

    def status(self, text: str, color: str = "") -> None:
        self._status_msgs.append(text)

    def _log(self, direction: str, text: str) -> None:
        self._log_msgs.append((direction, text))

    def _start_reader(self) -> None:
        pass

    def _confirm(self, message: str) -> bool:
        return True


# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def host(tmp_path):
    """Create a _StubHost with mocked engines."""
    cfg = {"port": "COM99", "baud_rate": 115200, "line_ending": "\r",
           "encoding": "utf-8", "cmd_prefix": "/"}
    config_path = tmp_path / "test_cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap", "prof"):
        (config_path.parent / sub).mkdir(exist_ok=True)

    mock_engine = MagicMock(spec=SerialEngine)
    mock_engine.is_connected = False
    mock_engine.serial_port = None
    mock_engine.port_obj = None
    mock_engine.last_error = ""

    mock_capture = MagicMock(spec=CaptureEngine)
    mock_capture.active = False

    repl = ReplEngine(cfg, str(config_path), write=lambda t, c="": None, prefix="/")

    h = _StubHost(cfg, str(config_path), mock_engine, repl, mock_capture)
    return h


# -- _build_internal_handle -------------------------------------------------------


class TestBuildSerialHandle:
    """Serial lifecycle verbs delegate to the host (moved off ctx.internal)."""

    def test_connect_callback_wired(self, host):
        # Arrange
        host._connect = MagicMock()

        # Act
        serial = host._build_plugin_context(host._build_internal_handle()).serial
        serial.connect("COM1")

        # Assert
        # connect delegates to host (the mock method raises on mismatch)
        host._connect.assert_called_once_with("COM1")

    def test_disconnect_callback_wired(self, host):
        # Arrange
        host._disconnect = MagicMock()

        # Act
        serial = host._build_plugin_context(host._build_internal_handle()).serial
        serial.disconnect()

        # Assert
        # disconnect delegates to host
        host._disconnect.assert_called_once()

    def test_apply_port_effects_wired(self, host):
        # Arrange
        host._apply_port_effects = MagicMock()

        # Act
        serial = host._build_plugin_context(host._build_internal_handle()).serial
        serial.apply_port_effects({"cfg_update": {"port": "COM2"}})

        # Assert
        # apply_port_effects delegates to host
        host._apply_port_effects.assert_called_once()


class TestBuildInternalHandle:
    def test_returns_internal_handle(self, host):
        # Act
        api = host._build_internal_handle()

        # Assert
        assert isinstance(api, InternalHandle), "returns InternalHandle instance"

    def test_start_capture_wired(self, host):
        # Arrange
        host._start_capture = MagicMock(return_value=True)

        # Act
        api = host._build_internal_handle()
        api.start_capture(mode="text", path="/tmp/x")

        # Assert
        # start_capture delegates to host
        host._start_capture.assert_called_once_with(mode="text", path="/tmp/x")

    def test_script_stop_event_wired(self, host):
        # Act
        api = host._build_internal_handle()

        # Assert
        assert api.script_stop_event is host.repl._script_stop, \
            "script_stop_event is repl's stop event"


# -- _build_plugin_context ---------------------------------------------------


class TestBuildPluginContext:
    def test_returns_plugin_context(self, host):
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert isinstance(ctx, PluginContext), "returns PluginContext instance"

    def test_cfg_wired(self, host):
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert ctx.cfg is host.cfg, "cfg is the host's cfg dict"

    def test_config_path_wired(self, host):
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert ctx.config_path == host.config_path, "config_path matches host"

    def test_serial_write_wired(self, host):
        # Arrange
        api = host._build_internal_handle()
        ctx = host._build_plugin_context(api)
        host.engine.serial_port = MagicMock()

        # Act
        ctx.serial.write(b"\x01\x02")

        # Assert
        # serial.write delegates through host
        host.engine.serial_port.write.assert_called_once_with(b"\x01\x02")

    def test_directories_wired(self, host):
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert ctx.fs.ss_dir == host.repl.ss_dir, "ss_dir matches repl"
        assert ctx.fs.scripts_dir == host.repl.scripts_dir, "scripts_dir matches repl"
        assert ctx.fs.proto_dir == host.repl.proto_dir, "proto_dir matches repl"
        assert ctx.fs.cap_dir == host.repl.cap_dir, "cap_dir matches repl"
        assert ctx.fs.prof_dir == host.repl.prof_dir, "prof_dir matches repl"

    def test_is_connected_wired(self, host):
        # Arrange
        api = host._build_internal_handle()
        ctx = host._build_plugin_context(api)
        host.engine.is_connected = True

        # Act
        actual = ctx.serial.is_connected()

        # Assert
        assert actual is True, "is_connected reflects engine state"

    def test_confirm_wired(self, host):
        # Arrange -- TUI capability is needed for ctx.ui.confirm
        api = host._build_internal_handle()
        ctx = host._build_plugin_context(api)
        from termapy.plugins import CapabilitySet
        ctx.capabilities = CapabilitySet(confirm_dialog=True)
        ctx.ui.capabilities = ctx.capabilities

        # Act
        actual = ctx.ui.confirm("proceed?")

        # Assert
        assert actual is True, "confirm delegates to host._confirm (returns True in stub)"


# -- _init_flags -------------------------------------------------------------


class TestInitFlags:
    def test_echo_true(self, host):
        # Arrange
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(echo=True)

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["echo"] is True, "echo set to True"
        assert flags["output_level"] == "normal", "output_level defaults to normal"

    def test_echo_false(self, host):
        # Arrange
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(echo=False)

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["echo"] is False, "echo set to False for CLI"

    def test_hex_mode_from_cfg(self, host):
        # Arrange
        host.cfg["hex_mode"] = True
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags()

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["hex_mode"] is True, "hex_mode read from cfg"


# -- _serial_write / _serial_send -------------------------------------------


class TestSerialIO:
    def test_serial_write_no_port(self, host):
        # Arrange
        host.engine.serial_port = None

        # Act - should not raise
        host._serial_write(b"hello")

        # Assert
        # no notify when no port
        host.engine.notify_tx.assert_not_called()

    def test_serial_write_with_port(self, host):
        # Arrange
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_write(b"\x01\x02")

        # Assert
        # bytes written to port
        host.engine.serial_port.write.assert_called_once_with(b"\x01\x02")
        # TX observers notified
        host.engine.notify_tx.assert_called_once_with(b"\x01\x02")

    def test_serial_send_appends_line_ending(self, host):
        # Arrange
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_send("AT")

        # Assert
        expected = b"AT\r"
        # text + line_ending encoded and written
        host.engine.serial_port.write.assert_called_once_with(expected)

    def test_serial_send_custom_encoding(self, host):
        # Arrange
        host.cfg["encoding"] = "ascii"
        host.cfg["line_ending"] = "\n"
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_send("OK")

        # Assert
        expected = b"OK\n"
        # custom encoding and line ending used
        host.engine.serial_port.write.assert_called_once_with(expected)


# -- _serial_write_raw -------------------------------------------------------


class TestSerialWriteRaw:
    def test_not_connected(self, host):
        # Arrange
        host.engine.is_connected = False

        # Act
        host._serial_write_raw("AT")

        # Assert
        assert "Not connected." in host._status_msgs, "shows not connected message"

    def test_sends_with_line_ending(self, host):
        # Arrange
        host.engine.is_connected = True
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_write_raw("AT")

        # Assert
        expected = b"AT\r"
        # raw text + line ending written
        host.engine.serial_port.write.assert_called_once_with(expected)
        # TX observers notified
        host.engine.notify_tx.assert_called_once_with(expected)


# -- _dispatch ---------------------------------------------------------------


class TestDispatch:
    def test_dispatch_delegates_to_repl(self, host):
        # Arrange
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)
        host.repl.set_context(host.ctx)
        host._init_flags(echo=False)
        host.repl.register_hook(
            "ping", "", "test", lambda ctx, args: CmdResult.ok(value="pong"),
            source="test",
        )

        # Act
        result = host._dispatch("/ping")

        # Assert
        assert result.success is True, "dispatch returns success"
        assert result.value == "pong", "dispatch returns handler value"


# -- _apply_port_effects -----------------------------------------------------


class TestApplyPortEffects:
    def test_cfg_update(self, host):
        # Act
        host._apply_port_effects({"cfg_update": {"baud_rate": 9600}})

        # Assert
        assert host.repl._cfg_data["baud_rate"] == 9600, "cfg updated"

    def test_empty_effects(self, host):
        # Act - should not raise
        host._apply_port_effects({})


# -- _start_capture / _stop_capture ------------------------------------------


class TestCapture:
    def test_start_when_already_active(self, host):
        # Arrange
        host.capture.active = True

        # Act
        actual = host._start_capture(mode="text", path="/tmp/x")

        # Assert
        assert actual is False, "returns False when already active"
        assert "already active" in host._status_msgs[0].lower(), "shows message"

    def test_start_failure(self, host):
        # Arrange
        host.capture.active = False
        host.capture.start.return_value = False

        # Act
        actual = host._start_capture(mode="text", path="/tmp/x")

        # Assert
        assert actual is False, "returns False on engine failure"

    def test_start_success(self, host):
        # Arrange
        host.capture.active = False
        host.capture.start.return_value = True

        # Act
        actual = host._start_capture(mode="text", path="/tmp/cap.txt")

        # Assert
        assert actual is True, "returns True on success"
        assert any("Capture started" in m for m in host._status_msgs), \
            "shows started message"

    def test_stop_with_result(self, host):
        # Arrange
        mock_result = MagicMock()
        mock_result.path = "/tmp/cap.txt"
        mock_result.size_label = "1.2 KB"
        mock_result.error = ""
        host.capture.stop.return_value = mock_result

        # Act
        host._stop_capture()

        # Assert
        assert any("Capture complete" in m for m in host._status_msgs), \
            "shows completion message"

    def test_stop_no_result(self, host):
        # Arrange
        host.capture.stop.return_value = None

        # Act
        host._stop_capture()

        # Assert
        assert len(host._status_msgs) == 0, "no message when nothing to stop"


# -- _hook_help_open ---------------------------------------------------------


class TestHookHelpOpen:
    def test_unknown_topic(self, host):
        # Act
        result = host._hook_help_open(None, "__does_not_exist__")

        # Assert
        assert result.success is False, "fails for unknown topic"
        assert "Unknown help topic" in result.error, "error mentions topic"

    def test_empty_topic_resolves_to_index(self, host):
        # Arrange - patch _ensure_help_server and webbrowser.open
        host._ensure_help_server = MagicMock(return_value=8080)

        with patch("webbrowser.open") as mock_open:
            # Act
            result = host._hook_help_open(None, "")

        # Assert
        assert result.success is True, "succeeds for empty topic (index)"
        # opens index.html
        mock_open.assert_called_once_with("http://127.0.0.1:8080/index.html")


# -- _ensure_help_server -----------------------------------------------------


class TestEnsureHelpServer:
    def test_starts_server(self, host):
        # Act
        port = host._ensure_help_server()

        # Assert
        assert port > 0, "returns a valid port number"
        assert host._help_server_port == port, "caches the port"

    def test_returns_cached_port(self, host):
        # Arrange
        first_port = host._ensure_help_server()

        # Act
        second_port = host._ensure_help_server()

        # Assert
        assert first_port == second_port, "returns same port on second call"


# -- _hook_raw ---------------------------------------------------------------


class TestHookRaw:
    def test_not_connected(self, host):
        # Arrange
        host.engine.is_connected = False

        # Act
        result = host._hook_raw(None, "hello")

        # Assert
        assert result.success is False, "fails when not connected"

    def test_empty_args(self, host):
        # Arrange
        host.engine.is_connected = True

        # Act
        result = host._hook_raw(None, "")

        # Assert
        assert result.success is False, "fails with empty args"

    def test_sends_bytes(self, host):
        # Arrange
        host.engine.is_connected = True
        host.engine.serial_port = MagicMock()

        # Act
        result = host._hook_raw(None, "AT")

        # Assert
        assert result.success is True, "succeeds"
        # raw bytes sent to port
        host.engine.serial_port.write.assert_called_once_with(b"AT")


# -- _hook_log_delete ---------------------------------------------------------


class TestHookLogClear:
    def test_no_log_file(self, host):
        # Arrange - config_path has no log file

        # Act
        result = host._hook_log_delete(None, "")

        # Assert
        assert result.success is False, "fails when no log file"

    def test_deletes_log_file(self, host, tmp_path):
        # Arrange
        log_path = tmp_path / "test_cfg" / "test.log"
        log_path.write_text("session log data")
        with patch("termapy.config.cfg_log_path", return_value=str(log_path)):
            # Act
            result = host._hook_log_delete(None, "")

        # Assert
        assert result.success is True, "succeeds when log exists"
        assert not log_path.exists(), "log file deleted"

    def test_delete_oserror(self, host, tmp_path):
        # Arrange
        log_path = tmp_path / "test_cfg" / "test.log"
        log_path.write_text("session log data")
        with patch("termapy.config.cfg_log_path", return_value=str(log_path)), \
             patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            # Act
            result = host._hook_log_delete(None, "")

        # Assert
        assert result.success is False, "fails on OSError"
        assert "permission denied" in result.error, "error message passed through"
