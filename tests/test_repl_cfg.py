"""Tests for the /cfg and /cfg.auto REPL commands."""

import json

import pytest

from termapy.plugins import InternalHandle, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """Create a ReplEngine with a temp config file and capture output."""
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo": False,
        "eol": "\r",
        "stop_bits": 1.5,
        "parity": "N",
        "custom_buttons_side": "bottom",
    }
    config_path = tmp_path / "test_cfg.cfg"
    config_path.write_text(json.dumps(cfg, indent=4))

    output = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(cfg, str(config_path), write)
    internal_handle = InternalHandle(
        plugins=engine._plugins,
        apply_cfg=engine._apply_cfg,
    )
    from termapy.plugins import IOHandle

    ctx = PluginContext(
        cfg=cfg,
        config_path=str(config_path),
        internal=internal_handle,
        io=IOHandle(_write=write, _write_markup=write),
    )
    engine.set_context(ctx)
    return engine, cfg, config_path, output


class TestCfgShowAll:
    def test_show_all_keys(self, repl_env):
        """/cfg.dump prints every config key; bare /cfg no longer does."""
        # Arrange -- bare /cfg now opens the Cfg picker (TUI) or shows
        # /help cfg (CLI); /cfg.dump is the explicit "print every key"
        # path.
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg.dump")

        # Assert
        texts = [text for text, _ in output]
        for key in cfg:
            assert any(key in text for text in texts), "each config key shown"


class TestCfgShowKey:
    def test_show_existing_key(self, repl_env):
        """/cfg with a known key and no value is a query that prints the current value."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate")
        assert any("115200" in t for t, _ in output), "value displayed"

    def test_query_data_keeps_the_json_type(self, repl_env):
        """A query's data is the value with its JSON type; the value stays the scalar text."""
        # Arrange
        engine, _, _, _ = repl_env

        # Act
        rate = engine.dispatch("cfg baud_rate")
        echo = engine.dispatch("cfg echo")

        # Assert
        assert (rate.value, rate.data) == (115200, 115200), "a number is data as a number"
        assert (echo.value, echo.data) == (False, False), (
            "a boolean is data as a boolean, shown as off"
        )

    def test_container_key_is_json_never_repr(self, repl_env):
        """A dict or list key answers compact JSON, shown and as the value; its data is the structure."""
        # Arrange -- a container key, as serial and custom_buttons are in a real config
        engine, cfg, _, output = repl_env
        cfg["custom_buttons"] = [{"enabled": True, "name": "Go"}]

        # Act
        result = engine.dispatch("cfg custom_buttons")

        # Assert
        expected = '[{"enabled": true, "name": "Go"}]'
        assert result.value == expected, "JSON as the config file spells it, not Python's repr"
        assert result.data == [{"enabled": True, "name": "Go"}], "the structure itself is the data"
        assert any(text == expected for text, _ in output), "shown as JSON too"

    def test_set_answers_the_new_value_as_data(self, repl_env):
        """A set's data is the value it applied, with its JSON type."""
        # Arrange
        engine, _, _, _ = repl_env

        # Act
        result = engine.dispatch("cfg.auto baud_rate 9600")

        # Assert
        assert (result.value, result.data) == (9600, 9600), "the applied value, typed"

    def test_show_unknown_key(self, repl_env):
        """A query for a key not in the config is an Unknown error rendered in red."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg nonexistent")
        assert any("Unknown" in t for t, _ in output), "error message"
        assert output[-1][1] == "red", "shown in red"


class TestCfgChange:
    def test_same_value_no_change(self, repl_env):
        """Setting a key to the value it already holds reports a no-op instead of saving."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate 115200")
        assert any("already" in t for t, _ in output), "no-op message"

    def test_bad_type_rejected(self, repl_env):
        """A value that cannot coerce to the key's type is refused with a Type error."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate notanumber")
        assert any("Type error" in t for t, _ in output), "type error shown"

    def test_bad_bool_rejected(self, repl_env):
        """A bool key given a token outside the bool vocabulary is refused with a Type error."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg echo maybe")
        assert any("Type error" in t for t, _ in output), "bool validation refused 'maybe'"
        assert output[-1][1] == "red", "error shown in red"

    def test_calls_confirm_save_cfg_hook(self, repl_env):
        """A frontend's confirm_save_cfg hook gets the key and the already-coerced value."""
        # Arrange
        engine, _, _, output = repl_env
        called_with = []

        def confirm(key, value):
            called_with.append((key, value))
            return True

        engine.ctx.internal.confirm_save_cfg = confirm

        # Act
        result = engine.dispatch("cfg baud_rate 9600")

        # Assert
        expected = [("baud_rate", 9600)]
        assert called_with == expected, "hook called with key and coerced value"
        assert result.value == 9600, "a confirmed change answers with the new value"

    def test_a_declined_confirmation_fails(self, repl_env):
        """When the frontend's confirmation is declined, /cfg fails as Canceled instead of answering the value."""
        # Arrange
        engine, _, _, _ = repl_env
        engine.ctx.internal.confirm_save_cfg = lambda key, value: False

        # Act
        result = engine.dispatch("cfg baud_rate 9600")

        # Assert
        assert (result.success, result.error) == (False, "Canceled.")

    def test_falls_back_to_apply_without_hook(self, repl_env):
        """With no save hook (CLI), /cfg applies the change for this session only."""
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600, "in-memory config updated"
        assert any("session" in t for t, _ in output), "session-only confirmation"

    def test_a_choice_in_another_case_is_stored_as_the_config_spells_it(self, repl_env):
        """/cfg reads a fixed-value key's value in any case and stores the file's spelling."""
        engine, cfg, _, _ = repl_env
        engine.dispatch("cfg custom_buttons_side LEFT")
        assert cfg["custom_buttons_side"] == "left", "stored as the file spells it"


class TestCfgAuto:
    def test_auto_int(self, repl_env):
        """/cfg.auto applies an int value in memory without prompting to save."""
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg.auto baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600, "in-memory config updated"
        assert any("session" in t for t, _ in output), "session-only confirmation"

    def test_auto_bool(self, repl_env):
        """/cfg.auto coerces the token "true" to a real bool for a bool key."""
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto echo true")
        assert cfg["echo"] is True, "bool coerced and applied"

    def test_auto_string(self, repl_env):
        """/cfg.auto stores a string key's value as given."""
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto port COM5")
        assert cfg["port"] == "COM5", "string value applied"

    def test_auto_float(self, repl_env):
        """/cfg.auto coerces to float when the key's current value is a float."""
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto stop_bits 2.0")
        assert cfg["stop_bits"] == 2.0, "float coerced and applied"

    def test_auto_matches_a_choice_in_any_case(self, repl_env):
        """A fixed-value key takes its value in any case and stores the file's spelling."""
        # Arrange
        engine, cfg, _, _ = repl_env

        # Act
        engine.dispatch("cfg.auto custom_buttons_side Right")
        engine.dispatch("cfg.auto parity e")

        # Assert
        actual = (cfg["custom_buttons_side"], cfg["parity"])
        assert actual == ("right", "E"), "each stored as the file spells it"

    def test_auto_refuses_a_value_outside_the_choices(self, repl_env):
        """A fixed-value key refuses any other value, naming the choices, and keeps its own."""
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg.auto custom_buttons_side up")

        # Assert
        expected = "Invalid custom_buttons_side: up (use bottom/left/right)"
        assert any(expected in t for t, _ in output), f"the refusal names the choices: {output}"
        assert cfg["custom_buttons_side"] == "bottom", "the value is unchanged"

    def test_auto_unknown_key(self, repl_env):
        """/cfg.auto refuses a key not in the config instead of adding it."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto bogus 123")
        assert any("Unknown" in t for t, _ in output), "unknown key rejected"

    def test_auto_bad_type(self, repl_env):
        """/cfg.auto rejects a value that does not fit the key's type."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto baud_rate abc")
        assert any("Type error" in t for t, _ in output), "type mismatch error"

    def test_auto_missing_args(self, repl_env):
        """/cfg.auto with a key but no value is a usage error, not a query."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto baud_rate")
        assert any("Usage" in t for t, _ in output), "missing value arg"

    def test_auto_no_args(self, repl_env):
        """Bare /cfg.auto is a usage error."""
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto")
        assert any("Usage" in t for t, _ in output), "no args at all"


class TestCfgReadOnly:
    """config_read_only only blocks UI dialogs; /cfg commands still work."""

    def test_cfg_set_allowed(self, repl_env):
        """config_read_only does not block /cfg.auto from changing a value."""
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["config_read_only"] = True

        # Act
        engine.dispatch("cfg.auto baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600, "value changed despite config_read_only"
        assert any("session" in t for t, _ in output), "session-only confirmation"

    def test_cfg_show_works(self, repl_env):
        """config_read_only leaves /cfg queries working."""
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["config_read_only"] = True

        # Act
        engine.dispatch("cfg baud_rate")

        # Assert
        assert any("115200" in t for t, _ in output), "value displayed"
