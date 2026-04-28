"""Unit tests for ``termapy.tree_render`` (the ``FileTree`` renderer)."""

from __future__ import annotations

from pathlib import Path

import pytest

from termapy.tree_render import FileTree, file_meta


# ── Connector + shape ────────────────────────────────────────────────────────


class TestEmpty:
    def test_no_sections_renders_no_lines(self):
        # Arrange
        # Act
        actual = FileTree(sections=[]).render()

        # Assert
        expected: list[str] = []
        assert actual == expected, "empty input renders zero lines"


class TestRootLevelFiles:
    def test_single_file_uses_last_connector(self):
        # Arrange
        sections = [("a.run", [])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert
        expected = ["└── a.run"]
        assert actual == expected, "lone file is the last entry"

    def test_multiple_files_branch_then_terminate(self):
        # Arrange
        sections = [("a.run", []), ("b.run", []), ("c.run", [])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert
        expected = ["├── a.run", "├── b.run", "└── c.run"]
        assert actual == expected, "first two branch, last terminates"


class TestDirectories:
    def test_single_dir_with_children(self):
        # Arrange
        sections = [("run/", ["a.run", "b.run"])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert -- last dir uses 4-space child indent (no trailing │)
        expected = ["└── run/", "    ├── a.run", "    └── b.run"]
        assert actual == expected, "last dir uses spaces under it"

    def test_two_dirs_first_uses_pipe_indent(self):
        # Arrange
        sections = [("run/", ["a.run"]), ("proto/", ["x.pro"])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert -- non-last dir uses │ indent so the tree spine is visible
        expected = [
            "├── run/",
            "│   └── a.run",
            "└── proto/",
            "    └── x.pro",
        ]
        assert actual == expected, "non-last dir threads │ through children"

    def test_empty_directory_renders_header_only(self):
        # Arrange
        sections = [("run/", [])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert
        expected = ["└── run/"]
        assert actual == expected, "empty dir is just a header line"


class TestMixed:
    def test_dirs_then_loose_files(self):
        # Arrange -- caller responsibility: dirs come before loose files
        sections = [("run/", ["a.run"]), ("readme.md", [])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert -- dir is non-last, so its children get │ indent
        expected = [
            "├── run/",
            "│   └── a.run",
            "└── readme.md",
        ]
        assert actual == expected, "dir threads │, last loose file terminates"


# ── Color markup ─────────────────────────────────────────────────────────────


class TestColor:
    def test_color_off_emits_plain_ascii(self):
        # Arrange
        sections = [("run/", ["a.run"])]

        # Act
        actual = FileTree(sections=sections, color=False).render()

        # Assert -- no Rich markup tags
        for line in actual:
            assert "[" not in line, f"plain output has no markup, got {line!r}"

    def test_color_on_wraps_dirs_files_connectors(self):
        # Arrange
        sections = [("run/", ["a.run"])]

        # Act
        actual = FileTree(sections=sections, color=True).render()

        # Assert -- one dir line + one file line, both wrapped
        expected_dir = "[dim]└── [/][cyan]run/[/]"
        expected_file = "[dim]    └── [/][blue]a.run[/]"
        actual_dir = actual[0]
        actual_file = actual[1]
        assert actual_dir == expected_dir, "dir uses cyan, dim connectors"
        assert actual_file == expected_file, "file uses blue, dim connectors"


# ── Indent prefix ────────────────────────────────────────────────────────────


class TestIndent:
    def test_indent_prepended_to_every_line(self):
        # Arrange
        sections = [("run/", ["a.run"]), ("b.run", [])]

        # Act
        actual = FileTree(sections=sections, color=False, indent="  ").render()

        # Assert -- every line starts with the two-space indent
        for line in actual:
            assert line.startswith("  "), f"line {line!r} missing indent"


# ── File-dates path ──────────────────────────────────────────────────────────


class TestFileDates:
    def test_file_dates_appends_metadata_columns(self, tmp_path: Path):
        # Arrange -- create a real file so file_meta() succeeds
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "a.run").write_text("/echo hi", encoding="utf-8")
        sections = [("run/", ["a.run"])]

        # Act
        actual = FileTree(
            sections=sections,
            base_dir=tmp_path,
            file_dates=True,
            color=False,
            name_width=10,
        ).render()

        # Assert -- the file line has the size suffix appended after padding
        # (padded name "a.run     " then "  <size>  <created>  <modified>").
        file_line = actual[1]
        assert "a.run     " in file_line, f"name padded to width 10: {file_line!r}"
        assert " B" in file_line or " KB" in file_line, (
            f"size column appended: {file_line!r}"
        )

    def test_no_file_dates_omits_padding(self):
        # Arrange -- with file_dates=False, name_width has no effect
        sections = [("a.run", [])]

        # Act
        actual = FileTree(
            sections=sections,
            color=False,
            name_width=20,  # would over-pad if applied
        ).render()

        # Assert -- raw filename, no trailing whitespace pad
        expected = ["└── a.run"]
        assert actual == expected, "no padding when file_dates=False"

    def test_file_meta_handles_missing_file(self, tmp_path: Path):
        # Arrange -- point at a file that doesn't exist
        # Act
        actual = file_meta(tmp_path / "nonexistent.run")

        # Assert
        expected = ("?", "?", "?")
        assert actual == expected, "missing file returns ? for all columns"


# ── Caller-facing 2-level tree (the help.py grouping pattern) ────────────────


class TestHelpStyleSections:
    """The shape help.py builds: ``[(dirname/, [files]), ..., (loose, [])]``."""

    def test_dirs_then_loose_at_root(self):
        # Arrange
        sections = [
            ("demo/", [".target_menu.cfg", "demo.cfg"]),
            ("xmitter/", ["xmitter.cfg"]),
        ]

        # Act
        actual = FileTree(sections=sections, color=False, indent="  ").render()

        # Assert
        expected = [
            "  ├── demo/",
            "  │   ├── .target_menu.cfg",
            "  │   └── demo.cfg",
            "  └── xmitter/",
            "      └── xmitter.cfg",
        ]
        assert actual == expected, "two-config tree renders with proper hierarchy"
