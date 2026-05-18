"""Tests for the .run-file docstring parser (termapy.run_docstring)."""

from __future__ import annotations

from pathlib import Path


from termapy.run_docstring import extract_docstring


def _write(tmp_path: Path, body: str) -> Path:
    """Helper: write ``body`` to ``tmp_path/script.run`` and return the path."""
    path = tmp_path / "script.run"
    path.write_text(body, encoding="utf-8")
    return path


class TestExtractDocstring:
    """The parser is intentionally tiny -- exhaust the corner cases."""

    def test_single_line_docstring(self, tmp_path):
        # Arrange
        path = _write(tmp_path, "# Smoke-test the device.\n/port.connect\n")

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "Smoke-test the device.", "summary is the first comment line"
        assert full == "Smoke-test the device.", (
            "single-line block: full == summary"
        )

    def test_multiline_block_summary_is_first_line(self, tmp_path):
        # Arrange
        path = _write(
            tmp_path,
            "# Smoke test.\n"
            "# Connects, captures 2s of output.\n"
            "# Used by the CI gate.\n"
            "/port.connect\n",
        )

        # Act
        summary, full = extract_docstring(path)

        # Assert -- summary is first line; full preserves the block.
        assert summary == "Smoke test.", "summary is line 1"
        expected_full = (
            "Smoke test.\n"
            "Connects, captures 2s of output.\n"
            "Used by the CI gate."
        )
        assert full == expected_full, "full block preserved with one-space strip"

    def test_block_with_blank_comment_lines(self, tmp_path):
        # Arrange -- ``#`` alone is a paragraph separator (Python-doc style).
        path = _write(
            tmp_path,
            "# Title line.\n"
            "#\n"
            "# Paragraph two.\n"
            "/cmd\n",
        )

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "Title line.", "summary is the title"
        assert "Title line." in full, "title preserved"
        assert "Paragraph two." in full, "second paragraph preserved"
        # The bare ``#`` becomes a blank line in the output -- separating
        # paragraphs the way the author intended.
        assert "\n\nParagraph" in full, "blank line separates paragraphs"

    def test_block_ends_at_non_comment_line(self, tmp_path):
        # Arrange -- a non-# line ends the block; subsequent # lines are
        # NOT part of the docstring.
        path = _write(
            tmp_path,
            "# Header.\n"
            "/cmd1\n"
            "# This is NOT part of the docstring.\n"
            "/cmd2\n",
        )

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "Header.", "only the leading block counts"
        assert "NOT part" not in full, "interior comments excluded"

    def test_block_ends_at_blank_line(self, tmp_path):
        # Arrange -- a blank line ends the block (no leading-#).
        path = _write(
            tmp_path,
            "# Header.\n"
            "\n"
            "# Not the docstring.\n"
            "/cmd\n",
        )

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "Header.", "first block is the docstring"
        assert full == "Header.", "second block excluded"

    def test_no_docstring_returns_empty_strings(self, tmp_path):
        # Arrange -- script with no leading comment.
        path = _write(tmp_path, "/port.connect\n/cap.text out.txt\n")

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "", "no docstring -> empty summary"
        assert full == "", "no docstring -> empty full"

    def test_leading_blank_line_means_no_docstring(self, tmp_path):
        # Arrange -- strict: docstring MUST be at the very top.
        path = _write(tmp_path, "\n# Description.\n/cmd\n")

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "", "leading blank line invalidates the docstring"
        assert full == "", "matches Python docstring strictness"

    def test_comment_without_leading_space_preserved(self, tmp_path):
        # Arrange -- "#text" (no space after #) is accepted, no space stripped.
        path = _write(tmp_path, "#NoSpace\n/cmd\n")

        # Act
        summary, full = extract_docstring(path)

        # Assert
        assert summary == "NoSpace", "no extra space stripping when none present"
        assert full == "NoSpace", "single-line ok"

    def test_missing_file_returns_empty(self, tmp_path):
        # Arrange / Act
        summary, full = extract_docstring(tmp_path / "does_not_exist.run")

        # Assert
        assert summary == "", "missing file is benign"
        assert full == "", "missing file is benign"

    def test_trailing_whitespace_in_summary_stripped(self, tmp_path):
        # Arrange
        path = _write(tmp_path, "# Trailing space.   \n/cmd\n")

        # Act
        summary, _full = extract_docstring(path)

        # Assert -- summary is shown in /run.list columns; trailing
        # whitespace would inflate the column width visibly.
        assert summary == "Trailing space.", "summary is .strip()ed"
