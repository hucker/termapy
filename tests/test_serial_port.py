"""Tests for SerialPort and SerialReader."""

import queue
import time

import pytest

from termapy.demo import FakeSerial
from termapy.serial_port import SerialPort, SerialReader


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


# -- SerialReader --------------------------------------------------------------


class TestSerialReaderLines:
    def test_complete_line(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello world\r\n")

        # Assert
        assert result.lines == ["hello world"]  # complete line extracted

    def test_multiple_lines(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"line1\r\nline2\r\nline3\r\n")

        # Assert
        assert result.lines == ["line1", "line2", "line3"]  # all lines

    def test_partial_line_buffered(self):
        # Arrange
        reader = SerialReader()

        # Act
        result1 = reader.process(b"hello ")
        result2 = reader.process(b"world\r\n")

        # Assert
        assert result1.lines == []  # no newline yet
        assert result2.lines == ["hello world"]  # assembled

    def test_empty_lines_skipped(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"\r\n\r\nhello\r\n\r\n")

        # Assert
        assert result.lines == ["hello"]  # blanks skipped

    def test_cr_stripped(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"]  # \r stripped


class TestSerialReaderIdleFlush:
    def test_flush_partial_after_silence(self):
        # Arrange
        reader = SerialReader()
        reader.process(b"partial")

        # Simulate 200ms+ of silence
        reader._last_rx = time.monotonic() - 0.3

        # Act
        result = reader.process(b"")

        # Assert
        assert result.lines == ["partial"]  # flushed

    def test_no_flush_during_ansi_escape(self):
        # Arrange
        reader = SerialReader()
        reader.process(b"text\x1b[")  # incomplete ANSI escape

        # Simulate silence
        reader._last_rx = time.monotonic() - 0.3

        # Act
        result = reader.process(b"")

        # Assert
        assert result.lines == []  # not flushed — waiting for escape to complete


class TestSerialReaderClearScreen:
    def test_clear_screen_detected(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"\x1b[2Jhello\r\n")

        # Assert
        assert result.clear_screen is True  # detected
        assert result.lines == ["hello"]  # text after clear

    def test_clear_screen_with_cursor_home(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"\x1b[H\x1b[2Jhello\r\n")

        # Assert
        assert result.clear_screen is True  # detected with home prefix


class TestSerialReaderEOLMarkers:
    def test_eol_markers_inserted(self):
        # Arrange
        reader = SerialReader(show_line_endings=True)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert len(result.lines) == 1
        assert "\\r" in result.lines[0]  # visible CR marker present

    def test_no_markers_by_default(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert "\\r" not in result.lines[0]  # no markers


class TestSerialReaderCapture:
    def test_binary_capture_consumes_data(self):
        # Arrange — mock capture engine
        class MockCapture:
            active = True
            mode = "bin"
            fed = []
            def feed_bytes(self, data):
                self.fed.append(data)
                return False

        cap = MockCapture()
        reader = SerialReader(capture=cap)

        # Act
        result = reader.process(b"\x01\x02\x03")

        # Assert
        assert result.lines == []  # no display output
        assert cap.fed == [b"\x01\x02\x03"]  # data went to capture

    def test_capture_target_reached(self):
        # Arrange
        class MockCapture:
            active = True
            mode = "bin"
            def feed_bytes(self, data):
                return True  # target reached

        reader = SerialReader(capture=MockCapture())

        # Act
        result = reader.process(b"\x01\x02")

        # Assert
        assert result.capture_target_reached is True

    def test_text_capture_not_consumed(self):
        # Arrange — text mode capture doesn't intercept in reader
        class MockCapture:
            active = True
            mode = "text"

        reader = SerialReader(capture=MockCapture())

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"]  # passed through to display


class TestSerialReaderProtoActive:
    def test_display_suppressed(self):
        # Arrange
        reader = SerialReader(proto_active=lambda: True)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == []  # suppressed

    def test_display_not_suppressed(self):
        # Arrange
        reader = SerialReader(proto_active=lambda: False)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"]  # normal


class TestSerialReaderReset:
    def test_reset_clears_buffer(self):
        # Arrange
        reader = SerialReader()
        reader.process(b"partial")

        # Act
        reader.reset()
        result = reader.process(b"new\r\n")

        # Assert
        assert result.lines == ["new"]  # no leftover from before reset
