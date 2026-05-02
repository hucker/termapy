"""Tests for the bundled DEMO profiles (Phase 5).

Termapy ships two profiles alongside the simulator:

- ``src/termapy/builtins/demo/demo.profile.json``        (legacy text DEMO)
- ``src/termapy/builtins/demo/demo_ndjson.profile.json`` (modern DEMO_JSON)

These tests verify both profiles validate against the schema, load
via ``Profile.load()``, and are accessible by /profile.load.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from termapy.profile import Profile, load_profile, validate_profile


DEMO_DIR = (
    Path(__file__).parent.parent
    / "src"
    / "termapy"
    / "builtins"
    / "demo"
)
DEMO_PROFILE = DEMO_DIR / "demo.profile.json"
DEMO_NDJSON_PROFILE = DEMO_DIR / "demo_ndjson.profile.json"


# ── Both profiles validate against the schema ───────────────────────────────


@pytest.mark.parametrize(
    "path",
    [DEMO_PROFILE, DEMO_NDJSON_PROFILE],
    ids=["demo", "demo_ndjson"],
)
class TestBundledProfiles:
    def test_file_exists(self, path):
        # Arrange / Act / Assert
        assert path.exists(), f"bundled profile missing: {path}"

    def test_loads_and_validates(self, path):
        # Arrange / Act
        profile = load_profile(path)
        result = validate_profile(profile)
        # Assert
        assert result.ok is True, (
            f"{path.name} should validate; errors: {result.errors}"
        )

    def test_profile_dataclass_load(self, path):
        # Arrange / Act
        p = Profile.load(path)
        # Assert
        assert p.path == path, "path attached"
        assert p.revision, "has profile_revision"
        assert p.commands, "has commands"


# ── DEMO profile (legacy text) shape ────────────────────────────────────────


class TestDemoProfileShape:
    def test_protocol_is_text(self):
        # Arrange / Act
        profile = load_profile(DEMO_PROFILE)
        # Assert
        assert profile["transport"]["protocol"] == "text", (
            "demo.profile.json describes the text DEMO simulator"
        )

    def test_includes_at_command(self):
        # Arrange / Act
        profile = load_profile(DEMO_PROFILE)
        # Assert
        assert "AT" in profile["commands"], "AT command in catalog"
        assert profile["commands"]["AT"]["response"]["format"] == "literal", (
            "AT returns the literal OK"
        )

    def test_at_temp_uses_regex_response(self):
        # Arrange / Act
        profile = load_profile(DEMO_PROFILE)
        # Assert
        cmd = profile["commands"]["AT+TEMP"]
        assert cmd["response"]["format"] == "regex", "AT+TEMP uses regex parsing"
        assert "celsius" in cmd["response"]["types"], "celsius typed"


# ── DEMO_JSON profile (modern NDJSON) shape ─────────────────────────────────


class TestDemoNdjsonProfileShape:
    def test_protocol_is_ndjson(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        assert profile["transport"]["protocol"] == "ndjson", (
            "demo_ndjson.profile.json describes the NDJSON DEMO_JSON simulator"
        )

    def test_field_routing_uses_default_keys(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        fr = profile["transport"].get("field_routing", {})
        assert fr.get("response_id") == "id", "default id field"
        assert fr.get("error_field") == "error", "default error field"
        assert fr.get("event_field") == "event", "default event field"

    def test_set_threshold_has_typed_args(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        ta = profile["commands"]["set_threshold"]["typed_args"]
        assert len(ta) == 1, "one typed arg"
        assert ta[0]["name"] == "celsius", "celsius"
        assert ta[0]["type"] == "float", "float type"

    def test_destructive_commands_marked(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        # set_threshold, set_mode, calibrate, reset are all destructive.
        for name in ("set_threshold", "set_mode", "calibrate", "reset"):
            assert (
                profile["commands"][name].get("safety") == "destructive"
            ), f"{name} is destructive"

    def test_reset_is_fire_and_forget(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        assert profile["commands"]["reset"]["response"]["format"] == "none", (
            "reset = fire-and-forget per the simulator"
        )


# ── Bundled profiles are loadable via /profile.load (smoke) ────────────────


pytest_mcp = pytest.importorskip(
    "mcp", reason="mcp SDK not installed; install with [mcp] extra"
)


class TestProfileLoadAcceptsBundled:
    @pytest.fixture
    def host(self, tmp_path):
        from termapy.defaults import DEFAULT_CFG
        from termapy.mcp.server import MCPHost

        cfg = dict(DEFAULT_CFG)
        cfg["port"] = ""
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        return MCPHost(cfg, str(config_path), verbose=False)

    def test_load_demo_profile(self, host):
        # Arrange / Act
        result = host.repl.dispatch(f"profile.load {DEMO_PROFILE}")
        # Assert
        assert result.success is True, (
            f"demo.profile.json loads via /profile.load; error: {result.error}"
        )

    def test_load_demo_ndjson_profile(self, host):
        # Arrange / Act
        result = host.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        # Assert
        assert result.success is True, (
            f"demo_ndjson.profile.json loads via /profile.load; "
            f"error: {result.error}"
        )
