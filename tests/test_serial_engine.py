"""Tests for SerialEngine - connection lifecycle and reader loop."""

import threading
import time

import pytest

from termapy.capture import CaptureEngine
from termapy.demo import FakeSerial
from termapy.serial_engine import SerialEngine


def _make_engine(cfg=None):
    """Create a SerialEngine with FakeSerial as the open function."""
    cfg = cfg or {"port": "DEMO", "baud_rate": 115200, "encoding": "utf-8",
                  "line_ending": "\r", "show_line_endings": False}
    capture = CaptureEngine()
    logged = []
    engine = SerialEngine(
        cfg=cfg,
        capture=capture,
        open_fn=lambda c: FakeSerial(baudrate=c["baud_rate"]),
        log=lambda d, t: logged.append((d, t)),
    )
    return engine, capture, logged


# -- Connection lifecycle ------------------------------------------------------


class TestConnect:
    def test_connect_succeeds(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act
        result = engine.connect()

        # Assert
        assert result is True, "connected"
        assert engine.is_connected is True, "engine should be connected"
        assert engine.serial_port is not None, "serial_port should be set"
        assert engine.reader is not None, "reader should be set"
        engine.disconnect()

    def test_connect_when_already_connected(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        result = engine.connect()

        # Assert
        assert result is True, "idempotent"
        engine.disconnect()

    def test_connect_failure(self):
        # Arrange
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"port": "BAD", "baud_rate": 9600, "encoding": "utf-8"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )

        # Act
        result = engine.connect()

        # Assert
        assert result is False, "failed"
        assert engine.is_connected is False, "engine should not be connected"

    def test_disconnect(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.disconnect()

        # Assert
        assert engine.is_connected is False, "engine should be disconnected"
        assert engine.serial_port is None, "serial_port should be cleared"

    def test_disconnect_when_not_connected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act - should not raise
        engine.disconnect()

        # Assert
        assert engine.is_connected is False, "engine should remain disconnected"


# -- Properties ----------------------------------------------------------------


class TestProperties:
    def test_port_obj_is_fake_serial(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Assert
        assert isinstance(engine.port_obj, FakeSerial), "port_obj should be FakeSerial"
        engine.disconnect()

    def test_rx_queue_exists(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.rx_queue is not None, "rx_queue should exist"

    def test_proto_active_default(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.proto_active is False, "proto_active should default to False"

    def test_proto_active_setter(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.proto_active = True

        # Assert
        assert engine.proto_active is True, "proto_active should be True after set"
        engine.disconnect()


# -- Reader loop ---------------------------------------------------------------


class TestReadLoop:
    @pytest.mark.flaky
    def test_read_loop_receives_lines(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Send a command to generate a response
        engine.port_obj.write(b"AT\r")
        time.sleep(0.05)

        lines_received = []

        def run():
            engine.read_loop(on_lines=lines_received.extend)

        # Act - run reader in a thread, stop after brief delay
        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        engine.stop_event.set()
        t.join(timeout=1.0)

        # Assert
        assert len(lines_received) > 0, "got some output"
        assert any("OK" in line for line in lines_received), "AT should produce OK"

    @pytest.mark.flaky
    def test_read_loop_stops_on_event(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        def run():
            engine.read_loop()

        # Act
        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.1)
        engine.stop_event.set()
        t.join(timeout=1.0)

        # Assert
        assert not t.is_alive(), "thread exited"
        assert engine.reader_stopped.is_set(), "flag set"

    def test_read_loop_calls_on_error(self):
        # Arrange - port that raises on read
        errors = []

        class BadPort:
            is_open = True
            in_waiting = 1
            def read(self, n):
                raise OSError("read failed")
            def close(self):
                self.is_open = False

        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"encoding": "utf-8", "show_line_endings": False},
            capture=capture,
            open_fn=lambda c: BadPort(),
        )
        engine.connect()

        def run():
            engine.read_loop(on_error=errors.append)

        # Act
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=1.0)

        # Assert
        assert len(errors) >= 1, "error callback fired"
        assert "read failed" in errors[0], "error message should contain 'read failed'"

    def test_read_loop_without_connect(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act - should return immediately
        engine.read_loop()

        # Assert
        assert engine.reader_stopped.is_set(), "reader_stopped should be set without connect"


# -- Reconnect ----------------------------------------------------------------


class TestReconnect:
    def test_try_reconnect_success(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act
        result = engine.try_reconnect()

        # Assert
        assert result is True, "FakeSerial always opens"

    def test_try_reconnect_failure(self):
        # Arrange
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"port": "BAD"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )

        # Act
        result = engine.try_reconnect()

        # Assert
        assert result is False, "reconnect should fail with bad port"


# -- RX observers ---------------------------------------------------------------


class TestRxObservers:

    def test_add_and_remove(self):
        # Arrange
        engine, _, _ = _make_engine()
        received = []
        def cb(data):
            return received.append(data)

        # Act
        engine.add_rx_observer(cb)
        engine.remove_rx_observer(cb)

        # Assert - no error, observer list empty
        assert cb not in engine._rx_observers, "observer should be removed"

    def test_add_duplicate_is_noop(self):
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_rx_observer(cb)
        engine.add_rx_observer(cb)

        # Assert
        assert engine._rx_observers.count(cb) == 1, "should not add duplicate"

    def test_remove_nonexistent_is_noop(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert - should not raise
        engine.remove_rx_observer(lambda data: None)

    @pytest.mark.flaky
    def test_observer_receives_rx_data(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        received = []
        engine.add_rx_observer(lambda data: received.append(data))

        # Send a command to generate a response
        engine.port_obj.write(b"AT\r")
        time.sleep(0.05)

        # Act - run the reader briefly
        t = threading.Thread(
            target=lambda: engine.read_loop(on_lines=lambda lines: None),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        engine.stop_event.set()
        t.join(timeout=2)

        # Assert
        assert len(received) > 0, "observer should have received RX data"
        assert all(isinstance(d, bytes) for d in received), "data should be bytes"
        engine.disconnect()

    @pytest.mark.flaky
    def test_exception_in_observer_does_not_block_others(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        received = []

        def bad_observer(data):
            raise ValueError("boom")

        engine.add_rx_observer(bad_observer)
        engine.add_rx_observer(lambda data: received.append(data))

        # Send a command to generate a response
        engine.port_obj.write(b"AT\r")
        time.sleep(0.05)

        # Act
        t = threading.Thread(
            target=lambda: engine.read_loop(on_lines=lambda lines: None),
            daemon=True,
        )
        t.start()
        time.sleep(0.3)
        engine.stop_event.set()
        t.join(timeout=2)

        # Assert - second observer should still receive data
        assert len(received) > 0, "second observer should work despite first raising"
        engine.disconnect()


# -- TX observers ---------------------------------------------------------------


class TestTxObservers:

    def test_add_and_remove(self):
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_tx_observer(cb)
        engine.remove_tx_observer(cb)

        # Assert
        assert cb not in engine._tx_observers, "observer should be removed"

    def test_notify_tx_fires_observers(self):
        # Arrange
        engine, _, _ = _make_engine()
        received = []
        engine.add_tx_observer(lambda data: received.append(data))

        # Act
        engine.notify_tx(b"hello")
        engine.notify_tx(b"world")

        # Assert
        assert received == [b"hello", b"world"], "observer should receive TX data"

    def test_exception_in_tx_observer_does_not_block_others(self):
        # Arrange
        engine, _, _ = _make_engine()
        received = []

        def bad_observer(data):
            raise ValueError("boom")

        engine.add_tx_observer(bad_observer)
        engine.add_tx_observer(lambda data: received.append(data))

        # Act
        engine.notify_tx(b"test")

        # Assert
        assert received == [b"test"], "second observer should work despite first raising"

    def test_add_duplicate_is_noop(self):
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_tx_observer(cb)
        engine.add_tx_observer(cb)

        # Assert
        assert engine._tx_observers.count(cb) == 1, "should not add duplicate"


# -- Hardware signal control ---------------------------------------------------


class TestHardwareSignals:
    def test_toggle_dtr(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        result = engine.toggle_dtr()

        # Assert
        assert isinstance(result, bool), "should return bool"
        engine.disconnect()

    def test_toggle_rts(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        result = engine.toggle_rts()

        # Assert
        assert isinstance(result, bool), "should return bool"
        engine.disconnect()

    def test_send_break(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act / Assert - should not raise
        engine.send_break()
        engine.disconnect()

    def test_get_hw_state(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        dtr, rts = engine.get_hw_state()

        # Assert
        assert isinstance(dtr, bool), "DTR should be bool"
        assert isinstance(rts, bool), "RTS should be bool"
        engine.disconnect()

    def test_toggle_dtr_when_disconnected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.toggle_dtr()

    def test_toggle_rts_when_disconnected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.toggle_rts()

    def test_send_break_when_disconnected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.send_break()

    def test_get_hw_state_when_disconnected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.get_hw_state()


# -- Reconnect loop ------------------------------------------------------------


@pytest.mark.slow  # ~2.5s sleep-based reconnect simulation
class TestReconnectLoop:
    def test_reconnect_loop_success(self):
        # Arrange
        engine, _, _ = _make_engine()
        statuses = []

        # Act - FakeSerial always succeeds, so first try should work
        def run():
            return engine.reconnect_loop(on_status=statuses.append)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=5.0)

        # Assert
        assert len(statuses) > 0, "should have received status updates"
        assert all("Connecting" in s for s in statuses), "status should show connecting"

    @pytest.mark.flaky
    def test_reconnect_loop_cancelled(self):
        # Arrange - use a port that always fails
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"port": "BAD"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )

        # Act - cancel after brief delay
        def run():
            return engine.reconnect_loop(interval=0.5)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        engine.stop_event.set()
        t.join(timeout=2.0)

        # Assert
        assert not t.is_alive(), "thread should have exited"


# -- Error classification & port-holder lookup ---------------------------------


class TestClassifySerialError:
    """_classify_serial_error maps raw exceptions to friendly messages,
    and on Unix tries to name the process holding the port."""

    def test_permission_error_without_port_name(self):
        # Arrange
        from termapy.serial_engine import _classify_serial_error
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert -- no port name passed, no holder lookup performed
        assert "Permission denied" in actual, "friendly permission message"
        assert "held by" not in actual, "no holder appended when port_name empty"

    def test_file_not_found_returns_helpful_message(self):
        # Arrange
        from termapy.serial_engine import _classify_serial_error
        inner = FileNotFoundError("no such device")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert
        assert "Port not found" in actual, "friendly not-found message"
        assert "/port.list" in actual, "points user at the discovery command"

    def test_oserror_errno_13_treated_as_permission(self, monkeypatch):
        # Arrange -- errno 13 is EACCES on POSIX
        from termapy.serial_engine import _classify_serial_error
        inner = OSError(13, "Permission denied")
        outer = Exception("open failed")
        outer.__cause__ = inner
        # No holder lookup (port_name empty) so sys.platform doesn't matter.

        # Act
        actual = _classify_serial_error(outer, port_name="")

        # Assert
        assert "Permission denied" in actual, "classified as in-use"

    def test_oserror_errno_2_treated_as_not_found(self):
        # Arrange -- errno 2 is ENOENT
        from termapy.serial_engine import _classify_serial_error
        inner = OSError(2, "No such file")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert
        assert "Port not found" in actual, "classified as missing"

    def test_permission_error_with_holder_is_appended(self, monkeypatch):
        # Arrange -- stub out the holder lookup to return a known value.
        import termapy.serial_engine as se
        monkeypatch.setattr(se, "_find_port_holder", lambda _p: "arduino (PID 1234)")
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = se._classify_serial_error(outer, port_name="ttyUSB0")

        # Assert
        assert "Permission denied" in actual, "still has the base message"
        assert "held by arduino (PID 1234)" in actual, "holder appended"

    def test_permission_error_holder_lookup_returns_none(self, monkeypatch):
        # Arrange -- holder lookup fails silently (Windows, or lsof missing)
        import termapy.serial_engine as se
        monkeypatch.setattr(se, "_find_port_holder", lambda _p: None)
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = se._classify_serial_error(outer, port_name="ttyUSB0")

        # Assert
        assert "Permission denied" in actual, "base message intact"
        assert "held by" not in actual, "no holder clause when lookup returned None"

    def test_pyserial_windows_permission_via_message(self):
        # Arrange -- mimic the exact shape pyserial raises on Windows when
        # another app holds the port: SerialException with the OSError
        # stringified into the message and no __cause__ chaining.
        from termapy.serial_engine import _classify_serial_error
        exc = Exception(
            "could not open port 'COM4': "
            "PermissionError(13, 'Access is denied.', None, 5)"
        )
        # cause deliberately None -- this is the bug we're guarding against.
        assert exc.__cause__ is None, "pyserial doesn't chain; the test knows that"

        # Act
        actual = _classify_serial_error(exc, port_name="COM4")

        # Assert
        assert "Permission denied -- port may be in use" in actual, \
            f"classified via string match, got {actual!r}"
        assert "PermissionError" not in actual, \
            "raw exception-class text is not shown to the user"

    def test_pyserial_windows_file_not_found_via_message(self):
        # Arrange -- same pattern for a missing port.
        from termapy.serial_engine import _classify_serial_error
        exc = Exception(
            "could not open port 'COM99': "
            "FileNotFoundError(2, 'The system cannot find the file specified.', None, 2)"
        )

        # Act
        actual = _classify_serial_error(exc, port_name="COM99")

        # Assert
        assert "Port not found" in actual, f"classified as missing, got {actual!r}"


class TestFindPortHolder:
    """_find_port_holder uses lsof on Unix; silent None on Windows
    or any failure."""

    def test_windows_returns_none(self, monkeypatch):
        # Arrange
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "win32")

        # Act
        actual = se._find_port_holder("COM7")

        # Assert
        assert actual is None, "no holder lookup on Windows"

    def test_lsof_missing_returns_none(self, monkeypatch):
        # Arrange -- simulate lsof not installed on PATH.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        def _raise_filenotfound(*args, **kwargs):
            raise FileNotFoundError("lsof")

        monkeypatch.setattr(se.subprocess, "run", _raise_filenotfound)

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "missing lsof doesn't raise, just bails silently"

    def test_lsof_timeout_returns_none(self, monkeypatch):
        # Arrange
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        def _raise_timeout(*args, **kwargs):
            raise se.subprocess.TimeoutExpired(cmd="lsof", timeout=2.0)

        monkeypatch.setattr(se.subprocess, "run", _raise_timeout)

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "timeout bails silently"

    def test_lsof_success_parses_pid_and_command(self, monkeypatch):
        # Arrange -- simulate lsof -F pc output format.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        class _Result:
            returncode = 0
            stdout = "p1234\ncarduino-ide\n"

        monkeypatch.setattr(se.subprocess, "run", lambda *a, **kw: _Result())

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        expected = "arduino-ide (PID 1234)"
        assert actual == expected, f"parsed as command + PID, got {actual!r}"

    def test_lsof_success_with_nothing_holding(self, monkeypatch):
        # Arrange -- lsof exit=0 but no output means nothing has the port.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        class _Result:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(se.subprocess, "run", lambda *a, **kw: _Result())

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "empty output -> no holder identified"

    def test_lsof_absolute_path_passed_through(self, monkeypatch):
        # Arrange -- user-supplied port already looks absolute; don't
        # prepend /dev/ again.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        captured = {}

        class _Result:
            returncode = 1
            stdout = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        monkeypatch.setattr(se.subprocess, "run", _fake_run)

        # Act
        se._find_port_holder("/dev/tty.usbserial-XYZ")

        # Assert -- argv[-1] is the path lsof queried.
        actual_path = captured["cmd"][-1]
        expected_path = "/dev/tty.usbserial-XYZ"
        assert actual_path == expected_path, \
            "absolute path passed through untouched"
