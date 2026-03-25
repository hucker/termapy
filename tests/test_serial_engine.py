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
        assert result is True  # connected
        assert engine.is_connected is True
        assert engine.serial_port is not None
        assert engine.reader is not None
        engine.disconnect()

    def test_connect_when_already_connected(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        result = engine.connect()

        # Assert
        assert result is True  # idempotent
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
        assert result is False  # failed
        assert engine.is_connected is False

    def test_disconnect(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.disconnect()

        # Assert
        assert engine.is_connected is False
        assert engine.serial_port is None

    def test_disconnect_when_not_connected(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act — should not raise
        engine.disconnect()

        # Assert
        assert engine.is_connected is False


# -- Properties ----------------------------------------------------------------


class TestProperties:
    def test_port_obj_is_fake_serial(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Assert
        assert isinstance(engine.port_obj, FakeSerial)
        engine.disconnect()

    def test_rx_queue_exists(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.rx_queue is not None

    def test_proto_active_default(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.proto_active is False

    def test_proto_active_setter(self):
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.proto_active = True

        # Assert
        assert engine.proto_active is True
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
        assert len(lines_received) > 0  # got some output
        assert any("OK" in line for line in lines_received)  # AT → OK

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
        assert not t.is_alive()  # thread exited
        assert engine.reader_stopped.is_set()  # flag set

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
        assert len(errors) >= 1  # error callback fired
        assert "read failed" in errors[0]

    def test_read_loop_without_connect(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act — should return immediately
        engine.read_loop()

        # Assert
        assert engine.reader_stopped.is_set()


# -- Reconnect ----------------------------------------------------------------


class TestReconnect:
    def test_try_reconnect_success(self):
        # Arrange
        engine, _, _ = _make_engine()

        # Act
        result = engine.try_reconnect()

        # Assert
        assert result is True  # FakeSerial always opens

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
        assert result is False
