"""Unit tests for TerminalHost - context builders, serial I/O, hooks."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from serial import SerialException

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
    cfg = {"port": "COM99", "baud_rate": 115200, "eol": "\r",
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

    repl = ReplEngine(cfg, str(config_path), write=lambda t, c="": None)

    h = _StubHost(cfg, str(config_path), mock_engine, repl, mock_capture)
    return h


# -- _build_internal_handle -------------------------------------------------------


class TestBuildSerialHandle:
    """Serial lifecycle verbs delegate to the host (moved off ctx.internal)."""

    def test_connect_callback_wired(self, host):
        """ctx.serial.connect reaches host._connect with the port name unchanged."""
        # Arrange
        host._connect = MagicMock()

        # Act
        serial = host._build_plugin_context(host._build_internal_handle()).serial
        serial.connect("COM1")

        # Assert
        # connect delegates to host (the mock method raises on mismatch)
        host._connect.assert_called_once_with("COM1")

    def test_disconnect_callback_wired(self, host):
        """ctx.serial.disconnect reaches host._disconnect."""
        # Arrange
        host._disconnect = MagicMock()

        # Act
        serial = host._build_plugin_context(host._build_internal_handle()).serial
        serial.disconnect()

        # Assert
        # disconnect delegates to host
        host._disconnect.assert_called_once()

    def test_apply_port_effects_wired(self, host):
        """ctx.serial.apply_port_effects reaches host._apply_port_effects."""
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
        """_build_internal_handle yields the InternalHandle type that ctx.internal expects."""
        # Act
        api = host._build_internal_handle()

        # Assert
        assert isinstance(api, InternalHandle), "returns InternalHandle instance"

    def test_start_capture_wired(self, host):
        """InternalHandle.start_capture forwards its keyword args to host._start_capture."""
        # Arrange
        host._start_capture = MagicMock(return_value=True)

        # Act
        api = host._build_internal_handle()
        api.start_capture(mode="text", path="/tmp/x")

        # Assert
        # start_capture delegates to host
        host._start_capture.assert_called_once_with(mode="text", path="/tmp/x")

    def test_script_stop_event_wired(self, host):
        """script_stop_event is the REPL's own stop event, not a copy, so a stop propagates."""
        # Act
        api = host._build_internal_handle()

        # Assert
        assert api.script_stop_event is host.repl._script_stop, \
            "script_stop_event is repl's stop event"


# -- _build_plugin_context ---------------------------------------------------


class TestBuildPluginContext:
    def test_returns_plugin_context(self, host):
        """_build_plugin_context yields a real PluginContext for handlers."""
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert isinstance(ctx, PluginContext), "returns PluginContext instance"

    def test_cfg_wired(self, host):
        """ctx.cfg is the host's live cfg dict, so a handler's edits are the host's."""
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert ctx.cfg is host.cfg, "cfg is the host's cfg dict"

    def test_config_path_wired(self, host):
        """ctx.config_path carries the host's config path."""
        # Arrange
        api = host._build_internal_handle()

        # Act
        ctx = host._build_plugin_context(api)

        # Assert
        assert ctx.config_path == host.config_path, "config_path matches host"

    def test_serial_write_wired(self, host):
        """ctx.serial.write sends bytes through the engine's port unchanged."""
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
        """ctx.fs data-folder paths (ss, run, proto, cap, prof) match the REPL's."""
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
        """ctx.serial.is_connected reads live engine state, set after the context was built."""
        # Arrange
        api = host._build_internal_handle()
        ctx = host._build_plugin_context(api)
        host.engine.is_connected = True

        # Act
        actual = ctx.serial.is_connected()

        # Assert
        assert actual is True, "is_connected reflects engine state"

    def test_confirm_wired(self, host):
        """With the confirm_dialog capability, ctx.ui.confirm returns host._confirm's answer."""
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
    def test_device_echo_seeded_from_cfg_when_allowed(self, host):
        """On an echoing host, cfg echo=True seeds the echo flag; output_level starts normal."""
        # Arrange -- device_echo_allowed=True (TUI/CLI); flags["echo"] is
        # seeded from cfg['echo'], the same pattern as hex_mode.
        host.cfg["echo"] = True
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(device_echo_allowed=True)

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["echo"] is True, "echo seeded True from cfg['echo']"
        assert flags["output_level"] == "normal", "output_level defaults to normal"

    def test_device_echo_off_when_cfg_off(self, host):
        """cfg echo=False keeps device echo off even on a host allowed to echo."""
        # Arrange -- host may echo, but cfg says off -> flag off.
        host.cfg["echo"] = False
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(device_echo_allowed=True)

        # Assert
        assert host.ctx.ns("flags")["echo"] is False, (
            "echo seeded from cfg (off) even when the host may echo"
        )

    def test_device_echo_forced_off_for_non_echoing_host(self, host):
        """A non-echoing host (MCP) forces device echo off even when cfg turns it on."""
        # Arrange -- MCP passes device_echo_allowed=False; force off
        # regardless of cfg (no terminal to echo to).
        host.cfg["echo"] = True
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(device_echo_allowed=False)

        # Assert
        assert host.ctx.ns("flags")["echo"] is False, (
            "echo forced off for MCP even when cfg['echo'] is True"
        )

    def test_echo_repl_from_host_default_not_cfg(self, host):
        """echo_repl comes from the host default, independent of the cfg device-echo key."""
        # Arrange -- echo_repl is session-only with a per-host default:
        # TUI passes True, CLI/MCP False.  It is not read from cfg.
        host.cfg["echo"] = False  # device echo unrelated to repl echo
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(echo_repl=True)

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["echo_repl"] is True, "echo_repl set from the host default"
        assert flags["echo"] is False, "device echo independent of repl echo"

    def test_echo_repl_off_by_host_default(self, host):
        """A host default of False (CLI, MCP) leaves REPL echo off."""
        # Arrange -- CLI/MCP pass echo_repl=False.
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags(echo_repl=False)

        # Assert
        assert host.ctx.ns("flags")["echo_repl"] is False, (
            "echo_repl off when the host default is False"
        )

    def test_hex_mode_from_cfg(self, host):
        """The hex display flag is seeded from cfg, the same way device echo is."""
        # Arrange
        host.cfg["hex"] = True
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)

        # Act
        host._init_flags()

        # Assert
        flags = host.ctx.ns("flags")
        assert flags["hex"] is True, "hex_mode read from cfg"


# -- _serial_write / _serial_send -------------------------------------------


class TestSerialIO:
    def test_serial_write_no_port(self, host):
        """tx-only-what-went-out: a write with no open port raises Not connected and notifies no TX observer."""
        # Arrange
        host.engine.serial_port = None

        # Act / Assert
        with pytest.raises(SerialException, match="Not connected"):
            host._serial_write(b"hello")
        host.engine.notify_tx.assert_not_called()

    def test_failed_write_notifies_no_tx_observer(self, host):
        """tx-only-what-went-out: a write the port refuses raises to the caller and reaches no TX observer."""
        # Arrange
        host.engine.serial_port = MagicMock()
        host.engine.serial_port.write.side_effect = SerialException("simulated unplug")

        # Act / Assert
        with pytest.raises(SerialException, match="simulated unplug"):
            host._serial_write(b"AT\r")
        host.engine.notify_tx.assert_not_called()

    def test_serial_write_with_port(self, host):
        """_serial_write sends the bytes verbatim and tells TX observers what went out."""
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
        """_serial_send appends the configured eol (CR here) and writes the encoded line."""
        # Arrange
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_send("AT")

        # Assert
        expected = b"AT\r"
        # text + line_ending encoded and written
        host.engine.serial_port.write.assert_called_once_with(expected)

    def test_serial_send_custom_encoding(self, host):
        """_serial_send honors a non-default encoding and eol taken from cfg."""
        # Arrange
        host.cfg["encoding"] = "ascii"
        host.cfg["eol"] = "\n"
        host.engine.serial_port = MagicMock()

        # Act
        host._serial_send("OK")

        # Assert
        expected = b"OK\n"
        # custom encoding and line ending used
        host.engine.serial_port.write.assert_called_once_with(expected)



# -- _dispatch ---------------------------------------------------------------


class TestDispatch:
    def test_dispatch_delegates_to_repl(self, host):
        """_dispatch runs the full REPL pipeline and hands back the handler's CmdResult."""
        # Arrange
        api = host._build_internal_handle()
        host.ctx = host._build_plugin_context(api)
        host.repl.set_context(host.ctx)
        host._init_flags()  # echo state irrelevant to this delegation test
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
        """A cfg_update key with no serial block to route into lands at the cfg top level."""
        # Act
        host._apply_port_effects({"cfg_update": {"baud_rate": 9600}})

        # Assert
        assert host.repl._cfg_data["baud_rate"] == 9600, "cfg updated"

    def test_empty_effects(self, host):
        """An effects dict with no cfg_update is a harmless no-op."""
        # Act - should not raise
        host._apply_port_effects({})


# -- _start_capture / _stop_capture ------------------------------------------


class TestCapture:
    def test_start_when_already_active(self, host):
        """A second capture start is refused with a status message, never stacked."""
        # Arrange
        host.capture.active = True

        # Act
        actual = host._start_capture(mode="text", path="/tmp/x")

        # Assert
        assert actual is False, "returns False when already active"
        assert "already active" in host._status_msgs[0].lower(), "shows message"

    def test_start_failure(self, host):
        """When the capture engine cannot start, _start_capture returns False."""
        # Arrange
        host.capture.active = False
        host.capture.start.return_value = False

        # Act
        actual = host._start_capture(mode="text", path="/tmp/x")

        # Assert
        assert actual is False, "returns False on engine failure"

    def test_start_success(self, host):
        """A started capture returns True and announces itself on the status line."""
        # Arrange
        host.capture.active = False
        host.capture.start.return_value = True

        # Act
        actual = host._start_capture(mode="text", path="/tmp/cap.txt")

        # Assert
        assert actual is True, "returns True on success"
        assert any("Capture started" in status_msg for status_msg in host._status_msgs), \
            "shows started message"

    def test_stop_delegates_message_to_on_complete(self, host):
        """_stop_capture emits no completion line itself; on_complete owns it, so none doubles."""
        # Arrange -- the completion message is owned by the CaptureEngine
        # on_complete callback (fired inside stop()), NOT by _stop_capture.
        # This mocked engine never fires on_complete, so _stop_capture must
        # emit nothing itself -- proving it can't double the real callback.
        mock_result = MagicMock()
        mock_result.error = ""
        host.capture.stop.return_value = mock_result

        # Act
        host._stop_capture()

        # Assert
        host.capture.stop.assert_called_once()
        assert not any("Capture complete" in status_msg for status_msg in host._status_msgs), \
            "_stop_capture must not emit the message (on_complete owns it)"

    def test_stop_no_result(self, host):
        """Stopping when the engine returns no result produces no status message."""
        # Arrange
        host.capture.stop.return_value = None

        # Act
        host._stop_capture()

        # Assert
        assert len(host._status_msgs) == 0, "no message when nothing to stop"


# -- _hook_help_open ---------------------------------------------------------


class TestHookHelpOpen:
    def test_unknown_topic(self, host):
        """A topic with no built HTML page fails as an unknown help topic."""
        # Act
        result = host._hook_help_open(None, "__does_not_exist__")

        # Assert
        assert result.success is False, "fails for unknown topic"
        assert "Unknown help topic" in result.error, "error mentions topic"

    def test_empty_topic_resolves_to_index(self, host):
        """No topic opens index.html from the local help server in the browser."""
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
        """The first call starts a local help server and caches its bound port."""
        # Act
        port = host._ensure_help_server()

        # Assert
        assert port > 0, "returns a valid port number"
        assert host._help_server_port == port, "caches the port"

    def test_returns_cached_port(self, host):
        """A second call reuses the running server rather than starting another."""
        # Arrange
        first_port = host._ensure_help_server()

        # Act
        second_port = host._ensure_help_server()

        # Assert
        assert first_port == second_port, "returns same port on second call"


# -- _hook_log_delete ---------------------------------------------------------


class TestHookLogClear:
    def test_no_log_file(self, host):
        """Deleting the log when none exists on disk fails rather than reporting success."""
        # Arrange - config_path has no log file

        # Act
        result = host._hook_log_delete(None, "")

        # Assert
        assert result.success is False, "fails when no log file"

    def test_deletes_log_file(self, host, tmp_path):
        """The log delete hook removes the session log file from disk."""
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
        """An OSError from unlink fails the delete and passes the OS message through."""
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
