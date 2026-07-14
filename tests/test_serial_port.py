"""Tests for SerialPort and SerialReader."""

import queue
import time

import pytest

from termapy.demo import FakeSerial
from termapy.serial_port import SerialPort, SerialReader, split_rx_lines


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
        assert sp.is_open is True, "FakeSerial starts open"

    def test_is_open_after_close(self, port_env):
        sp, fake, _, _ = port_env

        # Act
        fake.close()

        # Assert
        assert sp.is_open is False, "reflects closed state"

    def test_port_property(self, port_env):
        sp, fake, _, _ = port_env

        # Assert
        assert sp.port is fake, "returns the underlying port"


# -- Write ---------------------------------------------------------------------


class TestWrite:
    def test_write_sends_data(self, port_env):
        # Arrange
        sp, fake, _, _ = port_env

        # Act - FakeSerial enqueues the reply synchronously on write, and
        # fake.read blocks up to its own timeout for data, so no sleep needed.
        sp.write(b"AT\r")

        # Assert - read back from FakeSerial's response
        data = fake.read(1024)
        assert b"OK" in data, "FakeSerial responded to AT command"

    def test_write_logs_tx(self, port_env):
        # Arrange
        sp, _, _, logged = port_env

        # Act
        sp.write(b"ATZ\r")

        # Assert
        assert len(logged) >= 1, "at least one log entry"
        assert logged[0][0] == ">", "TX direction"
        assert "ATZ" in logged[0][1], "command logged"

    def test_write_logs_hex_for_binary(self, port_env):
        # Arrange
        sp, _, _, logged = port_env

        # Act
        sp.write(b"\x01\x02\xff")

        # Assert
        assert logged[0][0] == ">", "TX direction"
        assert "01 02 ff" in logged[0][1], "hex representation"


# -- Drain ---------------------------------------------------------------------


class TestDrain:
    def test_drain_empty_queue(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act
        actual = sp.drain()

        # Assert
        assert actual == 0, "nothing to drain"

    def test_drain_returns_byte_count(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        rx_queue.put(b"\x01\x02\x03")
        rx_queue.put(b"\x04\x05")

        # Act
        actual = sp.drain()

        # Assert
        assert actual == 5, "3 + 2 bytes drained"
        assert rx_queue.empty(), "queue is empty"


# -- Read Raw ------------------------------------------------------------------


class TestReadRaw:
    def test_read_raw_returns_data(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        rx_queue.put(b"\x01\x02\x03")

        # Act
        actual = sp.read_raw(timeout_ms=500, frame_gap_ms=50)

        # Assert
        assert actual == b"\x01\x02\x03", "data returned"

    def test_read_raw_timeout_returns_empty(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act
        actual = sp.read_raw(timeout_ms=100, frame_gap_ms=50)

        # Assert
        assert actual == b"", "timed out, no data"

    def test_read_raw_assembles_chunks(self, port_env):
        # Arrange
        sp, _, rx_queue, _ = port_env
        # Put two chunks close together - should assemble into one frame
        rx_queue.put(b"\x01\x02")
        rx_queue.put(b"\x03\x04")

        # Act
        actual = sp.read_raw(timeout_ms=500, frame_gap_ms=200)

        # Assert
        assert b"\x01\x02" in actual, "contains first chunk"
        assert len(actual) >= 4, "both chunks assembled"


# -- Wait for Idle -------------------------------------------------------------


class TestWaitForIdle:
    def test_wait_for_idle_returns_when_no_data(self, port_env):
        # Arrange
        sp, _, _, _ = port_env

        # Act - should return quickly since no data is arriving
        t0 = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=1.0)
        elapsed = time.monotonic() - t0

        # Assert
        assert elapsed < 0.5, "returned well before max_wait"

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
        assert elapsed < 1.0, "bounded by max_wait"

    def test_wait_for_idle_closed_port(self, port_env):
        # Arrange
        sp, fake, _, _ = port_env
        fake.close()

        # Act - should return immediately
        t0 = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=1.0)
        elapsed = time.monotonic() - t0

        # Assert
        assert elapsed < 0.1, "returned immediately"


# -- SerialReader --------------------------------------------------------------


class TestSerialReaderLines:
    def test_complete_line(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello world\r\n")

        # Assert
        assert result.lines == ["hello world"], "complete line extracted"

    def test_multiple_lines(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"line1\r\nline2\r\nline3\r\n")

        # Assert
        assert result.lines == ["line1", "line2", "line3"], "all lines"

    def test_partial_line_buffered(self):
        # Arrange
        reader = SerialReader()

        # Act
        result1 = reader.process(b"hello ")
        result2 = reader.process(b"world\r\n")

        # Assert
        assert result1.lines == [], "no newline yet"
        assert result2.lines == ["hello world"], "assembled"

    def test_blank_lines_preserved(self):
        # Arrange -- blank lines are now kept (TeraTerm-style display
        # fidelity), not dropped; each CRLF is a single break.
        reader = SerialReader()

        # Act
        result = reader.process(b"\r\n\r\nhello\r\n\r\n")

        # Assert
        actual = result.lines
        expected = ["", "", "hello", ""]
        assert actual == expected, "blank lines preserved, CRLF is one break"

    def test_cr_stripped(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"], "\\r stripped"


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
        assert result.lines == ["partial"], "flushed"

    def test_no_flush_during_ansi_escape(self):
        # Arrange
        reader = SerialReader()
        reader.process(b"text\x1b[")  # incomplete ANSI escape

        # Simulate silence
        reader._last_rx = time.monotonic() - 0.3

        # Act
        result = reader.process(b"")

        # Assert
        assert result.lines == [], "not flushed - waiting for escape to complete"


class TestSerialReaderUtf8Split:
    """A multi-byte char split across reads decodes once, not as two U+FFFD."""

    def test_two_byte_char_split_across_reads(self):
        # Arrange -- 'é' is 0xC3 0xA9; split between the two reads.
        reader = SerialReader()

        # Act
        result1 = reader.process(b"caf\xc3")
        result2 = reader.process(b"\xa9\r\n")

        # Assert
        actual = result2.lines
        expected = ["café"]
        assert actual == expected, "split 2-byte char reassembled as 'café'"
        assert result1.lines == [], "no line until newline arrives"

    def test_four_byte_emoji_split_mid_stream(self):
        # Arrange -- U+1F600 is 0xF0 0x9F 0x98 0x80; split inside the char,
        # with more text after it on the same line.
        reader = SerialReader()

        # Act
        reader.process(b"hi \xf0\x9f")
        result = reader.process(b"\x98\x80 there\r\n")

        # Assert
        actual = result.lines
        expected = ["hi \U0001f600 there"]
        assert actual == expected, "split 4-byte emoji reassembled in place"

    def test_split_emits_no_replacement_char(self):
        # Arrange
        reader = SerialReader()

        # Act
        reader.process(b"\xe2\x9c")  # first 2 bytes of '✓' (U+2713)
        result = reader.process(b"\x93\r\n")  # final byte + EOL

        # Assert
        actual = result.lines
        assert actual == ["✓"], "checkmark decoded whole"
        assert "�" not in actual[0], "no replacement char from the split"

    def test_truncated_multibyte_surfaces_on_idle(self):
        # Arrange -- a dangling lead byte that never completes, then silence.
        reader = SerialReader()
        reader.process(b"caf\xc3")  # 0xC3 is held pending (incomplete 'é')
        reader._last_rx = time.monotonic() - 0.3  # simulate 200ms+ silence

        # Act
        result = reader.process(b"")

        # Assert -- the dangling byte flushes as U+FFFD instead of lingering.
        actual = result.lines
        expected = ["caf�"]
        assert actual == expected, "truncated tail surfaces as replacement char on idle"


class TestSerialReaderClearScreen:
    def test_clear_screen_detected(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"\x1b[2Jhello\r\n")

        # Assert
        assert result.clear_screen is True, "detected"
        assert result.lines == ["hello"], "text after clear"

    def test_clear_screen_with_cursor_home(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"\x1b[H\x1b[2Jhello\r\n")

        # Assert
        assert result.clear_screen is True, "detected with home prefix"


class TestSerialReaderEOLMarkers:
    def test_eol_markers_inserted(self):
        # Arrange
        reader = SerialReader(show_line_endings=True)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert len(result.lines) == 1, "single line returned"
        assert "\\r" in result.lines[0], "visible CR marker present"

    def test_no_markers_by_default(self):
        # Arrange
        reader = SerialReader()

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert "\\r" not in result.lines[0], "no markers"


class TestRxNewline:
    """Receive-newline handling: split_rx_lines + SerialReader modes."""

    # -- pure split function --

    def test_auto_splits_lf(self):
        pairs, rem = split_rx_lines("a\nb\n", "auto")
        assert pairs == [("a", "lf"), ("b", "lf")], "LF terminates in auto"
        assert rem == "", "no remainder"

    def test_auto_crlf_is_one_break(self):
        pairs, rem = split_rx_lines("a\r\nb\r\n", "auto")
        assert pairs == [("a", "crlf"), ("b", "crlf")], "CRLF is a single break"
        assert rem == "", "no remainder"

    def test_auto_bare_cr_splits(self):
        pairs, rem = split_rx_lines("a\rb\rc", "auto")
        assert pairs == [("a", "cr"), ("b", "cr")], "bare CR terminates each line"
        assert rem == "c", "unterminated tail held"

    def test_auto_defers_trailing_cr(self):
        pairs, rem = split_rx_lines("foo\r", "auto")
        assert pairs == [], "trailing CR not emitted yet (may be CRLF)"
        assert rem == "foo\r", "trailing CR deferred to next read"

    def test_lf_mode_ignores_cr(self):
        pairs, rem = split_rx_lines("a\rb\n", "lf")
        assert pairs == [("a\rb", "lf")], "only LF breaks; CR is data"
        assert rem == "", "no remainder"

    def test_cr_mode_ignores_lf(self):
        pairs, rem = split_rx_lines("a\nb\r", "cr")
        assert pairs == [("a\nb", "cr")], "only CR breaks; LF is data"
        assert rem == "", "no remainder"

    def test_crlf_mode_only_pairs(self):
        pairs, rem = split_rx_lines("a\rb\nc\r\n", "crlf")
        assert pairs == [("a\rb\nc", "crlf")], "only CRLF pair breaks; lone CR/LF are data"
        assert rem == "", "no remainder"

    # -- integration via SerialReader --

    def test_chunk_split_crlf_no_blank(self):
        # Arrange - a CRLF split across two reads must not emit a blank line.
        reader = SerialReader()  # auto

        # Act
        first = reader.process(b"foo\r")
        second = reader.process(b"\nbar\r\n")

        # Assert
        assert first.lines == [], "trailing CR held, nothing emitted yet"
        assert second.lines == ["foo", "bar"], "CRLF across reads is one break, no blank"

    def test_bare_cr_progress_lines(self):
        # Arrange - a bare-CR device yields one line per update instead of a
        # single merged garbled line (the pre-rx_newline bug).
        reader = SerialReader()  # auto

        # Act
        result = reader.process(b"25%\r50%\r")

        # Assert
        assert result.lines == ["25%"], "completed CR-terminated line emitted; last held"

    def test_lf_mode_strips_cr(self):
        # Arrange - forced LF mode strips a stray CR (historical behavior).
        reader = SerialReader(rx_newline="lf")

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"], "CR stripped in lf mode"

    def test_show_line_endings_crlf_single_line(self):
        # Arrange - markers must not break CRLF detection (the wrinkle).
        reader = SerialReader(show_line_endings=True)  # auto

        # Act
        result = reader.process(b"foo\r\nbar\r\n")

        # Assert
        assert len(result.lines) == 2, "CRLF stays one break even with markers"
        first = result.lines[0]
        assert "\\r" in first and "\\n" in first, "both CR and LF markers on the line"

    def test_unknown_mode_falls_back_to_auto(self):
        # Arrange / Act
        reader = SerialReader(rx_newline="bogus")

        # Assert
        assert reader.rx_newline == "auto", "unknown mode falls back to auto at init"
        reader.rx_newline = "xyz"
        assert reader.rx_newline == "auto", "setter validates too"

    def test_idle_flush_resolves_deferred_cr(self):
        # Arrange - a held trailing CR flushes as a CR-terminated line on idle.
        reader = SerialReader()
        reader.process(b"partial\r")  # trailing CR deferred
        reader._last_rx -= 1.0  # force the idle window open

        # Act
        result = reader.process(b"")

        # Assert
        assert result.lines == ["partial"], "deferred CR flushed as a complete line on idle"


class TestSerialReaderCapture:
    def test_binary_capture_consumes_data(self):
        # Arrange - mock capture engine
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
        assert result.lines == [], "no display output"
        assert cap.fed == [b"\x01\x02\x03"], "data went to capture"

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
        assert result.capture_target_reached is True, "capture target reached"

    def test_text_capture_not_consumed(self):
        # Arrange - text mode capture doesn't intercept in reader
        class MockCapture:
            active = True
            mode = "text"

        reader = SerialReader(capture=MockCapture())

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"], "passed through to display"


class TestSerialReaderClaimed:
    def test_display_suppressed_when_claimed(self):
        # Arrange
        reader = SerialReader(serial_claimed=lambda: True)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == [], "display suppressed while serial claimed"

    def test_display_not_suppressed_when_not_claimed(self):
        # Arrange
        reader = SerialReader(serial_claimed=lambda: False)

        # Act
        result = reader.process(b"hello\r\n")

        # Assert
        assert result.lines == ["hello"], "display normal when not claimed"


class TestSerialReaderReset:
    def test_reset_clears_buffer(self):
        # Arrange
        reader = SerialReader()
        reader.process(b"partial")

        # Act
        reader.reset()
        result = reader.process(b"new\r\n")

        # Assert
        assert result.lines == ["new"], "no leftover from before reset"
