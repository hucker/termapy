"""Tests for app.py config utilities, custom buttons, and script editor."""

import json
from pathlib import Path

import pytest

from termapy.dialogs import _SCRIPT_TEMPLATE
from termapy.config import (
    DEFAULT_CFG,
    cfg_data_dir,
    cfg_history_path,
    cfg_log_path,
    cfg_path_for_name,
    cfg_plugins_dir,
    load_config,
)


# -- cfg_data_dir: subdirectory creation ------------------------------------


class TestCfgDataDir:
    def test_creates_subdirs(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual == config_path.parent  # returns parent directory
        for sub in ("plugins", "ss", "scripts"):
            assert (actual / sub).is_dir()  # all subdirs created

    def test_idempotent(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()

        # Act
        cfg_data_dir(str(config_path))
        cfg_data_dir(str(config_path))  # second call should not error

        # Assert
        assert (config_path.parent / "ss").is_dir()  # subdirs still exist

    def test_creates_parent_if_needed(self, tmp_path):
        # Arrange
        config_path = tmp_path / "new" / "new.json"

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual.exists()  # parent dir created
        assert (actual / "plugins").is_dir()  # subdirs created


# -- cfg helper functions ---------------------------------------------------


class TestCfgHelpers:
    def test_cfg_path_for_name(self):
        actual = cfg_path_for_name("mydev")
        assert actual.name == "mydev.json"  # filename matches
        assert actual.parent.name == "mydev"  # parent dir matches

    def test_cfg_log_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        actual = cfg_log_path(str(config_path))
        assert actual.endswith("dev.log")  # log named after config

    def test_cfg_history_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        actual = cfg_history_path(str(config_path))
        assert actual.endswith(".cmd_history.txt")  # history file pattern

    def test_cfg_plugins_dir(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        actual = cfg_plugins_dir(str(config_path))
        assert actual.name == "plugins"  # correct subdir name
        assert actual.is_dir()  # directory created


# -- DEFAULT_CFG structure --------------------------------------------------


class TestDefaultCfg:
    def test_has_custom_buttons(self):
        assert "custom_buttons" in DEFAULT_CFG  # key exists
        assert isinstance(DEFAULT_CFG["custom_buttons"], list)  # is a list
        assert len(DEFAULT_CFG["custom_buttons"]) >= 4  # at least 4 button placeholders

    def test_custom_buttons_info_enabled(self):
        """First default button is the Info button (enabled)."""
        info_btn = DEFAULT_CFG["custom_buttons"][0]
        assert info_btn["enabled"] is True  # Info button enabled
        assert info_btn["name"] == "Info"  # named Info
        assert info_btn["command"] == "!info"  # runs !info

    def test_custom_buttons_placeholders_disabled(self):
        """Remaining default buttons are disabled placeholders."""
        for btn in DEFAULT_CFG["custom_buttons"][1:]:
            assert btn["enabled"] is False  # placeholder disabled

    def test_custom_buttons_have_required_fields(self):
        for btn in DEFAULT_CFG["custom_buttons"]:
            assert "enabled" in btn  # enabled field present
            assert "name" in btn  # name field present
            assert "command" in btn  # command field present
            assert "tooltip" in btn  # tooltip field present

    def test_has_essential_keys(self):
        for key in ("port", "baudrate", "line_ending", "repl_prefix"):
            assert key in DEFAULT_CFG  # essential config key present


# -- load_config ------------------------------------------------------------


class TestLoadConfig:
    def test_creates_default_if_missing(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "test" / "test.json"

        # Act
        actual = load_config(str(config_path))

        # Assert
        assert config_path.exists()  # file created on disk
        assert actual["port"] == DEFAULT_CFG["port"]  # default port
        assert "custom_buttons" in actual  # default buttons added

    def test_adds_missing_keys(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        minimal = {"port": "COM3", "baudrate": 9600}
        config_path.write_text(json.dumps(minimal))

        # Act
        actual = load_config(str(config_path))

        # Assert
        assert actual["port"] == "COM3"  # original value preserved
        assert "custom_buttons" in actual  # missing default added
        actual_saved = json.loads(config_path.read_text())
        assert "custom_buttons" in actual_saved  # persisted to disk

    def test_does_not_overwrite_existing_keys(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        custom = {
            "port": "COM7",
            "baudrate": 9600,
            "custom_buttons": [
                {"enabled": True, "name": "Go", "command": "GO", "tooltip": "Run"},
            ],
        }
        config_path.write_text(json.dumps(custom))

        # Act
        actual = load_config(str(config_path))

        # Assert
        assert actual["port"] == "COM7"  # custom port preserved
        assert len(actual["custom_buttons"]) == 1  # custom buttons not replaced
        assert actual["custom_buttons"][0]["enabled"] is True  # custom value kept


# -- _SCRIPT_TEMPLATE -------------------------------------------------------


class TestScriptTemplate:
    def test_has_placeholder(self):
        actual = _SCRIPT_TEMPLATE.format(name="test_script")
        assert "test_script" in actual  # name placeholder expanded

    def test_has_comments(self):
        actual = _SCRIPT_TEMPLATE.format(name="x")
        lines = actual.strip().splitlines()
        assert all(
            line.startswith("#") for line in lines if line.strip()
        )  # all lines are comments

    def test_has_example_commands(self):
        actual = _SCRIPT_TEMPLATE.format(name="x")
        assert "!sleep" in actual  # contains REPL example


# -- Custom button config validation ----------------------------------------


class TestCustomButtonConfig:
    def test_enabled_filter(self):
        """Simulate the enabled filter used in compose."""
        # Arrange
        buttons = [
            {"enabled": True, "name": "A", "command": "cmd1", "tooltip": "t1"},
            {"enabled": False, "name": "B", "command": "cmd2", "tooltip": "t2"},
            {"enabled": True, "name": "C", "command": "cmd3", "tooltip": "t3"},
        ]

        # Act
        actual = [b for b in buttons if b.get("enabled", False)]

        # Assert
        assert len(actual) == 2  # only enabled buttons returned
        assert actual[0]["name"] == "A"  # first enabled button
        assert actual[1]["name"] == "C"  # second enabled button

    def test_missing_enabled_defaults_false(self):
        # Arrange
        buttons = [{"name": "X", "command": "cmd", "tooltip": "tip"}]

        # Act
        actual = [b for b in buttons if b.get("enabled", False)]

        # Assert
        assert len(actual) == 0  # missing enabled treated as False

    def test_command_split(self):
        """Simulate the \\n split used in _run_custom_button."""
        # Arrange
        raw = "ATZ\\nAT+INFO\\n!sleep 500ms"
        expected = ["ATZ", "AT+INFO", "!sleep 500ms"]

        # Act
        actual = [c.strip() for c in raw.split("\\n") if c.strip()]

        # Assert
        assert actual == expected  # multi-command split correctly

    def test_command_split_single(self):
        actual = [c.strip() for c in "ATZ".split("\\n") if c.strip()]
        assert actual == ["ATZ"]  # single command unchanged

    def test_command_split_empty(self):
        actual = [c.strip() for c in "".split("\\n") if c.strip()]
        assert actual == []  # empty string yields empty list

    def test_repl_prefix_detection(self):
        prefix = "!"
        assert "!run test.run".startswith(prefix)  # REPL command detected
        assert not "ATZ".startswith(prefix)  # serial command not matched
