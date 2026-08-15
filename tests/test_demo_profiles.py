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
    def test_set_threshold_has_typed_args(self):
        # Arrange / Act
        profile = load_profile(DEMO_NDJSON_PROFILE)
        # Assert
        ta = profile["commands"]["set_threshold"]["typed_args"]
        assert len(ta) == 1, "one typed arg"
        assert ta[0]["name"] == "celsius", "celsius"
        assert ta[0]["type"] == "float", "float type"

    def test_safety_classifications(self):
        # Arrange
        profile = load_profile(DEMO_NDJSON_PROFILE)
        commands = profile["commands"]
        # Assert -- four-tier safety taxonomy (readonly/safe/mutable/
        # destructive).  Only destructive triggers the MCP confirmation
        # gate; mutable changes state but is reversible (set_threshold/
        # set_mode can be called again with a different value).
        expected_safety = {
            "set_threshold": "mutable",
            "set_mode": "mutable",
            "calibrate": "destructive",  # alters stored cal data
            "reset": "destructive",       # loses RAM state
        }
        for name, expected in expected_safety.items():
            actual = commands[name].get("safety")
            assert actual == expected, (
                f"{name} expected safety={expected!r}, got {actual!r}"
            )

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
    def host(self, tmp_path, monkeypatch):
        from termapy.defaults import DEFAULT_CFG
        from termapy.mcp.server import MCPHost

        # Loading a bundled profile by its absolute path is the feature
        # under test; run unconfined so the MCP filesystem sandbox (tested
        # in test_fs_sandbox.py) doesn't refuse the fixture path.
        monkeypatch.setattr("termapy.env_flags.MCP_FS_UNCONFINED", True)
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
