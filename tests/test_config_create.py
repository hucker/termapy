"""Tests for creating a config: ``config.new_config``, ``config.create_config`` and ``--new``.

Every frontend makes a config the same way (registry
``config-created-once``): ``new_config`` builds it and ``create_config``
writes it, asking before it replaces a config already there.  An
overwrite replaces only the ``.cfg``; the folder's scripts and data
stay.  ``cli_flags.run_new`` is ``termapy --new`` on top of the two.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn

import pytest

from termapy.cli_flags import run_new
from termapy.config import (
    ConfigExistsError,
    create_config,
    load_config,
    new_config,
    validate_config,
)
from termapy.defaults import default_cfg


def _existing(root: Path, name: str) -> tuple[Path, Path]:
    """A config folder holding its .cfg and a run script."""
    folder = root / name
    (folder / "run").mkdir(parents=True)
    cfg_path = folder / f"{name}.cfg"
    cfg_path.write_text(json.dumps({"title": "old"}), encoding="utf-8")
    script = folder / "run" / "x.run"
    script.write_text("/print hi\n", encoding="utf-8")
    return cfg_path, script


def _title(cfg_path: Path) -> str:
    """The title stored in a .cfg on disk."""
    return json.loads(cfg_path.read_text(encoding="utf-8"))["title"]


class TestNewConfig:
    """``new_config`` is the defaults, titled by name, with the given settings."""

    def test_settings_land_where_the_cfg_keeps_them(self):
        """Port, baud and frame go under serial; eol, echo and auto_connect at the top."""
        # Act
        cfg = new_config(
            "mydev",
            port="COM4",
            baud=9600,
            mode=("E", 7, 1.0),
            eol="\r\n",
            echo=True,
            auto_connect=True,
        )

        # Assert
        serial_cfg = cfg["serial"]
        assert cfg["title"] == "mydev"
        assert serial_cfg["port"] == "COM4"
        assert serial_cfg["baud_rate"] == 9600
        assert (serial_cfg["parity"], serial_cfg["byte_size"], serial_cfg["stop_bits"]) == (
            "E",
            7,
            1.0,
        )
        assert cfg["eol"] == "\r\n"
        assert cfg["echo"] is True
        assert cfg["auto_connect"] is True

    def test_unset_settings_keep_their_defaults(self):
        """Only the title differs from the defaults when nothing else is given."""
        # Arrange
        expected = default_cfg()
        expected["title"] = "mydev"

        # Act
        actual = new_config("mydev")

        # Assert
        assert actual == expected, "a bare new config is the defaults plus its title"

    def test_custom_baud_is_derived_from_the_rate(self):
        """A rate outside the standard list marks custom_baud; a standard one does not."""
        # Act
        actual_custom = new_config("a", baud=250000)["serial"]["custom_baud"]
        actual_standard = new_config("b", baud=115200)["serial"]["custom_baud"]

        # Assert
        assert actual_custom is True, "250000 is not a standard rate"
        assert actual_standard is False, "115200 is a standard rate"

    def test_an_explicit_custom_baud_wins(self):
        """The wizard's checkbox decides custom_baud when it is passed."""
        # Act
        actual = new_config("a", baud=115200, custom_baud=True)["serial"]["custom_baud"]

        # Assert
        assert actual is True, "the caller's choice overrides the derived value"


class TestCreateConfig:
    """``create_config`` is the one writer of a new .cfg."""

    def test_writes_a_cfg_that_loads_cleanly(self, tmp_path):
        """A new config is a folder with its .cfg and .gitignore, and it loads with no warnings."""
        # Arrange
        cfg = new_config("mydev", port="COM4", baud=115200)
        cfg_path = tmp_path / "mydev" / "mydev.cfg"

        # Act
        actual_path = create_config(cfg_path, cfg)

        # Assert
        assert actual_path == cfg_path
        assert (tmp_path / "mydev" / ".gitignore").is_file(), "prepare_cfg_folder set it up"
        loaded = load_config(str(cfg_path))
        assert loaded["title"] == "mydev"
        assert validate_config(loaded) == [], "a new config is valid as written"

    def test_twice_in_a_row_the_second_is_refused(self, tmp_path):
        """config-created-once: creating the same config again raises, and the first is untouched."""
        # Arrange
        cfg_path = tmp_path / "mydev" / "mydev.cfg"
        create_config(cfg_path, new_config("mydev"))

        # Act
        with pytest.raises(ConfigExistsError) as excinfo:
            create_config(cfg_path, new_config("other-title"))

        # Assert
        assert excinfo.value.file_count == 2, "the .cfg and its .gitignore"
        assert _title(cfg_path) == "mydev", "the first config is untouched"

    def test_overwrite_replaces_the_cfg_and_keeps_the_rest(self, tmp_path):
        """config-created-once: an overwrite rewrites the .cfg and leaves the folder's other files."""
        # Arrange
        cfg_path, script = _existing(tmp_path, "mydev")

        # Act
        create_config(cfg_path, new_config("mydev"), overwrite=True)

        # Assert
        assert _title(cfg_path) == "mydev", "the .cfg is the new config"
        assert script.read_text(encoding="utf-8") == "/print hi\n", "the run script is kept"

    def test_an_existing_folder_without_a_cfg_is_taken(self, tmp_path):
        """A folder named after the config counts as the config: it may hold another setup's files."""
        # Arrange
        (tmp_path / "mydev" / "run").mkdir(parents=True)

        # Act / Assert
        with pytest.raises(ConfigExistsError):
            create_config(tmp_path / "mydev" / "mydev.cfg", new_config("mydev"))

    def test_a_shared_folder_is_taken_only_by_the_file(self, tmp_path):
        """A .cfg in a folder not named after it (Save As to a path) is refused only when it exists."""
        # Arrange
        (tmp_path / "shared").mkdir()
        cfg_path = tmp_path / "shared" / "mydev.cfg"

        # Act
        create_config(cfg_path, new_config("mydev"))

        # Assert
        assert cfg_path.is_file(), "an existing folder of another name does not block the file"

    def test_a_bad_name_is_refused_before_anything_is_written(self, tmp_path):
        """A name with a space fails validate_file_stem and no folder appears."""
        # Act
        with pytest.raises(ValueError, match="Name may not contain spaces"):
            create_config(tmp_path / "my dev" / "my dev.cfg", new_config("my dev"))

        # Assert
        assert not (tmp_path / "my dev").exists(), "nothing is created"

    def test_the_question_names_the_folder_and_keeps_the_rest(self, tmp_path):
        """The one overwrite question, word for word, as every frontend shows it."""
        # Arrange
        cfg_path, _ = _existing(tmp_path, "mydev")
        expected = (
            f"Config 'mydev' already exists ({tmp_path / 'mydev'}, 2 files). "
            "Overwrite mydev.cfg? Other files are kept."
        )

        # Act
        with pytest.raises(ConfigExistsError) as excinfo:
            create_config(cfg_path, new_config("mydev"))

        # Assert
        assert excinfo.value.question == expected


class _UsageError(Exception):
    """What the injected ``usage_error`` raises, standing in for argparse's exit 2."""


def _usage_error(message: str) -> NoReturn:
    """Raise instead of exiting, so a test can read the message."""
    raise _UsageError(message)


class TestRunNew:
    """``termapy --new NAME PORT [SETTING ...]`` with the overwrite answer injected."""

    @pytest.fixture
    def cfg_root(self, tmp_path, monkeypatch):
        """Point the config directory at tmp_path, the suite's usual way."""
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
        return tmp_path

    def test_creates_the_config_from_the_settings(self, cfg_root):
        """The /port.connect grammar sets port, baud and frame, and the config connects on load."""
        # Act
        actual_path = run_new(
            ["mydev", "COM4", "9600", "E71"],
            confirm=lambda _question: False,
            usage_error=_usage_error,
        )

        # Assert
        data = json.loads(actual_path.read_text(encoding="utf-8"))
        assert actual_path == cfg_root / "mydev" / "mydev.cfg"
        assert data["title"] == "mydev"
        assert data["serial"]["port"] == "COM4"
        assert data["serial"]["baud_rate"] == 9600
        assert data["serial"]["parity"] == "E"
        assert data["auto_connect"] is True, "--new starts connected"

    def test_yes_overwrites_and_keeps_the_scripts(self, cfg_root):
        """An existing config is replaced on yes; its run script stays."""
        # Arrange
        cfg_path, script = _existing(cfg_root, "mydev")
        questions: list[str] = []

        def _yes(question: str) -> bool:
            questions.append(question)
            return True

        # Act
        run_new(["mydev", "COM4"], confirm=_yes, usage_error=_usage_error)

        # Assert
        assert len(questions) == 1, "asked exactly once"
        assert _title(cfg_path) == "mydev", "the .cfg was replaced"
        assert script.is_file(), "the run script is kept"

    def test_no_changes_nothing_and_exits_1(self, cfg_root):
        """An existing config is left alone on no, and --new fails."""
        # Arrange
        cfg_path, _ = _existing(cfg_root, "mydev")

        # Act
        with pytest.raises(SystemExit) as excinfo:
            run_new(["mydev", "COM4"], confirm=lambda _question: False, usage_error=_usage_error)

        # Assert
        assert excinfo.value.code == 1
        assert _title(cfg_path) == "old", "the existing .cfg is untouched"

    def test_a_port_is_required(self, cfg_root):
        """Settings without a port are a usage error, and nothing is written."""
        # Act
        with pytest.raises(_UsageError, match="a port is required"):
            run_new(["mydev", "115200", "8N1"], confirm=lambda _q: False, usage_error=_usage_error)

        # Assert
        assert not (cfg_root / "mydev").exists(), "nothing is created"

    def test_a_bad_setting_is_a_usage_error(self, cfg_root):
        """A token /port.connect cannot read is refused with its reason."""
        # Act / Assert
        with pytest.raises(_UsageError, match="9Q9"):
            run_new(
                ["mydev", "COM4", "115200", "9Q9"],
                confirm=lambda _q: False,
                usage_error=_usage_error,
            )
