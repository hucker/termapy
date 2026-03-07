"""Tests for ReplEngine internals: start_script, _coerce_type, properties."""

import json
from pathlib import Path

import pytest

from terapy.repl import ReplEngine


@pytest.fixture
def engine(tmp_path):
    """Create a basic ReplEngine with a temp config."""
    cfg = {"port": "COM4", "baudrate": 115200, "line_ending": "\r"}
    config_path = tmp_path / "sub" / "test.json"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    output = []
    return ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c))), output


# -- _coerce_type ----------------------------------------------------------


class TestCoerceType:
    def test_bool_true_values(self):
        for val in ("true", "1", "yes", "on", "True", "YES"):
            assert ReplEngine._coerce_type(val, False) is True

    def test_bool_false_values(self):
        for val in ("false", "0", "no", "off", "False", "NO"):
            assert ReplEngine._coerce_type(val, True) is False

    def test_bool_invalid(self):
        with pytest.raises(ValueError):
            ReplEngine._coerce_type("maybe", True)

    def test_int(self):
        assert ReplEngine._coerce_type("42", 0) == 42
        assert isinstance(ReplEngine._coerce_type("42", 0), int)

    def test_int_invalid(self):
        with pytest.raises(ValueError):
            ReplEngine._coerce_type("abc", 0)

    def test_float(self):
        assert ReplEngine._coerce_type("3.14", 0.0) == 3.14
        assert isinstance(ReplEngine._coerce_type("3.14", 0.0), float)

    def test_string(self):
        assert ReplEngine._coerce_type("hello", "default") == "hello"


# -- start_script ----------------------------------------------------------


class TestStartScript:
    def test_no_filename(self, engine):
        eng, output = engine
        result = eng.start_script("")
        assert result is None
        assert any("Usage" in t for t, _ in output)

    def test_file_not_found(self, engine):
        eng, output = engine
        result = eng.start_script("nonexistent.txt")
        assert result is None
        assert any("not found" in t.lower() for t, _ in output)

    def test_file_found_directly(self, engine, tmp_path):
        eng, output = engine
        script = tmp_path / "test_script.txt"
        script.write_text("rev\n")
        result = eng.start_script(str(script))
        assert result == script
        assert eng._in_script is True

    def test_file_found_in_scripts_dir(self, engine):
        eng, output = engine
        scripts_dir = eng.scripts_dir
        scripts_dir.mkdir(exist_ok=True)
        script = scripts_dir / "init.txt"
        script.write_text("rev\n")
        result = eng.start_script("init.txt")
        assert result == script
        assert eng._in_script is True

    def test_already_running(self, engine, tmp_path):
        eng, output = engine
        eng._in_script = True
        script = tmp_path / "test.txt"
        script.write_text("rev\n")
        result = eng.start_script(str(script))
        assert result is None
        assert any("already running" in t.lower() for t, _ in output)


# -- Properties ------------------------------------------------------------


class TestProperties:
    def test_ss_dir(self, engine):
        eng, _ = engine
        ss = eng.ss_dir
        assert ss.name == "ss"
        assert ss.exists()

    def test_scripts_dir(self, engine):
        eng, _ = engine
        scripts = eng.scripts_dir
        assert scripts.name == "scripts"

    def test_ss_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.ss_dir == Path(".")

    def test_scripts_dir_no_config(self):
        eng = ReplEngine({}, "", lambda t, c=None: None)
        assert eng.scripts_dir == Path(".")

    def test_echo_default_true(self, engine):
        eng, _ = engine
        assert eng.echo is True

    def test_in_script_default_false(self, engine):
        eng, _ = engine
        assert eng.in_script is False
