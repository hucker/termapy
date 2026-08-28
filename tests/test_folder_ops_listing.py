"""Tests for the shared listing producers in ``folder_ops``.

``list_entries`` / ``file_columns`` / ``format_file_lines`` / ``file_record``
feed every ``/x.list`` command, the TUI file pickers, and the MCP capture
records, so their order and shape are pinned here once rather than per
consumer.
"""

from __future__ import annotations

import os
import time

from termapy.folder_ops import (
    file_columns,
    file_record,
    format_file_lines,
    list_entries,
)


def _make(folder, name: str, payload: bytes, age_s: float = 0.0):
    """Write ``name`` into ``folder`` and back-date its mtime by ``age_s``."""
    path = folder / name
    path.write_bytes(payload)
    if age_s:
        stamp = time.time() - age_s
        os.utime(path, (stamp, stamp))
    return path


class TestListEntries:
    def test_newest_first_then_name_and_no_dotfiles(self, tmp_path):
        # Arrange -- name order is b, a; mtime order puts a first.
        _make(tmp_path, "b.run", b"x", age_s=3600)
        _make(tmp_path, "a.run", b"x")
        _make(tmp_path, ".cmd_history.txt", b"x")
        (tmp_path / "sub").mkdir()

        # Act
        actual = [path.name for path in list_entries(tmp_path, "*")]

        # Assert
        assert actual == ["a.run", "b.run"], "newest first; dotfiles and folders excluded"

    def test_missing_folder_is_empty(self, tmp_path):
        assert list_entries(tmp_path / "nope", "*") == []


class TestFileColumns:
    def test_columns_are_padded_to_the_batch(self, tmp_path):
        # Arrange
        short = _make(tmp_path, "a.run", b"x" * 10)
        long = _make(tmp_path, "longer_name.run", b"x" * 2048, age_s=3600)

        # Act
        rows = file_columns([long, short])

        # Assert
        assert rows[0].name == "longer_name.run" and rows[1].name == "a.run          ", (
            "names padded to the widest name"
        )
        assert rows[0].size == "2.0 KB" and rows[1].size == "  10 B", (
            "sizes right-aligned to the widest size"
        )
        assert rows[0].age == "1 hr ago" and rows[1].age == "just now"

    def test_vanished_file_renders_question_marks(self, tmp_path):
        # Arrange -- a path that was globbed but no longer exists
        ghost = tmp_path / "gone.run"

        # Act
        actual = format_file_lines([ghost])

        # Assert
        assert actual == ["gone.run  ?  ?"], "a stat failure degrades one row, not the listing"

    def test_format_file_lines_joins_with_two_spaces(self, tmp_path):
        path = _make(tmp_path, "a.run", b"x" * 10)
        assert format_file_lines([path]) == ["a.run  10 B  just now"]


class TestFileRecord:
    def test_shape_and_numeric_fields(self, tmp_path):
        # Arrange
        path = _make(tmp_path, "cap.bin", b"x" * 300, age_s=120)
        now = time.time()

        # Act
        actual = file_record(path, now=now)

        # Assert
        assert set(actual) == {"name", "bytes", "mtime", "age_s"}
        assert actual["bytes"] == 300, "raw byte count, never a humanized string"
        assert 119 <= actual["age_s"] <= 122, "age in seconds against the given now"
        assert actual["mtime"].count(":") == 2 and "T" in actual["mtime"], "ISO-8601 local"

    def test_vanished_file_keeps_the_shape(self, tmp_path):
        actual = file_record(tmp_path / "gone.bin")
        assert actual == {"name": "gone.bin", "bytes": 0, "mtime": None, "age_s": None}
