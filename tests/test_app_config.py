"""Tests for app.py config utilities, custom buttons, and script editor."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from termapy.config import (
    cfg_data_dir,
    cfg_history_path,
    cfg_log_path,
    cfg_path_for_name,
    cfg_plugins_dir,
    expand_env_cfg,
    expand_env_str,
    file_manager_name,
    load_config,
    migrate_json_to_cfg,
    open_serial,
    validate_config,
)
from termapy.defaults import DEFAULT_CFG, SCRIPT_TEMPLATE

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


# -- cfg_data_dir: the config folder and its .gitignore, no data folders ----


class TestCfgDataDir:
    def test_creates_no_data_folders(self, tmp_path):
        """Data folders appear on first write (folders.ensure_folder), never on load."""
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual == config_path.parent, "returns parent directory"
        actual_entries = sorted(path.name for path in actual.iterdir())
        assert actual_entries == [".gitignore"], "only the .gitignore is written; no data folder"

    def test_idempotent(self, tmp_path):
        """A second call on an already-set-up config folder is a harmless no-op."""
        # Arrange
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()

        # Act
        cfg_data_dir(str(config_path))
        cfg_data_dir(str(config_path))  # second call should not error

        # Assert
        assert (config_path.parent / ".gitignore").is_file(), "the .gitignore is still there"

    def test_creates_parent_if_needed(self, tmp_path):
        """A config folder that does not exist yet is created, with its .gitignore."""
        # Arrange
        config_path = tmp_path / "new" / "new.cfg"

        # Act
        actual = cfg_data_dir(str(config_path))

        # Assert
        assert actual.is_dir(), "parent dir created"
        assert (actual / ".gitignore").is_file(), "with its .gitignore"

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
        """A config name maps to <name>/<name>.cfg -- the one naming invariant."""
        actual = cfg_path_for_name("mydev")
        assert actual.name == "mydev.cfg", "filename matches"
        assert actual.parent.name == "mydev", "parent dir matches"

    def test_cfg_log_path(self, tmp_path):
        """The session log is named after the config stem."""
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_log_path(str(config_path))
        assert actual.endswith("dev.log"), "log named after config"

    def test_cfg_history_path(self, tmp_path):
        """Per-config history sits beside the cfg as <stem>.history, where both hosts write."""
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
        """With no config loaded, history goes to a fallback file in the global cfg root."""
        # Act -- no config loaded: fallback file in the global cfg root
        actual = cfg_history_path("")

        # Assert
        assert actual.endswith(".cmd_history.txt"), "fallback filename"

    def test_cfg_plugins_dir(self, tmp_path):
        """The per-config plugin/ path is computed without creating the folder."""
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        actual = cfg_plugins_dir(str(config_path))
        assert actual == config_path.parent / "plugin", "the per-config plugin/ path"
        assert not actual.exists(), "a read: the folder is not created"

    def test_cfg_data_dir_migrates_the_symbol_table_into_sym(self, tmp_path):
        """A symbol table left beside the cfg (pre 2026-09 layout) moves into sym/."""
        # Arrange -- a table where it lived before 2026-09: beside the cfg
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        old = config_path.parent / "dev.symbols.json"
        old.write_text("{}", encoding="utf-8")

        # Act
        cfg_data_dir(str(config_path))

        # Assert
        assert not old.exists(), "moved out of the root"
        assert (config_path.parent / "sym" / "dev.symbols.json").is_file(), "into sym/"

    def test_sidecar_migration_never_overwrites(self, tmp_path):
        """When sym/ already holds the table, the migration keeps it and leaves the old copy."""
        # Arrange -- both an old and a new copy exist
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        (config_path.parent / "sym").mkdir()
        (config_path.parent / "sym" / "dev.symbols.json").write_text("new", encoding="utf-8")
        old = config_path.parent / "dev.symbols.json"
        old.write_text("old", encoding="utf-8")

        # Act
        cfg_data_dir(str(config_path))

        # Assert
        actual = (config_path.parent / "sym" / "dev.symbols.json").read_text(encoding="utf-8")
        assert actual == "new", "the copy already in sym/ wins"
        assert old.exists(), "the old one is left for the user to reconcile"


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
        """The defaults ship at least four custom button slots."""
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
        """Every default button carries enabled, name, command and tooltip."""
        for btn in _custom_buttons():
            assert "enabled" in btn, "enabled field present"
            assert "name" in btn, "name field present"
            assert "command" in btn, "command field present"
            assert "tooltip" in btn, "tooltip field present"

    def test_has_essential_keys(self):
        """Line ending and prefix stay top-level; port and baud_rate live under serial."""
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
        """Loading a missing config raises instead of silently creating one."""
        # Arrange
        config_path = tmp_path / "test" / "test.cfg"

        # Act / Assert - load_config no longer auto-creates files
        import pytest
        with pytest.raises(FileNotFoundError):
            load_config(str(config_path))

    def test_adds_missing_keys(self, tmp_path):
        """Missing default keys are backfilled and persisted; existing values survive."""
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
        """A user's own custom_buttons list is kept, not replaced by the defaults."""
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
        """A backfilled list is a copy, so editing a loaded config cannot corrupt DEFAULT_CFG."""
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
        default_buttons = DEFAULT_CFG["custom_buttons"]
        assert isinstance(default_buttons, list), "custom_buttons is a list"
        expected_default_len = len(default_buttons)
        actual["custom_buttons"].append(
            {"enabled": True, "name": "X", "command": "X", "tooltip": ""}
        )
        actual_default_len = len(default_buttons)
        assert actual_default_len == expected_default_len, \
            "mutating the loaded config must not change DEFAULT_CFG"


# -- SCRIPT_TEMPLATE -------------------------------------------------------


class TestScriptTemplate:
    def test_has_placeholder(self):
        """The new-script template substitutes the script name."""
        actual = SCRIPT_TEMPLATE.format(name="test_script")
        assert "test_script" in actual, "name placeholder expanded"

    def test_has_comments(self):
        """Every non-blank template line is a comment, so a fresh script sends nothing."""
        actual = SCRIPT_TEMPLATE.format(name="x")
        lines = actual.strip().splitlines()
        assert all(
            line.startswith("#") for line in lines if line.strip()
        ), "all lines are comments"

    def test_has_example_commands(self):
        """The template shows a REPL command example (/delay) for the user to copy."""
        actual = SCRIPT_TEMPLATE.format(name="x")
        assert "/delay" in actual, "contains REPL example"


# -- expand_env_str / expand_env_cfg ----------------------------------------


class TestExpandEnvStr:
    def test_known_var(self, monkeypatch):
        """A set environment variable replaces its $(env.NAME) placeholder."""
        # Arrange
        monkeypatch.setenv("TEST_PORT_XYZ", "COM7")

        # Act
        actual = expand_env_str("$(env.TEST_PORT_XYZ)")

        # Assert
        expected = "COM7"
        assert actual == expected, "known var expanded"

    def test_fallback_when_missing(self, monkeypatch):
        """An unset variable uses the |fallback value after the pipe."""
        # Arrange
        monkeypatch.delenv("MISSING_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.MISSING_CFG_VAR|COM4)")

        # Assert
        expected = "COM4"
        assert actual == expected, "fallback used"

    def test_unknown_without_fallback_unchanged(self, monkeypatch):
        """An unset variable with no fallback leaves the placeholder literal, no crash."""
        # Arrange
        monkeypatch.delenv("NOPE_CFG_VAR", raising=False)

        # Act
        actual = expand_env_str("$(env.NOPE_CFG_VAR)")

        # Assert
        expected = "$(env.NOPE_CFG_VAR)"
        assert actual == expected, "left unchanged (no crash)"

    def test_plain_string_unchanged(self):
        """A string with no placeholder passes through untouched."""
        # Act
        actual = expand_env_str("COM4")

        # Assert
        expected = "COM4"
        assert actual == expected, "no placeholder, unchanged"


class TestExpandEnvCfg:
    def test_expands_string_values(self, monkeypatch):
        """String cfg values are expanded while numbers beside them are left alone."""
        # Arrange
        monkeypatch.setenv("CFG_PORT_TEST", "COM9")
        cfg = {"port": "$(env.CFG_PORT_TEST|COM1)", "baud_rate": 115200}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["port"] == "COM9", "string value expanded"
        assert actual["baud_rate"] == 115200, "non-string untouched"

    def test_skips_non_strings(self):
        """Int and bool cfg values pass through expansion unchanged."""
        # Arrange
        cfg = {"baud_rate": 9600, "auto_connect": True, "max_lines": 10000}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert actual["baud_rate"] == 9600, "int unchanged"
        assert actual["auto_connect"] is True, "bool unchanged"

    def test_skips_connect_command_keys(self, monkeypatch):
        """Connect-command keys stay literal so env values never reach the wire unchecked."""
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


    def test_a_templated_bool_key_reads_as_a_real_bool(self, monkeypatch):
        """bool-one-vocabulary: a $(env.X) in a boolean key is read with the vocabulary, so the cfg holds a bool, not the text."""
        # Arrange
        monkeypatch.setenv("CFG_BOOL_TEST", "yes")
        cfg = {"echo": "$(env.CFG_BOOL_TEST|false)", "serial": {"custom_baud": "$(env.CFG_BOOL_TEST)"}}

        # Act
        actual = expand_env_cfg(cfg)

        # Assert
        assert (actual["echo"], actual["serial"]["custom_baud"]) == (True, True), "top-level and nested"

    def test_a_templated_bool_outside_the_vocabulary_is_refused_by_key(self, monkeypatch):
        """Text a boolean key cannot read raises Invalid <key>: <text>, so the cfg fails to load instead of reading True."""
        monkeypatch.setenv("CFG_BOOL_TEST", "maybe")
        with pytest.raises(ValueError, match=r"^Invalid serial\.custom_baud: maybe \(use on/off/"):
            expand_env_cfg({"serial": {"custom_baud": "$(env.CFG_BOOL_TEST)"}})

    def test_an_unresolved_bool_template_falls_back_to_the_default_loudly(self, monkeypatch):
        """An unset variable with no fallback in a boolean key yields DEFAULT_CFG's value and a warning naming the key."""
        # Arrange
        monkeypatch.delenv("CFG_BOOL_NOPE", raising=False)
        warnings: list[str] = []

        # Act
        actual = expand_env_cfg({"echo": "$(env.CFG_BOOL_NOPE)"}, warnings)

        # Assert
        assert actual["echo"] is False, "the default, not the literal placeholder (which read as True)"
        assert warnings == ["echo: $(env.CFG_BOOL_NOPE) did not resolve; using default false"]


class TestBoolKeysAreTyped:
    """A boolean cfg key holds a JSON boolean: a string or number there is type-checked at load, never parsed."""

    @pytest.mark.parametrize(("path", "value", "expected"), [
        (("echo",), "false", "echo: expected bool, got str"),
        (("timestamps",), 1, "timestamps: expected bool, got int"),
        (("serial", "custom_baud"), "yes", "serial.custom_baud: expected bool, got str"),
    ])
    def test_a_non_bool_in_a_bool_key_warns_by_path(self, path, value, expected):
        """Every DEFAULT_CFG boolean path is checked, nested serial keys included; the warning names the path and the type."""
        # Arrange
        cfg = copy.deepcopy(DEFAULT_CFG)
        node: Any = cfg
        for part in path[:-1]:
            node = node[part]
        node[path[-1]] = value

        # Act / Assert
        assert expected in validate_config(cfg), "a string in a bool key is a type warning, never read as a token"


class TestLoadConfigEnvExpansion:
    def test_a_templated_bool_loads_as_a_bool_and_an_unresolved_one_warns(self, tmp_path, monkeypatch):
        """Through load_config a templated boolean is a bool in memory, and an unresolved one surfaces in _config_warnings."""
        # Arrange
        monkeypatch.setenv("LC_BOOL_ON", "on")
        monkeypatch.delenv("LC_BOOL_NOPE", raising=False)
        config_path = tmp_path / "dev" / "dev.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps({"echo": "$(env.LC_BOOL_ON)", "timestamps": "$(env.LC_BOOL_NOPE)"}))

        # Act
        actual = load_config(str(config_path))

        # Assert
        assert (actual["echo"], actual["timestamps"]) == (True, False), "read with the vocabulary; default on no resolve"
        assert "timestamps: $(env.LC_BOOL_NOPE) did not resolve; using default false" in actual["_config_warnings"]

    def test_expands_env_in_loaded_config(self, tmp_path, monkeypatch):
        """A loaded config has env placeholders expanded in memory, after migration."""
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
        """The file on disk keeps the raw $(env...) template even after a migrating save."""
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
        """A legacy <name>/<name>.json config is renamed to .cfg."""
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
        """When both .json and .cfg exist, the .cfg wins and nothing is overwritten."""
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
        """Running the migration again after it has run changes nothing."""
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
        """The defaults plus a named port produce no warnings at all."""
        # Act
        actual = validate_config(_cfg_with())

        # Assert
        assert actual == [], "no warnings for defaults"

    def test_invalid_byte_size(self):
        """An out-of-range byte_size yields one warning naming the field and bad value."""
        # Arrange
        cfg = _cfg_with(byte_size=9)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "byte_size" in actual[0], "identifies the field"
        assert "9" in actual[0], "shows the bad value"

    def test_invalid_parity(self):
        """An unknown parity letter yields exactly one warning naming parity."""
        # Arrange
        cfg = _cfg_with(parity="X")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "parity" in actual[0], "identifies the field"

    def test_invalid_stop_bits(self):
        """A stop_bits value pyserial cannot use yields exactly one warning."""
        # Arrange
        cfg = _cfg_with(stop_bits=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "stop_bits" in actual[0], "identifies the field"

    def test_invalid_flow_control(self):
        """An unknown flow_control mode yields exactly one warning."""
        # Arrange
        cfg = _cfg_with(flow_control="bad")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "flow_control" in actual[0], "identifies the field"

    def test_nonstandard_baud_rate_warns(self):
        """A nonstandard baud warns, not rejects, and points the user at custom_baud."""
        # Arrange
        cfg = _cfg_with(baud_rate=250000)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns but doesn't reject"
        assert "not a standard rate" in actual[0], "clear message"
        assert "custom_baud" in actual[0], "tells user how to fix"

    def test_custom_baud_accepts_nonstandard(self):
        """With custom_baud on, a nonstandard rate like 625000 is accepted silently."""
        # Arrange
        cfg = _cfg_with(baud_rate=625000, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings with custom_baud enabled"

    def test_custom_baud_rejects_below_300(self):
        """custom_baud still enforces a 300 baud floor and says so."""
        # Arrange
        cfg = _cfg_with(baud_rate=150, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns even with custom_baud"
        assert "300" in actual[0], "mentions minimum"

    def test_custom_baud_accepts_300(self):
        """300 baud itself is inside the custom_baud floor."""
        # Arrange
        cfg = _cfg_with(baud_rate=300, custom_baud=True)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "300 is accepted"

    def test_custom_baud_false_still_warns(self):
        """An explicit custom_baud=False keeps the nonstandard-rate warning."""
        # Arrange
        cfg = _cfg_with(baud_rate=625000, custom_baud=False)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "warns when custom_baud is False"
        assert "not a standard rate" in actual[0], "standard warning"

    def test_custom_baud_wrong_type(self):
        """A non-bool custom_baud gets a type warning, not a truthy reading."""
        # Arrange
        cfg = _cfg_with(custom_baud="yes")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "expected bool" in actual[0], "type error"

    def test_standard_baud_rate_ok(self):
        """A standard rate like 9600 needs no custom_baud and draws no warning."""
        # Arrange
        cfg = _cfg_with(baud_rate=9600)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_negative_baud_rate(self):
        """A negative baud rate is flagged as needing to be positive."""
        # Arrange
        cfg = _cfg_with(baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "positive" in actual[0], "clear message"

    def test_baud_rate_wrong_type(self):
        """A string baud_rate gets a type warning naming the expected int."""
        # Arrange
        cfg = _cfg_with(baud_rate="fast")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "expected int" in actual[0], "type error message"

    def test_invalid_encoding(self):
        """An encoding Python has no codec for is flagged."""
        # Arrange
        cfg = _cfg_with(encoding="not-a-codec")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "encoding" in actual[0], "identifies the field"

    def test_valid_encoding(self):
        """A real codec name such as ascii passes validation."""
        # Arrange
        cfg = _cfg_with(encoding="ascii")

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_negative_cmd_delay_ms(self):
        """A negative inter-command delay is flagged."""
        # Arrange
        cfg = _cfg_with(cmd_delay_ms=-10)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "cmd_delay_ms" in actual[0], "identifies the field"

    def test_zero_max_lines(self):
        """A scrollback limit of zero is flagged as invalid."""
        # Arrange
        cfg = _cfg_with(max_lines=0)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "max_lines" in actual[0], "identifies the field"

    def test_unknown_key_flagged(self):
        """A key that never existed is reported as unknown, by name."""
        # Arrange -- a key that never existed, not a deprecated one.
        cfg = _cfg_with(not_a_real_key=9600)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "unknown key" in actual[0], "clear message"
        assert "not_a_real_key" in actual[0], "shows the bad key"

    def test_deprecated_renamed_key_flagged_with_hint(self):
        """A renamed key is called deprecated and names its replacement and version."""
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
        """A removed key is called deprecated and says the version that dropped it."""
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
        """Underscore-prefixed bookkeeping keys are never reported as unknown."""
        # Arrange
        cfg = _cfg_with(_migrated_from=5, _config_warnings=[])

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "internal keys not flagged"

    def test_older_config_version_warns_generic(self):
        """An older schema gets an informational warning but no upgrade advice."""
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
        """A config from a newer termapy yields one warning telling the user to upgrade."""
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
        """A future config's unfamiliar keys are not misreported as typos."""
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
        """Several bad fields each get their own warning, none swallowed."""
        # Arrange
        cfg = _cfg_with(byte_size=99, parity="Z", baud_rate=-1)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 3, "one warning per bad field"

    def test_old_config_version_warns(self):
        """A very old config_version (3) is flagged with its version number."""
        # Arrange
        cfg = _cfg_with(config_version=3)

        # Act
        actual = validate_config(cfg)

        # Assert
        assert len(actual) == 1, "exactly one warning"
        assert "config_version" in actual[0], "identifies the field"
        assert "3" in actual[0], "shows the old version"

    def test_current_config_version_ok(self):
        """The current config_version draws no version warning."""
        # Arrange
        cfg = _cfg_with()  # uses CURRENT_CONFIG_VERSION

        # Act
        actual = validate_config(cfg)

        # Assert
        assert actual == [], "no warnings"

    def test_empty_port_warns(self):
        """A saved config with an empty port is flagged, since it names no device."""
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
        """A numeric port value gets a type-mismatch warning."""
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
        """Broken JSON raises ValueError with line and column, not a raw decoder error."""
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
        """--check on a clean config prints status ok and exits 0."""
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
        """--check reports a bad baud as a warning and still exits 0."""
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
        assert any("baud_rate" in warning for warning in actual["warnings"]), "identifies field"

    def test_malformed_json_errors(self, tmp_path):
        """--check on unparseable JSON reports status error and exits 1."""
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
        """--check is read-only: an old config is not migrated on disk."""
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
        """The DEMO port opens the simulated FakeSerial device, not real hardware."""
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


class TestOpenSerialRxBuffer:
    """pyserial hardcodes a 4 KB driver RX buffer on Windows.

    That is small enough that any main-thread stall longer than
    ``4096 / (baud / 10)`` ms makes the driver silently discard the overflow.
    Measured on a hardware loopback: a 60 ms stall at 921600 lost 21% of the
    stream, 0% after widening the buffer
    (docs/review/2026-08-19-v0.74.0-opus-5.md).
    """

    def test_buffer_is_widened_when_the_port_supports_it(self):
        """A port with set_buffer_size gets the widened RX buffer, not 4 KB."""
        # Arrange -- stand in for serialwin32, the only backend with the API.
        from termapy.config import SERIAL_RX_BUFFER_BYTES, request_rx_buffer

        class WinLikePort:
            def __init__(self):
                self.rx_size = None

            def set_buffer_size(self, rx_size=4096, tx_size=None):
                self.rx_size = rx_size

        port = WinLikePort()

        # Act
        accepted = request_rx_buffer(port)

        # Assert
        assert accepted is True, "a port exposing the API should be widened"
        actual = port.rx_size
        assert actual == SERIAL_RX_BUFFER_BYTES, (
            "must widen past pyserial's 4 KB default, or a stalled main "
            "thread silently drops received bytes"
        )

    def test_driver_refusal_is_reported_not_raised(self):
        """A driver refusing the buffer size returns False and does not fail the open."""
        # Arrange -- best-effort: the port is usable at the default size, so a
        # refusal must not propagate and fail the open.
        from termapy.config import request_rx_buffer

        class RefusingPort:
            def set_buffer_size(self, rx_size=4096, tx_size=None):
                raise OSError("driver refused")

        # Act
        actual = request_rx_buffer(RefusingPort())

        # Assert
        assert actual is False, "a refused request reports False rather than raising"

    def test_port_without_the_api_is_left_alone(self):
        """A backend without set_buffer_size is skipped quietly, no exception."""
        # Arrange -- set_buffer_size exists only on serialwin32; POSIX and URL
        # backends must be untouched rather than erroring.
        from termapy.config import request_rx_buffer

        class PosixLikePort:
            pass

        # Act
        actual = request_rx_buffer(PosixLikePort())

        # Assert
        assert actual is False, "no API means no request, and no exception"

    def test_real_url_port_still_opens_and_works(self):
        """A loop:// port opens and round-trips bytes even with no buffer API."""
        # Arrange -- end-to-end through open_serial on a backend with no
        # set_buffer_size, proving the guard does not break normal opens.
        cfg = _cfg_with(port="loop://", baud_rate=115200)

        # Act
        ser = open_serial(cfg)
        try:
            has_api = hasattr(ser, "set_buffer_size")
            ser.write(b"ok")
            import time
            time.sleep(0.05)
            data = ser.read(2)
        finally:
            ser.close()

        # Assert
        assert has_api is False, "loop:// has no set_buffer_size -- guard is required"
        assert data == b"ok", "port still usable without the buffer request"


class TestFileManagerName:
    """The picker's folder button is named for the platform's file manager."""

    def test_windows_and_mac_have_one_each(self):
        """Windows names the folder button Explorer and macOS names it Finder."""
        assert file_manager_name("win32") == "Explorer"
        assert file_manager_name("darwin") == "Finder"

    @pytest.mark.parametrize("platform", ["linux", "freebsd14", "openbsd7", "haiku1"])
    def test_everything_else_is_files(self, platform):
        """Every other platform gets the generic Files label, with no desktop sniffing."""
        # Act / Assert -- Linux users know their file manager as a menu entry,
        # not a project name, so no desktop detection: one generic label
        assert file_manager_name(platform) == "Files"
