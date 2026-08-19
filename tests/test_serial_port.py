"""Tests for SerialPort and SerialReader."""

import queue
import threading
import time

import pytest

from termapy.demo import FakeSerial
from termapy.serial_port import (
    SerialPort,
    SerialReader,
    apply_backspace,
    split_rx_lines,
)


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

    def test_drain_purges_the_driver_buffer_too(self):
        """Emptying the queue alone leaves the driver's backlog to arrive later.

        The reader thread only moves bytes into the queue when it runs, so
        anything parked in the driver survives a queue-only drain and lands in
        the caller's next reply.  Measured at ~128 KB of stale bytes on real
        hardware before this purge existed
        (docs/review/2026-08-19-v0.74.0-opus-5.md).
        """
        # Arrange -- a port that records whether it was purged.
        class RecordingPort:
            is_open = True

            def __init__(self):
                self.purged = 0

            def write(self, data):
                pass

            def reset_input_buffer(self):
                self.purged += 1

        port = RecordingPort()
        rx_queue: queue.Queue[bytes] = queue.Queue()
        rx_queue.put(b"stale")
        sp = SerialPort(port=port, rx_queue=rx_queue)

        # Act
        actual = sp.drain()

        # Assert
        assert actual == 5, "queue bytes are still counted"
        assert port.purged == 1, (
            "drain must purge the driver buffer, or pre-drain bytes arrive "
            "afterwards and corrupt the next request/response reply"
        )

    def test_drain_tolerates_a_port_that_cannot_purge(self):
        # Arrange -- duck-typed ports need not implement the full pyserial API.
        class MinimalPort:
            is_open = True

            def write(self, data):
                pass

        rx_queue: queue.Queue[bytes] = queue.Queue()
        rx_queue.put(b"ab")
        sp = SerialPort(port=MinimalPort(), rx_queue=rx_queue)

        # Act / Assert -- must not raise.
        actual = sp.drain()
        assert actual == 2, "queue still drains when the port cannot purge"

    def test_drain_clears_pending_demo_output(self):
        # Arrange -- FakeSerial implements the purge, so the demo device
        # behaves like a real one rather than replaying pre-drain output.
        fake = FakeSerial()
        fake.write(b"AT\r")
        assert fake.in_waiting > 0, "demo device should have queued a reply"
        sp = SerialPort(port=fake, rx_queue=queue.Queue())

        # Act
        sp.drain()

        # Assert
        actual = fake.in_waiting
        assert actual == 0, "drain should discard the demo device's pending bytes"


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


def _drain_device(dev: FakeSerial, cmd: bytes) -> bytes:
    """Send an ASCII command to a FakeSerial and return the raw response."""
    dev.write(cmd + b"\r")
    time.sleep(0.01)
    out = b""
    while True:
        chunk = dev.read(4096)
        if not chunk:
            break
        out += chunk
    return out


class TestRxNewlineEndToEnd:
    """Real path: drive the demo device (AT+EOL) through SerialReader.

    Complements TestRxNewline (which unit-tests the streaming edge cases a
    real device can't produce -- chunk splits, deferred CR).  Here the demo
    device actually emits bare-CR / LF-only output and we assert the reader
    turns it into the right lines end to end.
    """

    def test_demo_bare_cr_splits_under_auto(self):
        # Arrange - switch the demo device to bare-CR line endings.
        dev = FakeSerial()
        _drain_device(dev, b"AT+EOL=cr")
        info = _drain_device(dev, b"AT+INFO")
        assert b"\n" not in info and info.count(b"\r") == 3, (
            "demo emitted a 3-line response terminated by bare CR"
        )

        # Act - the default (auto) reader consumes the bare-CR stream.  The
        # response ends in a lone CR, which auto defers (it could be the CR
        # of a CRLF), so the final line surfaces on the idle flush -- mirror
        # what the read loop does when the device goes quiet.
        reader = SerialReader()
        result = reader.process(info)
        reader._last_rx -= 1.0  # open the idle window
        flushed = reader.process(b"")
        lines = result.lines + flushed.lines

        # Assert - split into 3 clean lines (the old LF-only reader merged
        # these into one garbled line).
        assert len(lines) == 3, "bare-CR multi-line splits correctly under auto"
        assert all("\r" not in line and "\n" not in line for line in lines), (
            "no stray terminators left in the split lines"
        )

    def test_demo_lf_only_splits_under_auto(self):
        # Arrange - device emits LF-only.
        dev = FakeSerial()
        _drain_device(dev, b"AT+EOL=lf")
        info = _drain_device(dev, b"AT+INFO")
        assert b"\r" not in info, "demo emitted LF-only output"

        # Act
        reader = SerialReader()
        result = reader.process(info)

        # Assert
        assert len(result.lines) == 3, "LF-only multi-line splits under auto too"

    def test_demo_bare_cr_stalls_in_lf_mode(self):
        # Arrange - device speaks bare CR, but the reader is forced to lf.
        dev = FakeSerial()
        _drain_device(dev, b"AT+EOL=cr")
        info = _drain_device(dev, b"AT+INFO")

        # Act - lf mode finds no LF, so nothing splits (held for the idle
        # flush).  This is exactly the failure AUTO fixes and why AUTO is
        # the default.
        reader = SerialReader(rx_newline="lf")
        result = reader.process(info)

        # Assert
        assert result.lines == [], "a bare-CR stream yields no lines in lf mode"


class TestBackspace:
    """apply_backspace resolves \\b / DEL; SerialReader interprets them."""

    def test_backspace_erases_previous_char(self):
        assert apply_backspace("abc\bX") == "abX", "\\b erases the char before it"

    def test_multiple_backspaces(self):
        assert apply_backspace("abc\b\b") == "a", "two \\b erase two chars"

    def test_del_erases_like_backspace(self):
        assert apply_backspace("ab\x7f") == "a", "DEL (0x7f) erases like backspace"

    def test_backspace_at_line_start_dropped(self):
        # A \b must not eat the preceding line terminator.
        assert apply_backspace("line1\n\bX") == "line1\nX", "\\b does not cross \\n"

    def test_no_control_is_identity(self):
        assert apply_backspace("plain text") == "plain text", "no-op without \\b/DEL"

    def test_reader_resolves_backspace_in_line(self):
        # Arrange - a device rewrites a progress readout in place.
        reader = SerialReader()  # auto

        # Act
        result = reader.process(b"25%\b\b\b100%\n")

        # Assert
        assert result.lines == ["100%"], "backspaces erase 25% before 100% is written"


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


class TestWaitForIdleUsesTheReaderClock:
    """``in_waiting`` is not a silence signal while a reader is draining it.

    The background reader consumes the port continuously, so between two
    lines of a streaming response ``in_waiting`` reads 0 -- and the old
    implementation counted that as silence and returned mid-response.
    Callers sequencing commands on it then talked over the device
    (docs/review/2026-08-19-v0.74.0-opus-5.md, finding T10).
    """

    def test_does_not_report_idle_while_data_is_still_arriving(self):
        # Arrange -- a port whose in_waiting is ALWAYS 0 (a reader just drained
        # it) while the device is in fact still streaming: the rx clock keeps
        # advancing.
        class DrainedPort:
            is_open = True
            in_waiting = 0

            def write(self, data):
                pass

        now = [time.monotonic()]
        sp = SerialPort(
            port=DrainedPort(),
            rx_queue=queue.Queue(),
            last_rx=lambda: now[0],
        )

        def keep_streaming():
            # Device still talking for ~0.3s, then goes quiet.
            for _ in range(30):
                now[0] = time.monotonic()
                time.sleep(0.01)

        streamer = threading.Thread(target=keep_streaming, daemon=True)
        streamer.start()

        # Act
        started = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=3.0)
        elapsed = time.monotonic() - started
        streamer.join(2)

        # Assert -- must outlast the stream plus the silence gap, not return
        # immediately on a drained buffer.
        assert elapsed >= 0.3, (
            f"returned after {elapsed:.3f}s while the device was still "
            f"streaming; wait_for_idle must key off the reader's last-RX "
            f"clock, not the drained in_waiting"
        )

    def test_waits_a_full_gap_even_when_the_reply_has_not_started(self):
        """A stale clock must not be mistaken for silence.

        The engine's last-RX stamp still holds the PREVIOUS response's time
        when a command has been sent but the device has not begun answering.
        Returning then makes the caller talk over the reply it is waiting
        for -- the CLI gold file caught exactly that interleaving.
        """
        # Arrange -- clock is 5s stale; no reply has begun.
        class DrainedPort:
            is_open = True
            in_waiting = 0

            def write(self, data):
                pass

        stale = time.monotonic() - 5.0
        sp = SerialPort(
            port=DrainedPort(), rx_queue=queue.Queue(), last_rx=lambda: stale
        )

        # Act
        started = time.monotonic()
        sp.wait_for_idle(timeout_ms=150, max_wait_s=3.0)
        elapsed = time.monotonic() - started

        # Assert -- one full quiet gap measured from the CALL, not from the
        # stale stamp.
        assert elapsed >= 0.15, (
            f"returned after {elapsed:.3f}s on a stale clock; wait_for_idle "
            f"must floor the idle window at the call time so a reply that has "
            f"not started yet is not read as silence"
        )

    def test_returns_once_the_device_goes_quiet(self):
        # Arrange -- rx clock frozen in the past: already idle.
        class DrainedPort:
            is_open = True
            in_waiting = 0

            def write(self, data):
                pass

        stale = time.monotonic() - 1.0
        sp = SerialPort(
            port=DrainedPort(), rx_queue=queue.Queue(), last_rx=lambda: stale
        )

        # Act
        started = time.monotonic()
        sp.wait_for_idle(timeout_ms=100, max_wait_s=3.0)
        elapsed = time.monotonic() - started

        # Assert -- returns promptly rather than burning max_wait_s.
        assert 0.1 <= elapsed < 0.6, (
            f"should return one gap after the call once quiet, took {elapsed:.3f}s"
        )

    def test_without_a_reader_the_port_buffer_is_still_used(self):
        # Arrange -- no last_rx provider means nothing is draining the port,
        # so in_waiting is the honest signal and must still work.
        fake = FakeSerial()
        sp = SerialPort(port=fake, rx_queue=queue.Queue())

        # Act
        started = time.monotonic()
        sp.wait_for_idle(timeout_ms=50, max_wait_s=1.0)
        elapsed = time.monotonic() - started

        # Assert
        assert elapsed < 1.0, "an idle port should return before max_wait_s"
