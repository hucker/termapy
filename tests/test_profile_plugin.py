"""Tests for /profile.load and /profile.info (Phase 4).

Phase 0 added /profile.validate; Phase 4 added /profile.load (sets
the active_profile namespace) and /profile.info (displays the
metadata).

Transport-apply on load is deferred to Phase 6 (needs SerialEngine
runtime baud changes + lifecycle hooks).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


@pytest.fixture
def env(tmp_path):
    """Build an MCPHost so ctx.engine is wired (catalog + plugins available)."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    host = MCPHost(cfg, str(config_path), verbose=False)
    host.ctx.ns("flags")["output_level"] = "verbose"
    output: list = []
    orig_write = host.ctx.write

    def captured_write(text, color=""):
        output.append((text, color))
        orig_write(text, color)

    host.ctx.write = captured_write
    return host.repl, host.ctx, output


# ── /profile.load ───────────────────────────────────────────────────────────


class TestProfileLoad:
    def test_loads_reference_profile(self, env):
        # Arrange / Act
        eng, ctx, _output = env
        path = FIXTURES / "at_modem.profile.json"
        result = eng.dispatch(f"profile.load {path}")
        # Assert
        assert result.success is True, "load succeeds"
        active = ctx.ns("active_profile")
        assert active.get("profile_revision") == "1.0.0", "revision recorded"
        assert "AT" in active.get("commands", {}), "commands populated"

    def test_load_missing_file_fails(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("profile.load /no/such/path.json")
        # Assert
        assert result.success is False, "missing file fails"
        assert "not found" in result.error.lower(), "error names not found"

    def test_load_no_args_returns_usage(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("profile.load")
        # Assert
        assert result.success is False, "no args fails"
        assert "usage" in result.error.lower(), "error names usage"

    def test_load_invalid_json_fails(self, env, tmp_path):
        # Arrange — write a malformed profile
        bad = tmp_path / "bad.profile.json"
        bad.write_text("{ this is not valid", encoding="utf-8")
        eng, _ctx, _output = env
        # Act
        result = eng.dispatch(f"profile.load {bad}")
        # Assert
        assert result.success is False, "malformed parse fails"
        assert "parse" in result.error.lower(), "error names parse"

    def test_load_schema_invalid_refuses(self, env, tmp_path):
        # Arrange — schema-invalid profile
        bad = tmp_path / "schema_bad.profile.json"
        bad.write_text(
            json.dumps(
                {
                    "profile_version": 2,
                    "commands": {"X": {"help": "h", "safety": "made-up"}},
                }
            ),
            encoding="utf-8",
        )
        eng, ctx, _output = env
        # Act
        result = eng.dispatch(f"profile.load {bad}")
        # Assert
        assert result.success is False, "schema error refuses load"
        assert ctx.ns("active_profile") == {}, "active profile not set on failure"

    def test_load_replaces_previous_active_profile(self, env):
        # Arrange — load one, then another
        eng, ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        rev1 = ctx.ns("active_profile").get("profile_revision")
        # Act — load a second profile
        eng.dispatch(f"profile.load {FIXTURES / 'register_psu.profile.json'}")
        rev2 = ctx.ns("active_profile").get("profile_revision")
        # Assert
        assert rev1 != rev2, "active profile replaced"
        assert rev2 == "0.3.0", "second profile's revision in effect"


# ── /profile.info ──────────────────────────────────────────────────────────


class TestProfileInfo:
    def test_info_with_no_loaded_profile(self, env):
        # Arrange / Act
        eng, _ctx, output = env
        result = eng.dispatch("profile.info")
        # Assert
        assert result.success is True, "info still succeeds with no profile"
        full = " ".join(t for t, _ in output)
        assert "no profile" in full.lower(), "informs user no profile loaded"

    def test_info_after_load_shows_metadata(self, env):
        # Arrange
        eng, _ctx, output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        output.clear()
        # Act
        eng.dispatch("profile.info")
        full = " ".join(t for t, _ in output)
        # Assert
        assert "1.0.0" in full, "shows revision"
        assert "Generic AT Modem" in full, "shows device name"
        assert "115200" in full, "shows baud"

    def test_info_value_is_command_count(self, env):
        # Arrange
        eng, _ctx, _output = env
        eng.dispatch(f"profile.load {FIXTURES / 'at_modem.profile.json'}")
        # Act
        result = eng.dispatch("profile.info")
        # Assert
        assert int(result.value) == 5, "value is the command count"
