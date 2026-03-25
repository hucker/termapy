"""Tests for SerialPort — serial I/O wrapper using FakeSerial."""

import queue
import time

import pytest

from termapy.demo import FakeSerial
from termapy.serial_port import SerialPort


@pytest.fixture
def port_env():
    """Create a SerialPort wrapping FakeSerial with a log capture list."""
    fake = FakeSerial()
    rx_queue: queue.Queue[bytes] = queue.Queue()
    logged: list[tuple[str, str]] = []
    sp = SerialPort(
        port=fake,
        rx_queue=rx_queue,
        log=lambda d, t: logged.append((d, t)),
    )
    return sp, fake, rx_queue, logged


# -- Properties ----------------------------------------------------------------


class TestProperties:
    def test_is_open(self, port_env):
        sp, fake, _, _ = port_env

        # Assert
        assert sp.is_open is True  # FakeSerial starts open

    def test_is_open_after_close(self, port_env):
        sp, fake, _, _ = port_env

        # Act
        fake.close()

        # Assert
        assert sp.is_open is False  # reflects closed state

    def test_port_property(self, port_env):
        sp, fake, _, _ = port_env

        # Assert
        assert sp.port is fake  # returns the underlying port


# -- Write ---------------------------------------------------------------------


class TestWrite:
    def test_write_sends_data(self, port_env):
        # Arrange
        sp, fake, _, _ = port_env

        # Act
        sp.write(b"AT\r")

        # Assert — read back from FakeSerial's response
        time.sleep(0.05)
        data = fake.read(1024)
        assert b"OK" in data  # FakeSerial responded to AT command

    def test_write_logs_tx(self, port_env):
        # Arrange
        sp, _, _, logged = port_env

        # Act
        sp.write(b"ATZ\r")

        # Assert
        assert len(logged) >= 1  # at least one log entry
        assert logged[0][0] == ">"  # TX direction
        assert "ATZ" in logged[0][1]  # command logged

    def test_write_logs_hex_for_binary(self, port_env):
        # Arrange
        sp, _, _, logged = port_env

        # Act
        sp.write(b"\x01\x02\xff")

        # Assert
        assert logged[0][0] == ">"  # TX direction
        assert "01 02 ff" in logged[0][1]  # hex representation


# -- Drain ---------------------------------------------------------------------


class TestDrain:
    def test_drain_empty_queue(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act
        actual = sp.drain()

        # Assert
        assert actual == 0  # nothing to drain

    def test_drain_returns_byte_count(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        rx_queue.put(b"\x01\x02\x03")
        rx_queue.put(b"\x04\x05")

        # Act
        actual = sp.drain()

        # Assert
        assert actual == 5  # 3 + 2 bytes drained
        assert rx_queue.empty()  # queue is empty


# -- Read Raw ------------------------------------------------------------------


class TestReadRaw:
    def test_read_raw_returns_data(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        rx_queue.put(b"\x01\x02\x03")

        # Act
        actual = sp.read_raw(timeout_ms=500, frame_gap_ms=50)

        # Assert
        assert actual == b"\x01\x02\x03"  # data returned

    def test_read_raw_timeout_returns_empty(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act
        actual = sp.read_raw(timeout_ms=100, frame_gap_ms=50)

        # Assert
        assert actual == b""  # timed out, no data

    def test_read_raw_assembles_chunks(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        # Put two chunks close together — should assemble into one frame
        rx_queue.put(b"\x01\x02")
        rx_queue.put(b"\x03\x04")

        # Act
        actual = sp.read_raw(timeout_ms=500, frame_gap_ms=200)

        # Assert
        assert b"\x01\x02" in actual  # contains first chunk
        assert len(actual) >= 4  # both chunks assembled


# -- Wait for Idle -------------------------------------------------------------


class TestWaitForIdle:
    def test_wait_for_idle_returns_when_no_data(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act — should return quickly since no data is arriving
        t0 = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=1.0)
        elapsed = time.monotonic() - t0

        # Assert
        assert elapsed < 0.5  # returned well before max_wait

    def test_wait_for_idle_respects_max_wait(self, port_env):
        # Arrange
        sp, fake, _, _ = port_env
        # Send a command so data keeps coming
        fake.write(b"AT+INFO\r")

        # Act
        t0 = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=0.3)
        elapsed = time.monotonic() - t0

        # Assert
        assert elapsed < 1.0  # bounded by max_wait

    def test_wait_for_idle_closed_port(self, port_env):
        # Arrange
        sp, fake, _, _ = port_env
        fake.close()

        # Act — should return immediately
        t0 = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=1.0)
        elapsed = time.monotonic() - t0

        # Assert
        assert elapsed < 0.1  # returned immediately
