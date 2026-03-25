"""Tests for CaptureEngine — file capture state machine."""

import struct
from pathlib import Path

import pytest

from termapy.capture import CaptureEngine, CaptureProgress, CaptureResult


# -- Lifecycle -----------------------------------------------------------------


class TestLifecycle:
    def test_inactive_by_default(self):
        # Arrange
        engine = CaptureEngine()

        # Assert
        assert engine.active is False  # no capture running
        assert engine.mode == ""  # no mode

    def test_start_text_capture(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"

        # Act
        result = engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is True  # started successfully
        assert engine.active is True  # capture running
        assert engine.mode == "text"  # text mode

    def test_start_bin_capture(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"

        # Act
        result = engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=100,
        )

        # Assert
        assert result is True  # started successfully
        assert engine.mode == "bin"  # binary mode

    def test_start_fails_when_active(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        result = engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is False  # cannot start while active

    def test_start_fails_bad_path(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        bad_path = tmp_path / "nonexistent" / "deeply" / "nested" / "\0invalid"

        # Act
        result = engine.start(path=bad_path, file_mode="w", mode="text", duration=5.0)

        # Assert
        assert result is False  # cannot open file

    def test_stop_returns_none_when_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act
        result = engine.stop()

        # Assert
        assert result is None  # nothing to stop

    def test_stop_returns_result(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        result = engine.stop()

        # Assert
        assert isinstance(result, CaptureResult)  # returns result
        assert result.path == path  # correct path
        assert result.byte_count == 0  # no data fed
        assert engine.active is False  # now inactive

    def test_stop_calls_on_complete(self, tmp_path):
        # Arrange
        results = []
        engine = CaptureEngine(on_complete=results.append)
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=5.0)

        # Act
        engine.stop()

        # Assert
        assert len(results) == 1  # callback fired
        assert results[0].path == path  # correct result


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
        assert actual == "hello\nworld\n"  # lines written with newlines
        assert result.byte_count == 12  # 5+1+5+1 = 12

    def test_feed_text_ignored_when_inactive(self, tmp_path):
        # Arrange
        engine = CaptureEngine()

        # Act — should not raise
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
        assert result.byte_count == 0  # text not fed to bin capture

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
        assert actual == "existing\nnew line\n"  # appended


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

        # Assert — target reached, but caller must call stop
        result = engine.stop()
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a"
        assert result.byte_count == 10

    def test_feed_bytes_returns_true_at_target(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=5)

        # Act
        result1 = engine.feed_bytes(b"\x01\x02\x03")
        result2 = engine.feed_bytes(b"\x04\x05\x06\x07")

        # Assert
        assert result1 is False  # not yet at target
        assert result2 is True  # target reached

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
        assert actual == b"\x01\x02\x03"  # trimmed to target

    def test_feed_bytes_ignored_when_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act — should not raise
        result = engine.feed_bytes(b"\x01\x02")

        # Assert
        assert result is False


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

        # Act — two 4-byte records
        record1 = struct.pack(">HH", 100, 200)
        record2 = struct.pack(">HH", 300, 400)
        engine.feed_bytes(record1 + record2)
        engine.stop()

        # Assert
        actual_lines = path.read_text().strip().split("\n")
        assert actual_lines[0] == "A,B"  # header row
        assert actual_lines[1] == "100,200"  # first record
        assert actual_lines[2] == "300,400"  # second record

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
        assert actual_lines[0] == "A\tB"  # tab-separated header
        assert actual_lines[1] == "42\t99"  # tab-separated values

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
        assert len(echoed) >= 1  # echo callback fired
        assert "1" in echoed[-1]  # contains value


# -- Hex mode ------------------------------------------------------------------


class TestHexCapture:
    def test_hex_text_to_bytes(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=3, hex_mode=True,
        )

        # Act — feed hex-encoded text lines
        engine.feed_bytes(b"01 02 03\n")
        engine.stop()

        # Assert
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03"  # hex decoded

    def test_hex_partial_lines(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(
            path=path, file_mode="wb", mode="bin", target_bytes=3, hex_mode=True,
        )

        # Act — partial line, then rest
        engine.feed_bytes(b"01 02")
        engine.feed_bytes(b" 03\n")
        engine.stop()

        # Assert
        actual = path.read_bytes()
        assert actual == b"\x01\x02\x03"  # reassembled from partial lines


# -- Progress ------------------------------------------------------------------


class TestProgress:
    def test_progress_inactive(self):
        # Arrange
        engine = CaptureEngine()

        # Act
        prog = engine.get_progress()

        # Assert
        assert prog is None  # no progress when inactive

    def test_progress_bin_mode(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.bin"
        engine.start(path=path, file_mode="wb", mode="bin", target_bytes=100)
        engine.feed_bytes(b"\x00" * 50)

        # Act
        prog = engine.get_progress()

        # Assert
        assert isinstance(prog, CaptureProgress)
        assert prog.mode == "bin"
        assert prog.target_bytes == 100
        assert prog.path_name == "out.bin"

    def test_progress_text_mode(self, tmp_path):
        # Arrange
        engine = CaptureEngine()
        path = tmp_path / "out.txt"
        engine.start(path=path, file_mode="w", mode="text", duration=10.0)

        # Act
        prog = engine.get_progress()

        # Assert
        assert isinstance(prog, CaptureProgress)
        assert prog.mode == "text"
        assert prog.remaining_s > 0  # still counting down


# -- CaptureResult ------------------------------------------------------------


class TestCaptureResult:
    def test_size_label_bytes(self):
        # Arrange
        result = CaptureResult(path=Path("test.bin"), byte_count=500, raw=True)

        # Assert
        assert result.size_label == "500 bytes"

    def test_size_label_kb(self):
        # Arrange
        result = CaptureResult(path=Path("test.bin"), byte_count=2048, raw=True)

        # Assert
        assert result.size_label == "2.0 KB"
