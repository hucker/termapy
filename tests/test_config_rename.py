"""Tests for ``config.rename_config`` and ``config.validate_file_stem``.

A config is ``termapy_cfg/<name>/<name>.cfg``: the folder carries the
run/, proto/, cap/ ... data, so renaming must move the folder and the
file together, with the ``<name>.history`` sidecar, and never overwrite.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.config import rename_config, validate_file_stem


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
        # Arrange -- the user types the name WITH its extension
        old = _make_cfg(tmp_path, "xmitter")

        # Act
        actual = rename_config(str(old), "xm.cfg")

        # Assert -- xm/xm.cfg, not xm.cfg/xm.cfg.cfg (seen live)
        assert Path(actual) == tmp_path / "xm" / "xm.cfg"
        assert (tmp_path / "xm" / "xm.cfg").is_file()

    def test_non_standard_layout_renames_only_the_file(self, tmp_path):
        # Arrange -- folder named differently from the config
        old = _make_cfg(tmp_path, "bench", folder="hardware")

        # Act
        actual = rename_config(str(old), "lab")

        # Assert
        assert Path(actual) == tmp_path / "hardware" / "lab.cfg"
        assert (tmp_path / "hardware").is_dir(), "a folder that is not named after the cfg stays"

    def test_refuses_to_overwrite_an_existing_config(self, tmp_path):
        # Arrange
        old = _make_cfg(tmp_path, "bench")
        _make_cfg(tmp_path, "lab")

        # Act / Assert
        with pytest.raises(ValueError, match="already exists"):
            rename_config(str(old), "lab")
        assert old.is_file(), "nothing moved"

    def test_refuses_the_same_name(self, tmp_path):
        old = _make_cfg(tmp_path, "bench")
        with pytest.raises(ValueError, match="already named"):
            rename_config(str(old), "bench")

    def test_missing_config(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            rename_config(str(tmp_path / "nope" / "nope.cfg"), "lab")

    @pytest.mark.parametrize("bad", ["", "a b", "a/b", "a\\b", ".hidden", ".."])
    def test_refuses_names_that_cannot_be_a_stem(self, tmp_path, bad):
        old = _make_cfg(tmp_path, "bench")
        with pytest.raises(ValueError):
            rename_config(str(old), bad)
        assert old.is_file(), "nothing moved"


class TestValidateFileStem:
    def test_good_names_pass(self):
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
        reason = validate_file_stem(bad)
        assert reason is not None and fragment in reason
