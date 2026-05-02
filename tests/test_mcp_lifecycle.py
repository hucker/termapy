"""Tests for Phase 6 lifecycle: transport-apply, auto-include, banner watcher.

Three pieces:
- /profile.load applies transport rules to live cfg.
- on_connect fires auto-include when configured.
- on_connect spawns a banner watcher when the active profile declares one.

Signal-handler tests are deliberately omitted: signal-driven shutdown
is exercised end-to-end by the existing test_mcp_entry tests
(--mcp with stdin EOF) and is platform-sensitive enough that adding
finer-grained pytest coverage here is more pain than insight.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.catalog import build_device_state  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402
from termapy.profile import (  # noqa: E402
    SERIAL_LEVEL_TRANSPORT_KEYS,
    apply_profile_transport,
)


DEMO_NDJSON_PROFILE = (
    Path(__file__).parent.parent
    / "src"
    / "termapy"
    / "builtins"
    / "demo"
    / "demo_ndjson.profile.json"
)


# ── apply_profile_transport pure-function tests ─────────────────────────────


class TestApplyProfileTransport:
    def test_writes_each_recognized_key(self):
        # Arrange
        applied: dict[str, object] = {}

        def fake_apply(key, val):
            applied[key] = val

        transport = {
            "baud_rate": 9600,
            "byte_size": 7,
            "parity": "E",
            "stop_bits": 2,
            "flow_control": "rtscts",
            "encoding": "latin-1",
            "line_ending_send": "\n",
            "inter_command_delay_ms": 25,
            "default_response_timeout_ms": 1500,
        }
        # Act
        changes = apply_profile_transport(transport, fake_apply)
        # Assert
        assert applied["baud_rate"] == 9600, "baud_rate applied"
        assert applied["line_ending"] == "\n", "line_ending_send maps to line_ending"
        assert applied["encoding"] == "latin-1", "encoding applied"
        assert applied["byte_size"] == 7, "byte_size applied"
        assert len(changes) == 9, "every input key produces one change record"

    def test_unknown_key_in_transport_is_ignored(self):
        # Arrange
        applied: dict[str, object] = {}
        transport = {"protocol": "ndjson", "field_routing": {"id": "id"}}
        # Act
        changes = apply_profile_transport(transport, lambda k, v: applied.setdefault(k, v))
        # Assert -- protocol/field_routing aren't cfg keys; ignored
        assert applied == {}, "unknown keys silently ignored"
        assert changes == {}, "no changes recorded"

    def test_serial_level_keys_classification(self):
        # Arrange / Act / Assert
        for key in ("baud_rate", "byte_size", "parity", "stop_bits", "flow_control"):
            assert key in SERIAL_LEVEL_TRANSPORT_KEYS, (
                f"{key} flagged as serial-level"
            )
        for key in ("line_ending", "encoding", "inter_command_delay_ms"):
            assert key not in SERIAL_LEVEL_TRANSPORT_KEYS, (
                f"{key} is termapy-level, not serial-level"
            )

    def test_non_dict_transport_returns_empty(self):
        # Arrange / Act / Assert -- defensive: malformed transport doesn't crash
        result = apply_profile_transport("not a dict", lambda k, v: None)  # type: ignore[arg-type]
        assert result == {}, "non-dict input is a no-op"


# ── /profile.load applies transport ────────────────────────────────────────


class TestProfileLoadAppliesTransport:
    @pytest.fixture
    def host(self, tmp_path):
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = ""
        # Default cfg has line_ending="\r"; profile sets "\n".
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        return MCPHost(cfg, str(config_path), verbose=False)

    def test_load_demo_ndjson_changes_line_ending(self, host):
        # Arrange — verify before
        assert host.repl.cfg.get("line_ending") == "\r", "default \\r"
        # Act
        host.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        # Assert
        assert host.repl.cfg.get("line_ending") == "\n", (
            "profile.load applied line_ending_send -> cfg.line_ending"
        )

    def test_load_demo_ndjson_changes_encoding(self, host):
        # Arrange
        host.repl._cfg_data["encoding"] = "ascii"
        # Act
        host.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        # Assert
        assert host.repl.cfg.get("encoding") == "utf-8", "encoding applied"

    def test_load_applies_baud_rate(self, host):
        # Arrange
        host.repl._cfg_data["baud_rate"] = 9600
        # Act
        host.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        # Assert
        assert host.repl.cfg.get("baud_rate") == 115200, (
            "baud_rate applied (next-connect semantics; cfg updates immediately)"
        )


# ── auto_include_on_connect ─────────────────────────────────────────────────


class TestAutoIncludeOnConnect:
    @pytest.fixture
    def host(self, tmp_path):
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        # DEMO answers AT+HELP.JSON with a JSON catalog.
        cfg["device_json_cmd"] = "AT+HELP.JSON"
        cfg["auto_include_on_connect"] = True
        cfg["line_ending"] = "\r\n"
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            h._connect()
            time.sleep(0.3)  # let auto-include run
            yield h
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_auto_include_populates_target_commands(self, host):
        # Arrange / Act — connection already happened in fixture
        target = host.ctx.ns("target_commands")
        # Assert -- DEMO's AT+HELP.JSON yields a non-empty catalog
        assert len(target) > 0, (
            "auto_include_on_connect populated target_commands"
        )

    def test_disabled_skips_auto_include(self, tmp_path):
        # Arrange
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["device_json_cmd"] = "AT+HELP.JSON"
        cfg["auto_include_on_connect"] = False
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            h._connect()
            time.sleep(0.2)
            # Act / Assert
            assert host_target_count(h) == 0, "no auto-include when disabled"
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_no_device_json_cmd_skips_auto_include(self, tmp_path):
        # Arrange
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["device_json_cmd"] = ""
        cfg["auto_include_on_connect"] = True
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            h._connect()
            time.sleep(0.2)
            # Act / Assert
            assert host_target_count(h) == 0, (
                "no device_json_cmd = no auto-include"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()


# ── Banner watcher ──────────────────────────────────────────────────────────


class TestBannerWatcher:
    @pytest.fixture
    def host_with_profile(self, tmp_path):
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO_JSON"
        cfg["line_ending"] = "\n"
        cfg["device_json_cmd"] = ""  # don't trigger auto-include
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        # Load the DEMO_JSON profile so banner pattern is active.
        h.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        return h

    def test_banner_seen_after_connect(self, host_with_profile):
        # Arrange
        h = host_with_profile
        try:
            # Act -- DEMO_JSON sends ready banner immediately after open
            h._connect()
            # Wait up to 3s for the banner watcher's thread to record.
            for _ in range(30):
                if h._banner_seen:
                    break
                time.sleep(0.1)
            # Assert
            assert h._banner_seen is True, "watcher saw the ready banner"
            assert "ready" in h._banner_text.lower(), "banner text recorded"
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_device_state_resource_reflects_banner(self, host_with_profile):
        # Arrange
        h = host_with_profile
        try:
            h._connect()
            for _ in range(30):
                if h._banner_seen:
                    break
                time.sleep(0.1)
            # Act
            state = build_device_state(
                h.ctx,
                banner_seen=h._banner_seen,
                banner_text=h._banner_text,
            )
            # Assert
            assert state["device"]["banner_seen"] is True, (
                "device_state mirrors host's banner_seen flag"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()


# ── Helpers ─────────────────────────────────────────────────────────────────


def host_target_count(host: MCPHost) -> int:
    """Return the count of target_commands the host has loaded."""
    return len(host.ctx.ns("target_commands"))
