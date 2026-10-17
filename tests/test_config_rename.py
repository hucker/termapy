"""Tests for ``config.rename_config``, ``config.delete_config`` and ``config.validate_file_stem``.

A config is ``termapy_cfg/<name>/<name>.cfg``: the folder carries the
run/, proto/, cap/ ... data, so renaming must move the folder and the
file together, with every stem-named sidecar, and never overwrite; deleting
removes the folder whole, and refuses before anything goes when it cannot.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from termapy.config import (
    delete_config,
    delete_config_question,
    rename_config,
    validate_file_stem,
)


def _make_cfg(root: Path, name: str, *, folder: str | None = None, history: bool = False):
    """A config in ``root/<folder or name>/<name>.cfg`` with a run/ folder."""
    cfg_folder = root / (folder or name)
    cfg_folder.mkdir(parents=True)
    path = cfg_folder / f"{name}.cfg"
    path.write_text(json.dumps({"title": name}), encoding="utf-8")
    (cfg_folder / "run").mkdir()
    (cfg_folder / "run" / "hello.run").write_text("/echo hi\n", encoding="utf-8")
    if history:
        (cfg_folder / f"{name}.history").write_text("/echo hi\n", encoding="utf-8")
    return path


class TestRenameConfig:
    def test_standard_layout_moves_folder_file_and_history(self, tmp_path):
        """A <name>/<name>.cfg rename moves the folder, its data and the history together."""
        # Arrange
        old = _make_cfg(tmp_path, "bench", history=True)

        # Act
        actual = rename_config(str(old), "lab")

        # Assert
        expected = tmp_path / "lab" / "lab.cfg"
        assert Path(actual) == expected, "returns the new .cfg path"
        assert expected.is_file() and not old.exists(), "file moved"
        assert (tmp_path / "lab" / "run" / "hello.run").is_file(), (
            "the folder (with its data) moved with the file"
        )
        assert (tmp_path / "lab" / "lab.history").is_file(), "history sidecar renamed"
        assert not (tmp_path / "bench").exists(), "old folder gone"

    def test_typed_extension_is_not_doubled(self, tmp_path):
        """A new name typed with .cfg is taken as the stem, not xm.cfg/xm.cfg.cfg."""
        # Arrange -- the user types the name WITH its extension
        old = _make_cfg(tmp_path, "xmitter")

        # Act
        actual = rename_config(str(old), "xm.cfg")

        # Assert -- xm/xm.cfg, not xm.cfg/xm.cfg.cfg (seen live)
        assert Path(actual) == tmp_path / "xm" / "xm.cfg"
        assert (tmp_path / "xm" / "xm.cfg").is_file()

    def test_non_standard_layout_renames_only_the_file(self, tmp_path):
        """A folder not named after the config is left alone; only the .cfg is renamed."""
        # Arrange -- folder named differently from the config
        old = _make_cfg(tmp_path, "bench", folder="hardware")

        # Act
        actual = rename_config(str(old), "lab")

        # Assert
        assert Path(actual) == tmp_path / "hardware" / "lab.cfg"
        assert (tmp_path / "hardware").is_dir(), "a folder that is not named after the cfg stays"

    def test_refuses_to_overwrite_an_existing_config(self, tmp_path):
        """Renaming onto an existing config fails and moves nothing."""
        # Arrange
        old = _make_cfg(tmp_path, "bench")
        _make_cfg(tmp_path, "lab")

        # Act / Assert
        with pytest.raises(ValueError, match="already exists"):
            rename_config(str(old), "lab")
        assert old.is_file(), "nothing moved"

    def test_refuses_the_same_name(self, tmp_path):
        """Renaming a config to its current name is an error, not a silent no-op."""
        old = _make_cfg(tmp_path, "bench")
        with pytest.raises(ValueError, match="already named"):
            rename_config(str(old), "bench")

    def test_missing_config(self, tmp_path):
        """A config path that does not exist raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            rename_config(str(tmp_path / "nope" / "nope.cfg"), "lab")

    @pytest.mark.parametrize("bad", ["", "a b", "a/b", "a\\b", ".hidden", ".."])
    def test_refuses_names_that_cannot_be_a_stem(self, tmp_path, bad):
        """Empty, spaced, path-separated or dot-led names are refused before any move."""
        old = _make_cfg(tmp_path, "bench")
        with pytest.raises(ValueError):
            rename_config(str(old), bad)
        assert old.is_file(), "nothing moved"


class TestValidateFileStem:
    def test_good_names_pass(self):
        """Ordinary names with underscores or hyphens validate as None (no reason)."""
        assert validate_file_stem("smoke_test") is None
        assert validate_file_stem("bench-2") is None

    @pytest.mark.parametrize(
        "bad, fragment",
        [
            ("", "required"),
            ("a b", "spaces"),
            ("a/b", "separators"),
            ("a\\b", "separators"),
            (".hidden", "dot"),
        ],
    )
    def test_bad_names_say_why(self, bad, fragment):
        """Each kind of bad stem returns a reason naming the specific problem."""
        reason = validate_file_stem(bad)
        assert reason is not None and fragment in reason


class TestRenameCarriesEverySidecar:
    """Every stem-named sidecar in ``folders.SIDECARS`` follows a rename."""

    def test_log_report_profile_and_symbol_table_follow(self, tmp_path):
        """Log, report, profile and sym/ table are renamed too; no old-name file remains."""
        # Arrange -- the sidecars a real session leaves behind, log included
        old = _make_cfg(tmp_path, "bench", history=True)
        folder = old.parent
        (folder / "bench.log").write_text("> hi", encoding="utf-8")
        (folder / "bench.md").write_text("# Project: bench", encoding="utf-8")
        (folder / "bench.profile.json").write_text("{}", encoding="utf-8")
        (folder / "sym").mkdir()
        (folder / "sym" / "bench.symbols.json").write_text("{}", encoding="utf-8")

        # Act
        rename_config(str(old), "lab")

        # Assert
        lab = tmp_path / "lab"
        for name in ("lab.history", "lab.log", "lab.md", "lab.profile.json"):
            assert (lab / name).is_file(), f"{name} followed the rename"
        assert (lab / "sym" / "lab.symbols.json").is_file(), (
            "the symbol table followed, inside sym/"
        )
        left = sorted(path.name for path in lab.rglob("bench*"))
        assert left == [], f"nothing keeps the old name: {left}"


class TestDeleteConfig:
    """config-deleted-once: ``delete_config`` removes a config's whole folder, or only its own files."""

    def test_the_standard_layout_loses_its_whole_folder(self, tmp_path):
        """The folder named after the config goes with the .cfg, run script and all."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev", history=True)

        # Act
        actual_count = delete_config(path)

        # Assert
        assert actual_count == 3, "the .cfg, its history and the run script"
        assert not path.parent.exists(), "the folder is gone"
        assert list(tmp_path.iterdir()) == [], "nothing is left aside"

    def test_twice_in_a_row_the_second_is_not_found(self, tmp_path):
        """A deleted config is gone: deleting it again names it as not found."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev")
        delete_config(path)

        # Act / Assert
        with pytest.raises(FileNotFoundError, match="Config not found: mydev"):
            delete_config(path)

    def test_a_shared_folder_keeps_everything_but_the_config(self, tmp_path):
        """A config in a folder of another name takes only its .cfg and stem-named sidecars."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev", folder="shared", history=True)

        # Act
        actual_count = delete_config(path)

        # Assert
        assert actual_count == 2, "the .cfg and its history"
        assert (tmp_path / "shared" / "run" / "hello.run").is_file(), "the folder's script stays"

    def test_a_folder_left_aside_by_a_failed_delete_is_cleared(self, tmp_path):
        """A .<name>.deleting leftover is removed, not mistaken for an obstacle."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev")
        leftover = tmp_path / ".mydev.deleting"
        leftover.mkdir()
        (leftover / "stale.txt").write_text("x", encoding="utf-8")

        # Act
        delete_config(path)

        # Assert
        assert list(tmp_path.iterdir()) == [], "the config and the leftover are both gone"

    @pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to move an open file")
    def test_a_config_held_open_is_refused_with_nothing_removed(self, tmp_path):
        """A file another program holds open blocks the rename aside, so nothing is deleted."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev")
        held = path.parent / "mydev.log"

        # Act
        with open(held, "w", encoding="utf-8"), pytest.raises(OSError, match="nothing deleted"):
            delete_config(path)

        # Assert
        assert path.is_file(), "the .cfg is still there"
        assert (path.parent / "run" / "hello.run").is_file(), "so is the run script"

    def test_the_question_counts_what_goes(self, tmp_path):
        """The one delete question, word for word, as every frontend shows it."""
        # Arrange
        path = _make_cfg(tmp_path, "mydev")
        expected = (
            f"Delete config 'mydev' and its folder {path.parent} (2 files: "
            "scripts, logs, captures)? This cannot be undone."
        )

        # Act
        actual = delete_config_question(path)

        # Assert
        assert actual == expected
