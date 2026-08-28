"""Tests for CaptureEngine - file capture state machine."""

import struct
from pathlib import Path

from termapy.capture import (
    CaptureEngine,
    CaptureProgress,
    CaptureResult,
    format_capture_result,
)

# -- Lifecycle -----------------------------------------------------------------


class TestLifecycle:
    def test_inactive_by_default(self):
        # Arrange
        engine = CaptureEngine()

        # Assert
        assert engine.active is False, "no capture running"
        assert engine.mode == "", "no mode"

    def test_start_text_capture(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"

        # Act
        result = engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is True, "started successfully"
        assert engine.active is True, "capture running"
        assert engine.mode == "text", "text mode"

    def test_start_bin_capture(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"

        # Act
        result = engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=100,
        )

        # Assert
        assert result is True, "started successfully"
        assert engine.mode == "bin", "binary mode"

    def test_start_fails_when_active(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        result = engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is False, "cannot start while active"

    def test_start_fails_bad_path(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        bad_path = tmp_path / "nonexistent" / "deeply" / "nested" / "\0invalid"

        # Act
        result = engine.start(path=bad_path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is False, "cannot open file"

    def test_stop_returns_none_when_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act
        result = engine.stop()

        # Assert
        assert result is None, "nothing to stop"

    def test_stop_returns_result(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        result = engine.stop()

        # Assert
        assert isinstance(result, CaptureResult), "returns result"
        assert result.path == path, "correct path"
        assert result.byte_count == 0, "no data fed"
        assert engine.active is False, "now inactive"

    def test_stop_calls_on_complete(self, tmp_path):
        # Arrange
        results = []
        engine = CaptureEngine(on_complete=results.append)
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        engine.stop()

        # Assert
        assert len(results) == 1, "callback fired"
        assert results[0].path == path, "correct result"


# -- Text capture --------------------------------------------------------------


class TestTextCapture:
    def test_feed_text_writes_lines(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        engine.feed_text(["hello", "world"])
        result = engine.stop()

        # Assert
        actual = path.read_text()
        assert actual == "hello\nworld\n", "lines written with newlines"
        assert isinstance(result, CaptureResult), "stop returns a result"
        assert result.byte_count == 12, "5+1+5+1 = 12"

    def test_feed_text_ignored_when_inactive(self, tmp_path):
        # Arrange
        engine = CaptureEngine()

        # Act - should not raise
        engine.feed_text(["hello"])

    def test_feed_text_ignored_in_bin_mode(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=100)

        # Act
        engine.feed_text(["hello"])
        result = engine.stop()

        # Assert
        assert isinstance(result, CaptureResult), "stop returns a result"
        assert result.byte_count == 0, "text not fed to bin capture"

    def test_append_mode(self, tmp_path):
        # Arrange
        path = tmp_path / "out.txt"
        path.write_text("existing\n")
        engine = CaptureEngine()
        engine.start(path=path, file_mode="a", mode="text", duration=5.0)

        # Act
        engine.feed_text(["new line"])
        engine.stop()

        # Assert
        actual = path.read_text()
        assert actual == "existing\nnew line\n", "appended"


# -- Binary capture (raw) -----------------------------------------------------


class TestBinaryRawCapture:
    def test_feed_bytes_raw(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=10)

        # Act
        engine.feed_bytes(b"\x01\x02\x03\x04\x05")
        engine.feed_bytes(b"\x06\x07\x08\x09\x0a")

        # Assert - target reached, but caller must call stop
        result = engine.stop()
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a", "all bytes written"
        assert isinstance(result, CaptureResult), "stop returns a result"
        assert result.byte_count == 10, "byte count matches"

    def test_feed_bytes_returns_true_at_target(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=5)

        # Act
        result1 = engine.feed_bytes(b"\x01\x02\x03")
        result2 = engine.feed_bytes(b"\x04\x05\x06\x07")

        # Assert
        assert result1 is False, "not yet at target"
        assert result2 is True, "target reached"

    def test_feed_bytes_trims_to_target(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=3)

        # Act
        engine.feed_bytes(b"\x01\x02\x03\x04\x05")
        engine.stop()

        # Assert
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03", "trimmed to target"

    def test_feed_bytes_ignored_when_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act - should not raise
        result = engine.feed_bytes(b"\x01\x02")

        # Assert
        assert result is False, "feed_bytes returns False when inactive"


# -- Binary capture (format spec) ---------------------------------------------


class TestBinaryFormatCapture:
    def _make_columns(self):
        """Create a simple format spec: two unsigned 16-bit big-endian columns."""
        from termapy.protocol import parse_format_spec
        return parse_format_spec("A:U1-2 B:U3-4")

    def test_format_spec_csv(self, tmp_path):
        # Arrange
        columns = self._make_columns()
        engine = CaptureEngine()
        path = tmp_path / "out.csv"
        engine.start(
            path=path, file_mode="w", mode="bin", target_bytes=8,
            columns=columns, record_size=4, sep=",",
        )

        # Act - two 4-byte records
        record1 = struct.pack(">HH", 100, 200)
        record2 = struct.pack(">HH", 300, 400)
        engine.feed_bytes(record1 + record2)
        engine.stop()

        # Assert
        actual_lines = path.read_text().strip().split("\n")
        assert actual_lines[0] == "A,B", "header row"
        assert actual_lines[1] == "100,200", "first record"
        assert actual_lines[2] == "300,400", "second record"

    def test_format_spec_tab_separated(self, tmp_path):
        # Arrange
        columns = self._make_columns()
        engine = CaptureEngine()
        path = tmp_path / "out.tsv"
        engine.start(
            path=path, file_mode="w", mode="bin", target_bytes=4,
            columns=columns, record_size=4, sep="\t",
        )

        # Act
        record = struct.pack(">HH", 42, 99)
        engine.feed_bytes(record)
        engine.stop()

        # Assert
        actual_lines = path.read_text().strip().split("\n")
        assert actual_lines[0] == "A\tB", "tab-separated header"
        assert actual_lines[1] == "42\t99", "tab-separated values"

    def test_echo_callback(self, tmp_path):
        # Arrange
        columns = self._make_columns()
        echoed = []
        engine = CaptureEngine(on_echo=echoed.append)
        path = tmp_path / "out.csv"
        engine.start(
            path=path, file_mode="w", mode="bin", target_bytes=4,
            columns=columns, record_size=4, echo=True,
        )

        # Act
        record = struct.pack(">HH", 1, 2)
        engine.feed_bytes(record)
        engine.stop()

        # Assert
        assert len(echoed) >= 1, "echo callback fired"
        assert "1" in echoed[-1], "contains value"


# -- Hex mode ------------------------------------------------------------------


class TestHexCapture:
    def test_hex_text_to_bytes(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=3, hex_mode=True,
        )

        # Act - feed hex-encoded text lines
        engine.feed_bytes(b"01 02 03\n")
        engine.stop()

        # Assert
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03", "hex decoded"

    def test_hex_partial_lines(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=3, hex_mode=True,
        )

        # Act - partial line, then rest
        engine.feed_bytes(b"01 02")
        engine.feed_bytes(b" 03\n")
        engine.stop()

        # Assert
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03", "reassembled from partial lines"


# -- Progress ------------------------------------------------------------------


class TestProgress:
    def test_progress_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act
        prog = engine.get_progress()

        # Assert
        assert prog is None, "no progress when inactive"

    def test_progress_bin_mode(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=100)
        engine.feed_bytes(b"\x00" * 50)

        # Act
        prog = engine.get_progress()

        # Assert
        assert isinstance(prog, CaptureProgress), "returns CaptureProgress"
        assert prog.mode == "bin", "mode is bin"
        assert prog.target_bytes == 100, "target_bytes matches"
        assert prog.path_name == "out.bin", "path_name matches"

    def test_progress_text_mode(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=10.0)

        # Act
        prog = engine.get_progress()

        # Assert
        assert isinstance(prog, CaptureProgress), "returns CaptureProgress"
        assert prog.mode == "text", "mode is text"
        assert prog.remaining_s > 0, "still counting down"


# -- CaptureResult ------------------------------------------------------------


class TestCaptureResult:
    def test_size_label_bytes(self):
        # Arrange
        result = CaptureResult(path=Path("test.bin"), byte_count=500, raw=True)

        # Assert
        assert result.size_label == "500 B", "size_label uses the shared format_size"

    def test_size_label_kb(self):
        # Arrange
        result = CaptureResult(path=Path("test.bin"), byte_count=2048, raw=True)

        # Assert
        assert result.size_label == "2.0 KB", "size_label shows KB"


class TestFormatCaptureResult:
    """The single owner of the 'Capture complete/aborted' status line
    (shared by the TUI, capture_view, CLI, and MCP frontends)."""

    def test_complete_is_green(self):
        # Arrange
        result = CaptureResult(path=Path("out.csv"), byte_count=2048, raw=False, elapsed_s=3.2)

        # Act
        text, color = format_capture_result(result)

        # Assert
        assert text == "Capture complete: out.csv (2.0 KB in 3.2s)", "size and wall time"
        assert color == "green", "complete is green"

    def test_aborted_is_red(self):
        # Arrange -- a non-empty error marks an aborted capture
        result = CaptureResult(
            path=Path("out.csv"), byte_count=10, raw=False, error="disk full"
        )

        # Act
        text, color = format_capture_result(result)

        # Assert -- abort surfaces the error, not the size
        assert text == "Capture aborted: disk full (out.csv)", "aborted text"
        assert color == "red", "aborted is red"


# -- Write-failure handling (bug 5) --------------------------------------------


class _RaisingFile:
    """Fake file handle whose write() raises OSError, e.g. the capture target
    disk fills up or removable media is yanked mid-capture."""

    def __init__(self) -> None:
        self.closed = False

    def write(self, *_args) -> int:
        raise OSError("simulated write failure")

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class TestWriteFailure:
    def test_text_write_failure_signals_stop_and_records_error(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        engine.start(path=tmp_path / "out.txt", file_mode="w", mode="text", duration=5.0)
        engine._fh = _RaisingFile()  # media vanishes mid-capture

        # Act
        actual_stop = engine.feed_text(["hello"])
        result = engine.stop()

        # Assert
        assert actual_stop is True, "feed_text signals the caller to stop on write error"
        assert result is not None, "stop returns a result"
        assert "simulated write failure" in result.error, "write error surfaced on result"

    def test_bin_write_failure_signals_stop_and_records_error(self, tmp_path):
        # Arrange -- no byte target, so only a write error can signal stop
        engine = CaptureEngine()
        engine.start(path=tmp_path / "out.bin", file_mode="wb", mode="bin", target_bytes=0)
        engine._fh = _RaisingFile()

        # Act -- 4096 bytes triggers the periodic flush, which fails
        actual_stop = engine.feed_bytes(b"\x00" * 4096)
        result = engine.stop()

        # Assert
        assert actual_stop is True, "feed_bytes signals stop so the host releases the claim"
        assert "simulated write failure" in result.error, "write error surfaced on result"

    def test_successful_capture_has_no_error(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        engine.start(path=tmp_path / "ok.txt", file_mode="w", mode="text", duration=5.0)

        # Act
        actual_stop = engine.feed_text(["hello"])
        result = engine.stop()

        # Assert
        assert actual_stop is False, "no stop signal on a clean write"
        assert result.error == "", "no error on a successful capture"


class TestBinaryTargetAboveFlushSize:
    """A byte target larger than the 4 KB flush threshold must still stop.

    ``feed_bytes`` compared the target against ``len(self._buf)`` -- the
    buffer SINCE THE LAST FLUSH -- while ``_flush_bin`` clears that buffer
    every 4096 bytes.  Any target above 4096 was therefore unreachable and
    ``/cap.bin <file> bytes=N`` ran until a duration or a manual /cap.stop
    (docs/review/2026-08-19-v0.74.0-opus-5.md, finding T17).
    """

    def test_target_above_flush_threshold_is_reached(self, tmp_path):
        # Arrange -- 10000 > the 4096 internal flush point.
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        target = 10000
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=target)

        # Act -- feed in 1 KB chunks, as the reader does.
        reached_after = None
        for i in range(1, 21):
            if engine.feed_bytes(b"X" * 1024):
                reached_after = i * 1024
                break

        # Assert
        assert reached_after is not None, (
            f"capture never reported reaching its {target}-byte target after "
            f"20 KB -- the target is compared against the post-flush buffer "
            f"instead of the running total, so any target above 4096 is "
            f"unreachable"
        )
        engine.stop()

    def test_captured_file_is_exactly_the_target_size(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        target = 10000
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=target)

        # Act -- keep feeding past the target; the engine must not over-capture.
        for _ in range(20):
            if engine.feed_bytes(b"Y" * 1024):
                break
        result = engine.stop()

        # Assert
        actual = path.stat().st_size
        assert actual == target, f"file should hold exactly {target} bytes, got {actual}"
        assert result is not None and result.byte_count == target, (
            f"reported byte_count should match the target, got "
            f"{result.byte_count if result else None}"
        )

    def test_target_below_flush_threshold_still_works(self, tmp_path):
        # Arrange -- the case that always worked; guard against regressing it.
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=500)

        # Act
        reached = engine.feed_bytes(b"Z" * 1024)
        engine.stop()

        # Assert
        assert reached is True, "a sub-flush-size target should still be reached"
        actual = path.stat().st_size
        assert actual == 500, f"file should hold exactly 500 bytes, got {actual}"


class TestStopRacesTheReader:
    """``stop()`` closes the file on one thread while the reader still writes.

    ``stop()`` runs ``_fh.close()`` and then ``_reset()``; a reader-thread
    write landing between those raises ``ValueError`` (closed file), and one
    landing after raises ``AttributeError`` (``None``).  Only ``OSError`` was
    caught, so both escaped onto the reader thread.
    """

    def test_text_write_to_a_closed_handle_is_caught(self, tmp_path):
        # Arrange -- the close half of stop(), with the field still set
        engine = CaptureEngine()
        engine.start(path=tmp_path / "out.txt", file_mode="w", mode="text", duration=5.0)
        engine._fh.close()

        # Act -- the reader was already inside feed_text when the close landed
        actual_stop = engine.feed_text(["late line"])
        result = engine.stop()

        # Assert
        assert actual_stop is True, "a write onto a closed file must signal stop"
        assert result is not None and result.error != "", (
            "the failure is reported on the result, not raised at the reader"
        )

    def test_binary_flush_to_a_closed_handle_is_caught(self, tmp_path):
        # Arrange -- no byte target, so only the periodic flush writes
        engine = CaptureEngine()
        engine.start(path=tmp_path / "out.bin", file_mode="wb", mode="bin", target_bytes=0)
        engine._fh.close()

        # Act -- 4096 bytes triggers the flush that now hits a closed file
        actual_stop = engine.feed_bytes(b"\x00" * 4096)
        result = engine.stop()

        # Assert
        assert actual_stop is True, "a failed flush must signal stop"
        assert result is not None and result.error != "", (
            "the failure is reported on the result, not raised at the reader"
        )

    def test_a_late_failure_is_not_charged_to_the_next_capture(self, tmp_path):
        """Ownership by identity, not by timing.

        A straggler write from the *previous* capture can fail after a new
        one has started.  Recording that on the live engine would report a
        failure against a capture that is writing perfectly well.
        """
        # Arrange -- capture A fails and is stopped, capture B starts clean
        engine = CaptureEngine()
        engine.start(path=tmp_path / "a.txt", file_mode="w", mode="text", duration=5.0)
        stale_fh = engine._fh
        stale_fh.close()
        engine.feed_text(["doomed"])
        engine.stop()
        engine.start(path=tmp_path / "b.txt", file_mode="w", mode="text", duration=5.0)

        # Act -- A's straggler reports its failure after B is live
        engine._record_write_error(stale_fh, ValueError("I/O operation on closed file"))
        engine.feed_text(["healthy"])
        result = engine.stop()

        # Assert
        assert result is not None and result.error == "", (
            "a failure from the previous capture's handle must not be "
            f"charged to the current one, got {result.error!r}"
        )
        actual = (tmp_path / "b.txt").read_text(encoding="utf-8")
        assert actual == "healthy\n", f"capture B wrote normally, got {actual!r}"
