"""Tests for SerialEngine — connection lifecycle and reader loop."""

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

        # Act — should not raise
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

        # Act — run reader in a thread, stop after brief delay
        t = threading.Thread(target=run, daemon=True)
        t.start()
        time.sleep(0.3)
        engine.stop_event.set()
        t.join(timeout=1.0)

        # Assert
        assert len(lines_received) > 0, "got some output"
        assert any("OK" in line for line in lines_received), "AT should produce OK"

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
        # Arrange — port that raises on read
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

        # Act — should return immediately
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
        cb = lambda data: received.append(data)

        # Act
        engine.add_rx_observer(cb)
        engine.remove_rx_observer(cb)

        # Assert — no error, observer list empty
        assert cb not in engine._rx_observers, "observer should be removed"

    def test_add_duplicate_is_noop(self):
        # Arrange
        engine, _, _ = _make_engine()
        cb = lambda data: None

        # Act
        engine.add_rx_observer(cb)
        engine.add_rx_observer(cb)

        # Assert
        assert engine._rx_observers.count(cb) == 1, "should not add duplicate"

    def test_remove_nonexistent_is_noop(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert — should not raise
        engine.remove_rx_observer(lambda data: None)

    def test_observer_receives_rx_data(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        received = []
        engine.add_rx_observer(lambda data: received.append(data))

        # Send a command to generate a response
        engine.port_obj.write(b"AT\r")
        time.sleep(0.05)

        # Act — run the reader briefly
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

        # Assert — second observer should still receive data
        assert len(received) > 0, "second observer should work despite first raising"
        engine.disconnect()
