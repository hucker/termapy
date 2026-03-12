"""Tests for the /cfg and /cfg.auto REPL commands."""

import json

import pytest

from termapy.plugins import EngineAPI, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """Create a ReplEngine with a temp config file and capture output."""
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo_input": False,
        "line_ending": "\r",
        "stop_bits": 1.5,
    }
    config_path = tmp_path / "test_cfg.cfg"
    config_path.write_text(json.dumps(cfg, indent=4))

    output = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(cfg, str(config_path), write)
    engine_api = EngineAPI(
        prefix="/",
        plugins=engine._plugins,
        apply_cfg=engine._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
    )
    ctx = PluginContext(
        write=write,
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
    )
    engine.set_context(ctx)
    return engine, cfg, config_path, output


class TestCfgShowAll:
    def test_show_all_keys(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg")

        # Assert
        texts = [t for t, _ in output]
        for key in cfg:
            assert any(key in t for t in texts)  # each config key shown


class TestCfgShowKey:
    def test_show_existing_key(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate")
        assert any("115200" in t for t, _ in output)  # value displayed

    def test_show_unknown_key(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg nonexistent")
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red


class TestCfgChange:
    def test_same_value_no_change(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate 115200")
        assert any("already" in t for t, _ in output)  # no-op message

    def test_bad_type_rejected(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg baud_rate notanumber")
        assert any("Type error" in t for t, _ in output)  # type error shown

    def test_bad_bool_rejected(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg echo_input maybe")
        assert output[-1][1] == "red"  # error shown in red

    def test_calls_save_cfg_hook(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        called_with = []
        engine.ctx.engine.save_cfg = lambda k, v: called_with.append((k, v))

        # Act
        engine.dispatch("cfg baud_rate 9600")

        # Assert
        expected = [("baud_rate", 9600)]
        assert called_with == expected  # hook called with key and coerced value

    def test_falls_back_to_apply_without_hook(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600  # in-memory config updated
        assert any("session" in t for t, _ in output)  # session-only confirmation


class TestCfgAuto:
    def test_auto_int(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg.auto baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600  # in-memory config updated
        assert any("session" in t for t, _ in output)  # session-only confirmation

    def test_auto_bool(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto echo_input true")
        assert cfg["echo_input"] is True  # bool coerced and applied

    def test_auto_string(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto port COM5")
        assert cfg["port"] == "COM5"  # string value applied

    def test_auto_float(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg.auto stop_bits 2.0")
        assert cfg["stop_bits"] == 2.0  # float coerced and applied

    def test_auto_unknown_key(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto bogus 123")
        assert any("Unknown" in t for t, _ in output)  # unknown key rejected

    def test_auto_bad_type(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto baud_rate abc")
        assert any("Type error" in t for t, _ in output)  # type mismatch error

    def test_auto_missing_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto baud_rate")
        assert any("Usage" in t for t, _ in output)  # missing value arg

    def test_auto_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg.auto")
        assert any("Usage" in t for t, _ in output)  # no args at all


class TestCfgReadOnly:
    """config_read_only only blocks UI dialogs; /cfg commands still work."""

    def test_cfg_set_allowed(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["config_read_only"] = True

        # Act
        engine.dispatch("cfg.auto baud_rate 9600")

        # Assert
        assert cfg["baud_rate"] == 9600  # value changed despite config_read_only
        assert any("session" in t for t, _ in output)  # session-only confirmation

    def test_cfg_show_works(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["config_read_only"] = True

        # Act
        engine.dispatch("cfg baud_rate")

        # Assert
        assert any("115200" in t for t, _ in output)  # value displayed
