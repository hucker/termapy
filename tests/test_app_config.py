"""Tests for app.py config utilities, custom buttons, and script editor."""

import json
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG, SCRIPT_TEMPLATE
from termapy.config import (
    cfg_data_dir,
    cfg_history_path,
    cfg_log_path,
    cfg_path_for_name,
    cfg_plugins_dir,
    expand_env_cfg,
    expand_env_str,
    load_config,
    migrate_json_to_cfg,
    validate_config,
)


# -- cfg_data_dir: subdirectory creation ------------------------------------


class TestCfgDataDir:
    def test_creates_subdirs(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual == config_path.parent  # returns parent directory
        for sub in ("plugin", "ss", "run"):
            assert (actual / sub).is_dir()  # all subdirs created

    def test_idempotent(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        cfg_data_dir(str(config_path))
        cfg_data_dir(str(config_path))  # second call should not error

        # Assert
        assert (config_path.parent / "ss").is_dir()  # subdirs still exist

    def test_creates_parent_if_needed(self, tmp_path):
        # Arrange
        config_path = tmp_path / "new" / "new.cfg"

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual.exists()  # parent dir created
        assert (actual / "plugin").is_dir()  # subdirs created


# -- cfg helper functions ---------------------------------------------------


class TestCfgHelpers:
    def test_cfg_path_for_name(self):
        actual = cfg_path_for_name("mydev")
        assert actual.name == "mydev.cfg"  # filename matches
        assert actual.parent.name == "mydev"  # parent dir matches

    def test_cfg_log_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_log_path(str(config_path))
        assert actual.endswith("dev.log")  # log named after config

    def test_cfg_history_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_history_path(str(config_path))
        assert actual.endswith(".cmd_history.txt")  # history file pattern

    def test_cfg_plugins_dir(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_plugins_dir(str(config_path))
        assert actual.name == "plugin"  # correct subdir name
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
        assert info_btn["command"] == "/cfg.info"  # runs /cfg.info

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
        for key in ("port", "baud_rate", "line_ending", "cmd_prefix"):
            assert key in DEFAULT_CFG  # essential config key present


# -- load_config ------------------------------------------------------------


class TestLoadConfig:
    def test_raises_if_missing(self, tmp_path):
        # Arrange
        config_path = tmp_path / "test" / "test.cfg"

        # Act / Assert - load_config no longer auto-creates files
        import pytest
        with pytest.raises(FileNotFoundError):
            load_config(str(config_path))

    def test_adds_missing_keys(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        minimal = {"port": "COM3", "baud_rate": 9600}
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
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        custom = {
            "port": "COM7",
            "baud_rate": 9600,
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


# -- SCRIPT_TEMPLATE -------------------------------------------------------


class TestScriptTemplate:
    def test_has_placeholder(self):
        actual = SCRIPT_TEMPLATE.format(name="test_script")
        assert "test_script" in actual  # name placeholder expanded

    def test_has_comments(self):
        actual = SCRIPT_TEMPLATE.format(name="x")
        lines = actual.strip().splitlines()
        assert all(
            line.startswith("#") for line in lines if line.strip()
        )  # all lines are comments

    def test_has_example_commands(self):
        actual = SCRIPT_TEMPLATE.format(name="x")
        assert "/sleep" in actual  # contains REPL example


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
        raw = "ATZ\\nAT+INFO\\n/sleep 500ms"
        expected = ["ATZ", "AT+INFO", "/sleep 500ms"]

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
        prefix = "/"
        assert "/run test.run".startswith(prefix)  # REPL command detected
        assert not "ATZ".startswith(prefix)  # serial command not matched


# -- expand_env_str / expand_env_cfg ----------------------------------------


class TestExpandEnvStr:
    def test_known_var(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("TEST_PORT_XYZ", "COM7")

        # Act
        actual = expand_env_str("$(env.TEST_PORT_XYZ)")

        # Assert
        expected = "COM7"
        assert actual == expected  # known var expanded

    def test_fallback_when_missing(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("MISSING_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.MISSING_CFG_VAR|COM4)")

        # Assert
        expected = "COM4"
        assert actual == expected  # fallback used

    def test_unknown_without_fallback_unchanged(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("NOPE_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.NOPE_CFG_VAR)")

        # Assert
        expected = "$(env.NOPE_CFG_VAR)"
        assert actual == expected  # left unchanged (no crash)

    def test_plain_string_unchanged(self):
        # Act
        actual = expand_env_str("COM4")

        # Assert
        expected = "COM4"
        assert actual == expected  # no placeholder, unchanged


class TestExpandEnvCfg:
    def test_expands_string_values(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("CFG_PORT_TEST", "COM9")
        cfg = {"port": "$(env.CFG_PORT_TEST|COM1)", "baud_rate": 115200}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["port"] == "COM9"  # string value expanded
        assert actual["baud_rate"] == 115200  # non-string untouched

    def test_skips_non_strings(self):
        # Arrange
        cfg = {"baud_rate": 9600, "auto_connect": True, "max_lines": 10000}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["baud_rate"] == 9600  # int unchanged
        assert actual["auto_connect"] is True  # bool unchanged


class TestLoadConfigEnvExpansion:
    def test_expands_env_in_loaded_config(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.setenv("LC_TEST_PORT", "COM8")
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        raw = {"port": "$(env.LC_TEST_PORT|COM1)", "baud_rate": 9600}
        config_path.write_text(json.dumps(raw))

        # Act
        actual = load_config(str(config_path))

        # Assert
        assert actual["port"] == "COM8"  # env var expanded in memory

    def test_disk_keeps_template(self, tmp_path, monkeypatch):
        # Arrange
        monkeypatch.setenv("LC_TEST_PORT2", "COM8")
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        template = "$(env.LC_TEST_PORT2|COM1)"
        raw = {"port": template, "baud_rate": 9600}
        config_path.write_text(json.dumps(raw))

        # Act
        load_config(str(config_path))

        # Assert
        actual_disk = json.loads(config_path.read_text())
        assert actual_disk["port"] == template  # disk keeps raw template


# -- migrate_json_to_cfg -----------------------------------------------------


class TestMigrateJsonToCfg:
    def test_renames_json_to_cfg(self, tmp_path):
        # Arrange
        sub = tmp_path / "foo"
        sub.mkdir()
        json_file = sub / "foo.json"
        json_file.write_text('{"port": "COM1"}')

        # Act
        migrate_json_to_cfg(tmp_path)

        # Assert
        assert (sub / "foo.cfg").exists()  # .cfg file created
        assert not json_file.exists()  # .json file removed

    def test_skips_when_cfg_exists(self, tmp_path):
        # Arrange
        sub = tmp_path / "bar"
        sub.mkdir()
        json_file = sub / "bar.json"
        json_file.write_text('{"port": "COM1"}')
        cfg_file = sub / "bar.cfg"
        cfg_file.write_text('{"port": "COM2"}')

        # Act
        migrate_json_to_cfg(tmp_path)

        # Assert
        assert json_file.exists()  # .json not removed (conflict)
        actual = json.loads(cfg_file.read_text())
        assert actual["port"] == "COM2"  # .cfg not overwritten

    def test_idempotent(self, tmp_path):
        # Arrange
        sub = tmp_path / "baz"
        sub.mkdir()
        json_file = sub / "baz.json"
        json_file.write_text('{"port": "COM1"}')

        # Act
        migrate_json_to_cfg(tmp_path)
        migrate_json_to_cfg(tmp_path)  # second call is no-op

        # Assert
        assert (sub / "baz.cfg").exists()  # .cfg still exists


# -- validate_config: serial port setting validation --------------------------


class TestValidateConfig:
    def test_default_cfg_passes(self):
        # Act
        actual = validate_config(dict(DEFAULT_CFG))

        # Assert
        assert actual == []  # no warnings for defaults

    def test_invalid_byte_size(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, byte_size=9)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "byte_size" in actual[0]  # identifies the field
        assert "9" in actual[0]  # shows the bad value

    def test_invalid_parity(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, parity="X")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "parity" in actual[0]  # identifies the field

    def test_invalid_stop_bits(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, stop_bits=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "stop_bits" in actual[0]  # identifies the field

    def test_invalid_flow_control(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, flow_control="bad")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "flow_control" in actual[0]  # identifies the field

    def test_nonstandard_baud_rate_warns(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, baud_rate=250000)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # warns but doesn't reject
        assert "not a standard rate" in actual[0]  # clear message

    def test_standard_baud_rate_ok(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, baud_rate=9600)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == []  # no warnings

    def test_negative_baud_rate(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "positive" in actual[0]  # clear message

    def test_baud_rate_wrong_type(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, baud_rate="fast")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "expected int" in actual[0]  # type error message

    def test_invalid_encoding(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, encoding="not-a-codec")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "encoding" in actual[0]  # identifies the field

    def test_valid_encoding(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, encoding="ascii")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == []  # no warnings

    def test_negative_cmd_delay_ms(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, cmd_delay_ms=-10)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "cmd_delay_ms" in actual[0]  # identifies the field

    def test_zero_max_lines(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, max_lines=0)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "max_lines" in actual[0]  # identifies the field

    def test_unknown_key_flagged(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, baudrate=9600)  # typo: should be baud_rate

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "unknown key" in actual[0]  # clear message
        assert "baudrate" in actual[0]  # shows the bad key

    def test_internal_keys_ignored(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, _migrated_from=5, _config_warnings=[])

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == []  # internal keys not flagged

    def test_multiple_errors(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, byte_size=99, parity="Z", baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 3  # one warning per bad field

    def test_old_config_version_warns(self):
        # Arrange
        cfg = dict(DEFAULT_CFG, config_version=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1  # exactly one warning
        assert "config_version" in actual[0]  # identifies the field
        assert "3" in actual[0]  # shows the old version

    def test_current_config_version_ok(self):
        # Arrange
        cfg = dict(DEFAULT_CFG)  # uses CURRENT_CONFIG_VERSION

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == []  # no warnings


# -- load_config: malformed JSON handling -------------------------------------


class TestLoadConfigMalformed:
    def test_malformed_json_raises(self, tmp_path):
        # Arrange
        cfg_file = tmp_path / "bad" / "bad.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text("{not valid json!!}")

        # Act / Assert -- ValueError with line/column detail
        with pytest.raises(ValueError, match="Invalid JSON at line"):
            load_config(str(cfg_file))


# -- _run_check: CLI --check flag --------------------------------------------


class TestRunCheck:
    def _run(self, *args):
        """Run termapy --check via subprocess and return (returncode, stdout)."""
        import subprocess

        result = subprocess.run(
            ["uv", "run", "termapy", "--check", *args],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout

    def test_valid_config_ok(self, tmp_path):
        # Arrange
        cfg_file = tmp_path / "ok" / "ok.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text(json.dumps(dict(DEFAULT_CFG)))

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 0  # success exit code
        assert actual["status"] == "ok"  # no warnings

    def test_invalid_baud_warns(self, tmp_path):
        # Arrange
        cfg = dict(DEFAULT_CFG, baud_rate=999)
        cfg_file = tmp_path / "bad" / "bad.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text(json.dumps(cfg))

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 0  # still exits 0 (warnings, not errors)
        assert actual["status"] == "warn"  # flagged as warn
        assert any("baud_rate" in w for w in actual["warnings"])  # identifies field

    def test_malformed_json_errors(self, tmp_path):
        # Arrange
        cfg_file = tmp_path / "bad" / "bad.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text("{broken json!}")

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 1  # error exit code
        assert actual["status"] == "error"  # parse failure

    def test_does_not_modify_file(self, tmp_path):
        # Arrange — config with old version, check should NOT migrate it
        cfg = dict(DEFAULT_CFG, config_version=3)
        cfg_file = tmp_path / "old" / "old.cfg"
        cfg_file.parent.mkdir()
        original = json.dumps(cfg)
        cfg_file.write_text(original)

        # Act
        self._run(str(cfg_file))

        # Assert
        actual = cfg_file.read_text()
        assert actual == original  # file unchanged (read-only check)
