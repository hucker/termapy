"""Tests for the !cfg and !cfg_auto REPL commands."""

import json

import pytest

from termapy.plugins import EngineAPI, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """Create a ReplEngine with a temp config file and capture output."""
    cfg = {
        "port": "COM4",
        "baudrate": 115200,
        "echo_cmd": False,
        "line_ending": "\r",
        "stopbits": 1.5,
    }
    config_path = tmp_path / "test_cfg.json"
    config_path.write_text(json.dumps(cfg, indent=4))

    output = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(cfg, str(config_path), write)
    engine_api = EngineAPI(
        prefix="!",
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
        engine.dispatch("cfg baudrate")
        assert any("115200" in t for t, _ in output)  # value displayed

    def test_show_unknown_key(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg nonexistent")
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red


class TestCfgChange:
    def test_same_value_no_change(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg baudrate 115200")
        assert any("already" in t for t, _ in output)  # no-op message

    def test_bad_type_rejected(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg baudrate notanumber")
        assert any("Type error" in t for t, _ in output)  # type error shown

    def test_bad_bool_rejected(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg echo_cmd maybe")
        assert output[-1][1] == "red"  # error shown in red

    def test_calls_save_cfg_hook(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        called_with = []
        engine.ctx.engine.save_cfg = lambda k, v: called_with.append((k, v))

        # Act
        engine.dispatch("cfg baudrate 9600")

        # Assert
        expected = [("baudrate", 9600)]
        assert called_with == expected  # hook called with key and coerced value

    def test_falls_back_to_apply_without_hook(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg baudrate 9600")

        # Assert
        assert cfg["baudrate"] == 9600  # in-memory config updated
        actual_saved = json.loads(config_path.read_text())
        assert actual_saved["baudrate"] == 9600  # persisted to disk
        assert any("saved" in t for t, _ in output)  # confirmation shown


class TestCfgAuto:
    def test_auto_int(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("cfg_auto baudrate 9600")

        # Assert
        assert cfg["baudrate"] == 9600  # in-memory config updated
        actual_saved = json.loads(config_path.read_text())
        assert actual_saved["baudrate"] == 9600  # persisted to disk
        assert any("saved" in t for t, _ in output)  # confirmation shown

    def test_auto_bool(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg_auto echo_cmd true")
        assert cfg["echo_cmd"] is True  # bool coerced and applied

    def test_auto_string(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg_auto port COM5")
        assert cfg["port"] == "COM5"  # string value applied

    def test_auto_float(self, repl_env):
        engine, cfg, _, output = repl_env
        engine.dispatch("cfg_auto stopbits 2.0")
        assert cfg["stopbits"] == 2.0  # float coerced and applied

    def test_auto_unknown_key(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg_auto bogus 123")
        assert any("Unknown" in t for t, _ in output)  # unknown key rejected

    def test_auto_bad_type(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg_auto baudrate abc")
        assert any("Type error" in t for t, _ in output)  # type mismatch error

    def test_auto_missing_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg_auto baudrate")
        assert any("Usage" in t for t, _ in output)  # missing value arg

    def test_auto_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("cfg_auto")
        assert any("Usage" in t for t, _ in output)  # no args at all
