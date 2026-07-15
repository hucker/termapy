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
    open_serial,
    validate_config,
)

pytestmark = pytest.mark.slow  # subprocess-based config-load tests


# DEFAULT_CFG has an empty ``port`` field because the zero-config CLI
# needs somewhere to synthesize its in-memory cfg from, but any cfg
# actually validated or loaded from disk must name a port.  Tests that
# build minimal cfgs with ``_cfg_with(...)`` inherit a valid DEMO port
# so the one-warning-at-a-time assertions below aren't clouded by an
# incidental empty-port warning.  Callers that want to test the port
# field specifically override it via ``port=...``.
#
# Post-v22, pyserial keys (port, baud_rate, byte_size, parity,
# stop_bits, flow_control, custom_baud) live nested under
# ``cfg["serial"]``.  The helper below routes serial-key overrides
# into the sub-dict and leaves other overrides (encoding, cmd_delay_ms,
# config_version, etc.) at the top level, so tests can write
# ``_cfg_with(byte_size=9)`` without thinking about the nesting.
_SERIAL_KEYS = frozenset({
    "port", "baud_rate", "custom_baud", "byte_size",
    "parity", "stop_bits", "flow_control",
})


def _cfg_with(**overrides) -> dict:
    """Build a test cfg, routing serial keys into ``cfg['serial']``."""
    serial_overrides = {
        k: overrides.pop(k) for k in list(overrides) if k in _SERIAL_KEYS
    }
    default_serial = DEFAULT_CFG["serial"]
    assert isinstance(default_serial, dict), "DEFAULT_CFG['serial'] is a dict"
    return {
        **DEFAULT_CFG,
        "serial": {**default_serial, "port": "DEMO", **serial_overrides},
        **overrides,
    }


# -- cfg_data_dir: subdirectory creation ------------------------------------


class TestCfgDataDir:
    def test_creates_subdirs(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual == config_path.parent, "returns parent directory"
        for sub in ("plugin", "ss", "run"):
            assert (actual / sub).is_dir(), f"all subdirs created: {sub}"

    def test_idempotent(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        cfg_data_dir(str(config_path))
        cfg_data_dir(str(config_path))  # second call should not error

        # Assert
        assert (config_path.parent / "ss").is_dir(), "subdirs still exist"

    def test_creates_parent_if_needed(self, tmp_path):
        # Arrange
        config_path = tmp_path / "new" / "new.cfg"

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual.exists(), "parent dir created"
        assert (actual / "plugin").is_dir(), "subdirs created"

    def test_refuses_bundled_path(self):
        """``cfg_data_dir`` must refuse paths inside the installed
        termapy package tree (the bundled ``builtins/demo/demo.cfg``
        was the historical footgun -- live-activating it polluted
        the source tree with empty ``cap/``, ``ss/``, ``prof/`` dirs
        and a ``.gitignore``)."""
        # Arrange -- the bundled demo cfg is the canonical example;
        # detection works for any path inside termapy.* regardless.
        import importlib.resources

        pkg_root = Path(str(importlib.resources.files("termapy")))
        bundled_cfg = pkg_root / "builtins" / "demo" / "demo.cfg"
        assert bundled_cfg.exists(), (
            "precondition: bundled demo.cfg is the fixture under test"
        )

        # Act / Assert -- direct activation refused with a useful error.
        with pytest.raises(RuntimeError) as exc_info:
            cfg_data_dir(str(bundled_cfg))
        msg = str(exc_info.value)
        assert "Refusing" in msg, "error names the action"
        assert str(bundled_cfg) in msg, "error names the offending path"

    def test_error_message_mentions_demo_recovery(self):
        """The refusal must teach the user the right alternative
        (``--demo`` to copy the template) so the UX promise is pinned."""
        # Arrange
        import importlib.resources

        pkg_root = Path(str(importlib.resources.files("termapy")))
        bundled_cfg = pkg_root / "builtins" / "demo" / "demo.cfg"

        # Act
        with pytest.raises(RuntimeError) as exc_info:
            cfg_data_dir(str(bundled_cfg))
        msg = str(exc_info.value)

        # Assert -- the message must point at both recovery paths.
        assert "--demo" in msg, "error suggests --demo as the fix"
        assert "--cfg-dir" in msg, "error mentions --cfg-dir as alternative"


# -- cfg helper functions ---------------------------------------------------


class TestCfgHelpers:
    def test_cfg_path_for_name(self):
        actual = cfg_path_for_name("mydev")
        assert actual.name == "mydev.cfg", "filename matches"
        assert actual.parent.name == "mydev", "parent dir matches"

    def test_cfg_log_path(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_log_path(str(config_path))
        assert actual.endswith("dev.log"), "log named after config"

    def test_cfg_history_path(self, tmp_path):
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        actual = cfg_history_path(str(config_path))

        # Assert -- per-config history is <stem>.history NEXT TO the cfg
        # (the path both hosts have always written)
        expected = str(config_path.parent / "dev.history")
        assert actual == expected, "per-config history path"

    def test_cfg_history_path_no_config_falls_back(self):
        # Act -- no config loaded: fallback file in the global cfg root
        actual = cfg_history_path("")

        # Assert
        assert actual.endswith(".cmd_history.txt"), "fallback filename"

    def test_cfg_plugins_dir(self, tmp_path):
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_plugins_dir(str(config_path))
        assert actual.name == "plugin", "correct subdir name"
        assert actual.is_dir(), "directory created"


# -- DEFAULT_CFG structure --------------------------------------------------


def _custom_buttons() -> list[dict]:
    """Return the custom_buttons list with a narrowed type for ty.

    DEFAULT_CFG is a mixed-value-type dict literal (int, str, bool, list),
    so subscripting returns a union and ty can't prove it's a list.  This
    helper asserts the type once and hands back a plain list[dict] so the
    tests below read naturally without scattering isinstance narrows.
    """
    buttons = DEFAULT_CFG["custom_buttons"]
    assert isinstance(buttons, list), "custom_buttons is a list"
    return buttons


class TestDefaultCfg:
    def test_has_custom_buttons(self):
        assert "custom_buttons" in DEFAULT_CFG, "key exists"
        buttons = _custom_buttons()
        assert len(buttons) >= 4, "at least 4 button placeholders"

    def test_custom_buttons_info_enabled(self):
        """First default button is the Info button (enabled)."""
        info_btn = _custom_buttons()[0]
        assert info_btn["enabled"] is True, "Info button enabled"
        assert info_btn["name"] == "Info", "named Info"
        assert info_btn["command"] == "/cfg.info", "runs /cfg.info"

    def test_custom_buttons_placeholders_disabled(self):
        """Remaining default buttons are disabled placeholders."""
        for btn in _custom_buttons()[1:]:
            assert btn["enabled"] is False, "placeholder disabled"

    def test_custom_buttons_have_required_fields(self):
        for btn in _custom_buttons():
            assert "enabled" in btn, "enabled field present"
            assert "name" in btn, "name field present"
            assert "command" in btn, "command field present"
            assert "tooltip" in btn, "tooltip field present"

    def test_has_essential_keys(self):
        # Post-v22, port and baud_rate live nested under cfg["serial"]
        # while line_ending and cmd_prefix stay at the top level.
        for key in ("eol", "cmd_prefix"):
            assert key in DEFAULT_CFG, (
                f"essential top-level key present: {key}"
            )
        assert "serial" in DEFAULT_CFG, "serial sub-dict present"
        serial = DEFAULT_CFG["serial"]
        assert isinstance(serial, dict), "serial is a dict"
        for key in ("port", "baud_rate"):
            assert key in serial, f"essential serial key present: {key}"


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
        # Arrange -- minimal cfg in the pre-v22 flat shape; load_config
        # should migrate it forward so port lives at cfg["serial"]["port"].
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        minimal = {"port": "COM3", "baud_rate": 9600}
        config_path.write_text(json.dumps(minimal))

        # Act
        actual = load_config(str(config_path))

        # Assert -- original value preserved at its migrated location
        assert actual["serial"]["port"] == "COM3", (
            "original port preserved at migrated location"
        )
        assert "custom_buttons" in actual, "missing default added"
        actual_saved = json.loads(config_path.read_text())
        assert "custom_buttons" in actual_saved, "persisted to disk"

    def test_does_not_overwrite_existing_keys(self, tmp_path):
        # Arrange -- pre-v22 flat cfg; load_config migrates port to
        # cfg["serial"]["port"] but leaves custom_buttons alone.
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
        assert actual["serial"]["port"] == "COM7", (
            "custom port preserved at migrated location"
        )
        assert len(actual["custom_buttons"]) == 1, "custom buttons not replaced"
        assert actual["custom_buttons"][0]["enabled"] is True, "custom value kept"

    def test_backfilled_mutable_default_is_deep_copied(self, tmp_path):
        # Arrange -- a config missing custom_buttons; the backfill must
        # deep-copy the default list, not alias the module-global DEFAULT_CFG
        # (otherwise an in-session edit would corrupt the process-wide default).
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({"port": "COM3", "baud_rate": 9600}))

        # Act
        actual = load_config(str(config_path))

        # Assert -- distinct object, and mutating it cannot reach DEFAULT_CFG
        assert actual["custom_buttons"] is not DEFAULT_CFG["custom_buttons"], \
            "backfilled list is a copy, not the module-global default"
        expected_default_len = len(DEFAULT_CFG["custom_buttons"])
        actual["custom_buttons"].append(
            {"enabled": True, "name": "X", "command": "X", "tooltip": ""}
        )
        actual_default_len = len(DEFAULT_CFG["custom_buttons"])
        assert actual_default_len == expected_default_len, \
            "mutating the loaded config must not change DEFAULT_CFG"


# -- SCRIPT_TEMPLATE -------------------------------------------------------


class TestScriptTemplate:
    def test_has_placeholder(self):
        actual = SCRIPT_TEMPLATE.format(name="test_script")
        assert "test_script" in actual, "name placeholder expanded"

    def test_has_comments(self):
        actual = SCRIPT_TEMPLATE.format(name="x")
        lines = actual.strip().splitlines()
        assert all(
            line.startswith("#") for line in lines if line.strip()
        ), "all lines are comments"

    def test_has_example_commands(self):
        actual = SCRIPT_TEMPLATE.format(name="x")
        assert "/delay" in actual, "contains REPL example"


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
        assert len(actual) == 2, "only enabled buttons returned"
        assert actual[0]["name"] == "A", "first enabled button"
        assert actual[1]["name"] == "C", "second enabled button"

    def test_missing_enabled_defaults_false(self):
        # Arrange
        buttons = [{"name": "X", "command": "cmd", "tooltip": "tip"}]

        # Act
        actual = [b for b in buttons if b.get("enabled", False)]

        # Assert
        assert len(actual) == 0, "missing enabled treated as False"

    def test_command_split(self):
        """Simulate the \\n split used in _run_custom_button."""
        # Arrange
        raw = "ATZ\\nAT+INFO\\n/delay 500ms"
        expected = ["ATZ", "AT+INFO", "/delay 500ms"]

        # Act
        actual = [c.strip() for c in raw.split("\\n") if c.strip()]

        # Assert
        assert actual == expected, "multi-command split correctly"

    def test_command_split_single(self):
        actual = [c.strip() for c in "ATZ".split("\\n") if c.strip()]
        assert actual == ["ATZ"], "single command unchanged"

    def test_command_split_empty(self):
        actual = [c.strip() for c in "".split("\\n") if c.strip()]
        assert actual == [], "empty string yields empty list"

    def test_repl_prefix_detection(self):
        prefix = "/"
        assert "/run test.run".startswith(prefix), "REPL command detected"
        assert not "ATZ".startswith(prefix), "serial command not matched"


# -- expand_env_str / expand_env_cfg ----------------------------------------


class TestExpandEnvStr:
    def test_known_var(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("TEST_PORT_XYZ", "COM7")

        # Act
        actual = expand_env_str("$(env.TEST_PORT_XYZ)")

        # Assert
        expected = "COM7"
        assert actual == expected, "known var expanded"

    def test_fallback_when_missing(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("MISSING_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.MISSING_CFG_VAR|COM4)")

        # Assert
        expected = "COM4"
        assert actual == expected, "fallback used"

    def test_unknown_without_fallback_unchanged(self, monkeypatch):
        # Arrange
        monkeypatch.delenv("NOPE_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.NOPE_CFG_VAR)")

        # Assert
        expected = "$(env.NOPE_CFG_VAR)"
        assert actual == expected, "left unchanged (no crash)"

    def test_plain_string_unchanged(self):
        # Act
        actual = expand_env_str("COM4")

        # Assert
        expected = "COM4"
        assert actual == expected, "no placeholder, unchanged"


class TestExpandEnvCfg:
    def test_expands_string_values(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("CFG_PORT_TEST", "COM9")
        cfg = {"port": "$(env.CFG_PORT_TEST|COM1)", "baud_rate": 115200}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["port"] == "COM9", "string value expanded"
        assert actual["baud_rate"] == 115200, "non-string untouched"

    def test_skips_non_strings(self):
        # Arrange
        cfg = {"baud_rate": 9600, "auto_connect": True, "max_lines": 10000}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["baud_rate"] == 9600, "int unchanged"
        assert actual["auto_connect"] is True, "bool unchanged"

    def test_skips_connect_command_keys(self, monkeypatch):
        # Arrange -- the *_on_connect_cmd keys are dispatched through the REPL
        # on connect, where the env gate applies per command (repl commands
        # expand $(env.X), bare device text does not).  Pre-expanding them
        # here would put env values on the wire for device commands -- the
        # $(env.PATH) incident class -- so they must be left literal.
        monkeypatch.setenv("OCC_TEST", "SECRET")
        cfg = {
            "on_connect_cmd": "AT+TOKEN=$(env.OCC_TEST)",
            "tui_on_connect_cmd": "$(env.OCC_TEST)",
            "cli_on_connect_cmd": "$(env.OCC_TEST)",
            "mcp_on_connect_cmd": "$(env.OCC_TEST)",
            "log_file": "$(env.OCC_TEST).log",
        }

        # Act
        actual = expand_env_cfg(cfg)

        # Assert -- connect-command keys left literal; ordinary values expand
        assert actual["on_connect_cmd"] == "AT+TOKEN=$(env.OCC_TEST)", \
            "on_connect_cmd not pre-expanded (env gated at dispatch)"
        assert actual["tui_on_connect_cmd"] == "$(env.OCC_TEST)", \
            "tui_on_connect_cmd not pre-expanded"
        assert actual["cli_on_connect_cmd"] == "$(env.OCC_TEST)", \
            "cli_on_connect_cmd not pre-expanded"
        assert actual["mcp_on_connect_cmd"] == "$(env.OCC_TEST)", \
            "mcp_on_connect_cmd not pre-expanded"
        assert actual["log_file"] == "SECRET.log", \
            "ordinary cfg values still expand"


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

        # Assert -- after migration, port lives at cfg["serial"]["port"].
        assert actual["serial"]["port"] == "COM8", (
            "env var expanded in memory after migration"
        )

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

        # Assert -- load_config persists the migrated shape back to
        # disk, so port lives nested.  The raw env-var template must
        # survive the round-trip (NOT expanded on disk).
        actual_disk = json.loads(config_path.read_text())
        assert actual_disk["serial"]["port"] == template, (
            "disk keeps raw template at migrated location"
        )


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
        assert (sub / "foo.cfg").exists(), ".cfg file created"
        assert not json_file.exists(), ".json file removed"

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
        assert json_file.exists(), ".json not removed (conflict)"
        actual = json.loads(cfg_file.read_text())
        assert actual["port"] == "COM2", ".cfg not overwritten"

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
        assert (sub / "baz.cfg").exists(), ".cfg still exists"

    def test_only_exact_name_json_is_recognized(self, tmp_path):
        """Regression: the migration must ONLY recognize <name>/<name>.json.

        Termapy's naming invariant is that a config file is exactly
        ``<folder>/<folder>.cfg`` -- nothing else.  Earlier the migration
        globbed ``*/*.json`` and renamed any ``.json`` it found, which
        silently corrupted profile files (``<name>.profile.json`` ->
        ``<name>.profile.cfg``) every time the host started.  This test
        drops several plausible filenames in a fixture folder and
        asserts only the exact-match cfg file is touched.
        """
        # Arrange -- folder with one real cfg file and several others
        # that share the .json extension but are NOT config files.
        sub = tmp_path / "device_x"
        sub.mkdir()
        (sub / "device_x.json").write_text('{"port": "COM1"}')   # the cfg
        (sub / "device_x.profile.json").write_text('{"v": 2}')    # profile
        (sub / "device_x.schema.json").write_text('{"$ref": ""}')  # future schema
        (sub / "ad_hoc.json").write_text('{"misc": true}')        # user's drop

        # Act
        migrate_json_to_cfg(tmp_path)

        # Assert -- only the exact-match cfg file got renamed.
        actual_cfg = (sub / "device_x.cfg").exists()
        actual_json_gone = not (sub / "device_x.json").exists()
        actual_profile = (sub / "device_x.profile.json").exists()
        actual_schema = (sub / "device_x.schema.json").exists()
        actual_adhoc = (sub / "ad_hoc.json").exists()
        no_bogus_cfg = not (sub / "device_x.profile.cfg").exists()
        assert actual_cfg, "device_x.json -> device_x.cfg (the cfg file)"
        assert actual_json_gone, "original device_x.json removed"
        assert actual_profile, (
            "device_x.profile.json must NOT be migrated -- it's a profile, "
            "not a cfg file (this is the bug the test guards against)"
        )
        assert actual_schema, "device_x.schema.json must NOT be migrated"
        assert actual_adhoc, "ad_hoc.json must NOT be migrated"
        assert no_bogus_cfg, (
            "no bogus <name>.profile.cfg file -- migration must not "
            "produce filenames that violate the strict naming convention"
        )


# -- validate_config: serial port setting validation --------------------------


class TestValidateConfig:
    def test_default_cfg_passes(self):
        # Act
        actual = validate_config(_cfg_with())

        # Assert
        assert actual == [], "no warnings for defaults"

    def test_invalid_byte_size(self):
        # Arrange
        cfg = _cfg_with(byte_size=9)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "byte_size" in actual[0], "identifies the field"
        assert "9" in actual[0], "shows the bad value"

    def test_invalid_parity(self):
        # Arrange
        cfg = _cfg_with(parity="X")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "parity" in actual[0], "identifies the field"

    def test_invalid_stop_bits(self):
        # Arrange
        cfg = _cfg_with(stop_bits=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "stop_bits" in actual[0], "identifies the field"

    def test_invalid_flow_control(self):
        # Arrange
        cfg = _cfg_with(flow_control="bad")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "flow_control" in actual[0], "identifies the field"

    def test_nonstandard_baud_rate_warns(self):
        # Arrange
        cfg = _cfg_with(baud_rate=250000)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns but doesn't reject"
        assert "not a standard rate" in actual[0], "clear message"
        assert "custom_baud" in actual[0], "tells user how to fix"

    def test_custom_baud_accepts_nonstandard(self):
        # Arrange
        cfg = _cfg_with(baud_rate=625000, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings with custom_baud enabled"

    def test_custom_baud_rejects_below_300(self):
        # Arrange
        cfg = _cfg_with(baud_rate=150, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns even with custom_baud"
        assert "300" in actual[0], "mentions minimum"

    def test_custom_baud_accepts_300(self):
        # Arrange
        cfg = _cfg_with(baud_rate=300, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "300 is accepted"

    def test_custom_baud_false_still_warns(self):
        # Arrange
        cfg = _cfg_with(baud_rate=625000, custom_baud=False)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns when custom_baud is False"
        assert "not a standard rate" in actual[0], "standard warning"

    def test_custom_baud_wrong_type(self):
        # Arrange
        cfg = _cfg_with(custom_baud="yes")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "expected bool" in actual[0], "type error"

    def test_standard_baud_rate_ok(self):
        # Arrange
        cfg = _cfg_with(baud_rate=9600)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_negative_baud_rate(self):
        # Arrange
        cfg = _cfg_with(baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "positive" in actual[0], "clear message"

    def test_baud_rate_wrong_type(self):
        # Arrange
        cfg = _cfg_with(baud_rate="fast")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "expected int" in actual[0], "type error message"

    def test_invalid_encoding(self):
        # Arrange
        cfg = _cfg_with(encoding="not-a-codec")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "encoding" in actual[0], "identifies the field"

    def test_valid_encoding(self):
        # Arrange
        cfg = _cfg_with(encoding="ascii")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_negative_cmd_delay_ms(self):
        # Arrange
        cfg = _cfg_with(cmd_delay_ms=-10)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "cmd_delay_ms" in actual[0], "identifies the field"

    def test_zero_max_lines(self):
        # Arrange
        cfg = _cfg_with(max_lines=0)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "max_lines" in actual[0], "identifies the field"

    def test_unknown_key_flagged(self):
        # Arrange -- a key that never existed, not a deprecated one.
        cfg = _cfg_with(not_a_real_key=9600)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "unknown key" in actual[0], "clear message"
        assert "not_a_real_key" in actual[0], "shows the bad key"

    def test_deprecated_renamed_key_flagged_with_hint(self):
        # Arrange -- 'baudrate' was renamed to 'baud_rate' in v4.
        cfg = _cfg_with(baudrate=9600)

        # Act
        actual = validate_config(cfg)

        # Assert -- user gets told it's deprecated *and* what it was
        # renamed to, instead of the generic "typo?" message.
        assert len(actual) == 1, "exactly one warning"
        assert "deprecated key" in actual[0], "flagged as deprecated"
        assert "baudrate" in actual[0], "shows the stale key"
        assert "baud_rate" in actual[0], "names the replacement"
        assert "v4" in actual[0], "cites the version that retired it"

    def test_deprecated_removed_key_flagged_with_hint(self):
        # Arrange -- 'cap_endian' was removed outright in v8.
        cfg = _cfg_with(cap_endian="little")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "deprecated key" in actual[0], "flagged as deprecated"
        assert "cap_endian" in actual[0], "shows the stale key"
        assert "removed in v8" in actual[0], "explains what happened"

    def test_internal_keys_ignored(self):
        # Arrange
        cfg = _cfg_with(_migrated_from=5, _config_warnings=[])

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "internal keys not flagged"

    def test_older_config_version_warns_generic(self):
        # Arrange -- a cfg with an older schema (migration will run
        # to bring it forward; the warning here is informational).
        from termapy.migration import CURRENT_CONFIG_VERSION
        cfg = _cfg_with(config_version=CURRENT_CONFIG_VERSION - 1)

        # Act
        actual = validate_config(cfg)

        # Assert -- exactly the existing "config_version: X" warning.
        assert any("config_version:" in w for w in actual), (
            "older cfg gets the informational version warning"
        )
        assert not any("upgrade" in w.lower() for w in actual), (
            "no upgrade suggestion for older cfgs (we can migrate forward)"
        )

    def test_newer_config_version_suggests_upgrade(self):
        # Arrange -- a cfg from a future termapy that the current
        # one can't migrate.  This is the misleading "unknown key
        # (typo?)" scenario the change fixes.
        from termapy.migration import CURRENT_CONFIG_VERSION
        cfg = _cfg_with(config_version=CURRENT_CONFIG_VERSION + 1)

        # Act
        actual = validate_config(cfg)

        # Assert -- one clear warning with an actionable upgrade hint.
        assert len(actual) == 1, (
            f"only the upgrade warning, no per-key noise; got {actual!r}"
        )
        msg = actual[0]
        assert "newer termapy" in msg, "explains the cause"
        assert "Upgrade with:" in msg, "names a specific upgrade command"
        assert "termapy" in msg, "command targets the termapy package"
        assert "restart termapy" in msg.lower(), (
            "tells the user to restart after upgrade"
        )

    def test_newer_config_suppresses_unknown_key_noise(self):
        # Arrange -- a future cfg with fields the current termapy
        # doesn't recognize.  Without the fix these all got
        # "unknown key (typo?)" warnings, misleading users into
        # thinking they had typos when the keys were just newer.
        from termapy.migration import CURRENT_CONFIG_VERSION
        cfg = _cfg_with(
            config_version=CURRENT_CONFIG_VERSION + 1,
            some_future_field="future-value",
            another_future_field=42,
        )

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, (
            f"only the single upgrade warning; got {actual!r}"
        )
        assert "typo" not in actual[0], (
            "no misleading 'typo?' verbiage when cfg is from the future"
        )
        assert "some_future_field" not in actual[0], (
            "future fields are not enumerated"
        )

    def test_multiple_errors(self):
        # Arrange
        cfg = _cfg_with(byte_size=99, parity="Z", baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 3, "one warning per bad field"

    def test_old_config_version_warns(self):
        # Arrange
        cfg = _cfg_with(config_version=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "config_version" in actual[0], "identifies the field"
        assert "3" in actual[0], "shows the old version"

    def test_current_config_version_ok(self):
        # Arrange
        cfg = _cfg_with()  # uses CURRENT_CONFIG_VERSION

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_empty_port_warns(self):
        # Arrange -- the bare DEFAULT_CFG (without the _DEFAULTS_WITH_PORT
        # override) has port="".  A config actually persisted to disk
        # with an empty port is invalid -- the user needs to name a
        # device, USB serial number, or reserved token.
        cfg = dict(DEFAULT_CFG)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, f"exactly one warning, got {actual}"
        assert "port" in actual[0], f"identifies the field; got {actual[0]!r}"
        assert "empty" in actual[0], (
            f"describes the problem; got {actual[0]!r}"
        )

    def test_port_wrong_type_warns(self):
        # Arrange -- port must be a string.
        cfg = _cfg_with(port=1234)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, f"exactly one warning, got {actual}"
        assert "port" in actual[0], f"identifies the field; got {actual[0]!r}"
        assert "str" in actual[0] or "int" in actual[0], (
            f"describes the type mismatch; got {actual[0]!r}"
        )


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
        """Run termapy --check via subprocess and return (returncode, stdout).

        Spawns the interpreter directly (``sys.executable -c``) rather than
        ``uv run termapy``.  The console script ``termapy:run`` is exactly
        ``termapy.entry.main`` (see ``src/termapy/__init__.py``), so this is
        behaviorally identical -- but it skips uv's implicit re-sync, which
        tries to rewrite ``termapy.exe``.  A running termapy session locks that
        launcher on Windows, so ``uv run`` would otherwise fail with empty
        output whenever a live session is present.
        """
        import subprocess
        import sys

        argv = ["termapy", "--check", *args]
        result = subprocess.run(
            [
                sys.executable, "-c",
                f"import sys; sys.argv = {argv!r}; "
                "from termapy.entry import main; main()",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode, result.stdout

    def test_valid_config_ok(self, tmp_path):
        # Arrange
        cfg_file = tmp_path / "ok" / "ok.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text(json.dumps(_cfg_with()))

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 0, "success exit code"
        assert actual["status"] == "ok", "no warnings"

    def test_invalid_baud_warns(self, tmp_path):
        # Arrange
        cfg = _cfg_with(baud_rate=999)
        cfg_file = tmp_path / "bad" / "bad.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text(json.dumps(cfg))

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 0, "still exits 0 (warnings, not errors)"
        assert actual["status"] == "warn", "flagged as warn"
        assert any("baud_rate" in w for w in actual["warnings"]), "identifies field"

    def test_malformed_json_errors(self, tmp_path):
        # Arrange
        cfg_file = tmp_path / "bad" / "bad.cfg"
        cfg_file.parent.mkdir()
        cfg_file.write_text("{broken json!}")

        # Act
        code, stdout = self._run(str(cfg_file))

        # Assert
        actual = json.loads(stdout)
        assert code == 1, "error exit code"
        assert actual["status"] == "error", "parse failure"

    def test_does_not_modify_file(self, tmp_path):
        # Arrange - config with old version, check should NOT migrate it
        cfg = _cfg_with(config_version=3)
        cfg_file = tmp_path / "old" / "old.cfg"
        cfg_file.parent.mkdir()
        original = json.dumps(cfg)
        cfg_file.write_text(original)

        # Act
        self._run(str(cfg_file))

        # Assert
        actual = cfg_file.read_text()
        assert actual == original, "file unchanged (read-only check)"


# -- open_serial: URL support (rfc2217, socket, loop) -----------------------


class TestOpenSerialUrl:
    def test_demo_port_returns_fake(self):
        # Arrange
        cfg = _cfg_with(port="DEMO")

        # Act
        ser = open_serial(cfg)

        # Assert
        assert ser.__class__.__name__ == "FakeSerial", "DEMO port returns FakeSerial"

    def test_loopback_url_works(self):
        """loop:// URL round-trips bytes through pyserial's loopback handler."""
        # Arrange
        cfg = _cfg_with(port="loop://", baud_rate=115200)

        # Act
        ser = open_serial(cfg)
        try:
            ser.write(b"test\r")
            import time
            time.sleep(0.05)
            data = ser.read(100)
        finally:
            ser.close()

        # Assert
        assert data == b"test\r", "loopback returns written bytes"
