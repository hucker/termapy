"""Tests for app.py config utilities, custom buttons, and script editor."""

import json
from pathlib import Path

import pytest

from termapy.app import (
    DEFAULT_CFG,
    _SCRIPT_TEMPLATE,
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
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        d = cfg_data_dir(str(config_path))
        assert d == config_path.parent
        for sub in ("plugins", "ss", "scripts"):
            assert (d / sub).is_dir()

    def test_idempotent(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        cfg_data_dir(str(config_path))
        cfg_data_dir(str(config_path))  # no error on second call
        assert (config_path.parent / "ss").is_dir()

    def test_creates_parent_if_needed(self, tmp_path):
        config_path = tmp_path / "new" / "new.json"
        d = cfg_data_dir(str(config_path))
        assert d.exists()
        assert (d / "plugins").is_dir()


# -- cfg helper functions ---------------------------------------------------


class TestCfgHelpers:
    def test_cfg_path_for_name(self):
        p = cfg_path_for_name("mydev")
        assert p.name == "mydev.json"
        assert p.parent.name == "mydev"

    def test_cfg_log_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        log = cfg_log_path(str(config_path))
        assert log.endswith("dev.txt")

    def test_cfg_history_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        hist = cfg_history_path(str(config_path))
        assert hist.endswith(".cmd_history.txt")

    def test_cfg_plugins_dir(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        d = cfg_plugins_dir(str(config_path))
        assert d.name == "plugins"
        assert d.is_dir()


# -- DEFAULT_CFG structure --------------------------------------------------


class TestDefaultCfg:
    def test_has_custom_buttons(self):
        assert "custom_buttons" in DEFAULT_CFG
        assert isinstance(DEFAULT_CFG["custom_buttons"], list)
        assert len(DEFAULT_CFG["custom_buttons"]) == 4

    def test_custom_buttons_all_disabled(self):
        for btn in DEFAULT_CFG["custom_buttons"]:
            assert btn["enabled"] is False

    def test_custom_buttons_have_required_fields(self):
        for btn in DEFAULT_CFG["custom_buttons"]:
            assert "enabled" in btn
            assert "name" in btn
            assert "command" in btn
            assert "tooltip" in btn

    def test_has_essential_keys(self):
        for key in ("port", "baudrate", "line_ending", "repl_prefix"):
            assert key in DEFAULT_CFG


# -- load_config ------------------------------------------------------------


class TestLoadConfig:
    def test_creates_default_if_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config_path = tmp_path / "test" / "test.json"
        cfg = load_config(str(config_path))
        assert config_path.exists()
        assert cfg["port"] == DEFAULT_CFG["port"]
        assert "custom_buttons" in cfg

    def test_adds_missing_keys(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.json"
        config_path.parent.mkdir()
        # Write a minimal config without custom_buttons
        minimal = {"port": "COM3", "baudrate": 9600}
        config_path.write_text(json.dumps(minimal))
        cfg = load_config(str(config_path))
        assert cfg["port"] == "COM3"  # original preserved
        assert "custom_buttons" in cfg  # default added
        # Verify it was persisted
        saved = json.loads(config_path.read_text())
        assert "custom_buttons" in saved

    def test_does_not_overwrite_existing_keys(self, tmp_path):
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
        cfg = load_config(str(config_path))
        assert cfg["port"] == "COM7"
        assert len(cfg["custom_buttons"]) == 1
        assert cfg["custom_buttons"][0]["enabled"] is True


# -- _SCRIPT_TEMPLATE -------------------------------------------------------


class TestScriptTemplate:
    def test_has_placeholder(self):
        result = _SCRIPT_TEMPLATE.format(name="test_script")
        assert "test_script" in result

    def test_has_comments(self):
        result = _SCRIPT_TEMPLATE.format(name="x")
        lines = result.strip().splitlines()
        assert all(line.startswith("#") for line in lines if line.strip())

    def test_has_example_commands(self):
        result = _SCRIPT_TEMPLATE.format(name="x")
        assert "!!sleep" in result or "!!" in result


# -- Custom button config validation ----------------------------------------


class TestCustomButtonConfig:
    def test_enabled_filter(self):
        """Simulate the enabled filter used in compose."""
        buttons = [
            {"enabled": True, "name": "A", "command": "cmd1", "tooltip": "t1"},
            {"enabled": False, "name": "B", "command": "cmd2", "tooltip": "t2"},
            {"enabled": True, "name": "C", "command": "cmd3", "tooltip": "t3"},
        ]
        enabled = [b for b in buttons if b.get("enabled", False)]
        assert len(enabled) == 2
        assert enabled[0]["name"] == "A"
        assert enabled[1]["name"] == "C"

    def test_missing_enabled_defaults_false(self):
        buttons = [{"name": "X", "command": "cmd", "tooltip": "tip"}]
        enabled = [b for b in buttons if b.get("enabled", False)]
        assert len(enabled) == 0

    def test_command_split(self):
        """Simulate the \\n split used in _run_custom_button."""
        raw = "ATZ\\nAT+INFO\\n!!sleep 500ms"
        parts = [c.strip() for c in raw.split("\\n") if c.strip()]
        assert parts == ["ATZ", "AT+INFO", "!!sleep 500ms"]

    def test_command_split_single(self):
        raw = "ATZ"
        parts = [c.strip() for c in raw.split("\\n") if c.strip()]
        assert parts == ["ATZ"]

    def test_command_split_empty(self):
        raw = ""
        parts = [c.strip() for c in raw.split("\\n") if c.strip()]
        assert parts == []

    def test_repl_prefix_detection(self):
        prefix = "!!"
        assert "!!run test.run".startswith(prefix)
        assert not "ATZ".startswith(prefix)
