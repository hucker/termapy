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


# ── Disconnect clears device-specific state ─────────────────────────────────


class TestDisconnectClearsDeviceState:
    """Disconnect wipes per-device namespaces and MCP-specific tracking.

    Pinning the contract: after a disconnect, ``active_profile`` and
    ``target_commands`` are empty, and the MCP host's banner/expect/
    async-event/last-command attributes are reset.  Carrying any of
    these across a port switch is the bug that motivated this whole
    cleanup -- the next connect lands fresh.
    """

    @pytest.fixture
    def host(self, tmp_path):
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["auto_include_on_connect"] = False  # focus the test
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        return MCPHost(cfg, str(config_path), verbose=False)

    def test_active_profile_cleared_on_disconnect(self, host):
        # Arrange -- connect, load a profile, verify it stuck
        host._connect()
        host.repl.dispatch(f"profile.load {DEMO_NDJSON_PROFILE}")
        actual_before = host.ctx.ns("active_profile").get("commands", {})
        assert actual_before, "precondition: profile loaded"
        # Act
        host._disconnect()
        # Assert
        actual_after = host.ctx.ns("active_profile")
        expected: dict = {}
        assert actual_after == expected, (
            "active_profile wiped on disconnect (no leak to next device)"
        )

    def test_target_commands_cleared_on_disconnect(self, host):
        # Arrange -- connect and seed target_commands directly
        from termapy.plugins import TargetCommand
        host._connect()
        host.ctx.ns("target_commands").update({
            "AT+FOO": TargetCommand(name="AT+FOO", help="x"),
        })
        actual_before = len(host.ctx.ns("target_commands"))
        assert actual_before == 1, "precondition: target_commands seeded"
        # Act
        host._disconnect()
        # Assert
        actual_after = host.ctx.ns("target_commands")
        expected: dict = {}
        assert actual_after == expected, "target_commands cleared on disconnect"

    def test_banner_state_cleared_on_disconnect(self, host):
        # Arrange -- simulate a banner observation, then disconnect
        host._connect()
        host._banner_seen = True
        host._banner_text = "READY 1.2.3"
        # Act
        host._disconnect()
        # Assert
        assert host._banner_seen is False, "banner_seen reset"
        assert host._banner_text == "", "banner_text reset"

    def test_event_buffers_cleared_on_disconnect(self, host):
        # Arrange -- simulate captured events, then disconnect
        host._connect()
        host._last_command = {"cmd": "AT+TEMP", "success": True}
        host._expect_history.append({"match": "OK"})
        host._async_events.append({"line": "unsolicited"})
        host._async_errors.append({"code": "E001"})
        # Act
        host._disconnect()
        # Assert -- four independent buffers, each must reset
        assert host._last_command is None, "last_command reset"
        assert host._expect_history == [], "expect_history cleared"
        assert host._async_events == [], "async_events cleared"
        assert host._async_errors == [], "async_errors cleared"


# ── MCP auto-load profile on connect ────────────────────────────────────────


class TestAutoLoadProfileOnConnect:
    """``--mcp`` auto-loads a v2 profile on connect.

    Two lookup paths: explicit ``cfg.profile_path`` wins; otherwise
    convention ``<cfg_dir>/<cfg_name>.profile.json``.  Missing file is
    a non-fatal log line, never a connect failure.
    """

    def _write_profile(self, path: Path) -> None:
        """Write a minimal v2 profile to the given path."""
        path.write_text(json.dumps({
            "profile_version": 2,
            "profile_revision": "1.0.0",
            "profile_date": "2026-05-03",
            "device": {"name": "Test Device"},
            "transport": {"protocol": "text"},
            "commands": {
                "PING": {"help": "ping the device", "safety": "readonly"},
            },
        }))

    @pytest.fixture
    def cfg_dir(self, tmp_path):
        cfg_dir = tmp_path / "cfg"
        cfg_dir.mkdir()
        for sub in ("plugin", "ss", "run", "cap"):
            (cfg_dir / sub).mkdir(exist_ok=True)
        return cfg_dir

    def test_explicit_profile_path_loads(self, cfg_dir):
        # Arrange -- write profile at a non-conventional location
        profile = cfg_dir / "weird-name.profile.json"
        self._write_profile(profile)
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["profile_path"] = str(profile)
        cfg["auto_include_on_connect"] = False
        config_path = cfg_dir / "test.cfg"
        config_path.write_text(json.dumps(cfg))
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            # Act
            h._connect()
            time.sleep(0.1)
            # Assert
            actual = h.ctx.ns("active_profile").get("commands", {})
            expected_keys = {"PING"}
            assert set(actual.keys()) == expected_keys, (
                "explicit cfg.profile_path loaded on connect"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_convention_profile_loads_when_no_explicit_path(self, cfg_dir):
        # Arrange -- profile at <cfg_dir>/<cfg_name>.profile.json
        profile = cfg_dir / "test.profile.json"
        self._write_profile(profile)
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["auto_include_on_connect"] = False
        # profile_path empty -> falls back to convention
        config_path = cfg_dir / "test.cfg"
        config_path.write_text(json.dumps(cfg))
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            # Act
            h._connect()
            time.sleep(0.1)
            # Assert
            actual = h.ctx.ns("active_profile").get("commands", {})
            expected_keys = {"PING"}
            assert set(actual.keys()) == expected_keys, (
                "convention <cfg>.profile.json loaded on connect"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_no_profile_file_is_non_fatal(self, cfg_dir):
        # Arrange -- no profile file at any expected location
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["auto_include_on_connect"] = False
        config_path = cfg_dir / "test.cfg"
        config_path.write_text(json.dumps(cfg))
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            # Act -- connect should still succeed
            connected = h._connect()
            time.sleep(0.1)
            # Assert
            assert connected, "connect succeeds even with no profile"
            actual = h.ctx.ns("active_profile")
            expected: dict = {}
            assert actual == expected, "no profile loaded -> active_profile empty"
        finally:
            if h.engine.is_connected:
                h._disconnect()

    def test_auto_load_method_lives_on_mcphost_only(self):
        # Arrange / Act / Assert -- the auto-load hook is MCP-only by
        # construction; TUI/CLI inherit from TerminalHost and never get
        # it.  Pinning the invariant so a future "let's hoist this to
        # the base class" refactor has to consciously break this test.
        from termapy.terminal_host import TerminalHost
        assert not hasattr(TerminalHost, "_on_connect_auto_load_profile"), (
            "TerminalHost must NOT have profile auto-load -- "
            "TUI/CLI stay text-to-text by design"
        )
        assert hasattr(MCPHost, "_on_connect_auto_load_profile"), (
            "MCPHost auto-loads profiles on connect"
        )

    def test_explicit_path_beats_convention(self, cfg_dir):
        # Arrange -- both files exist, explicit must win
        explicit = cfg_dir / "explicit.profile.json"
        self._write_profile(explicit)
        # Convention path exists with a DIFFERENT command name so we can tell
        convention = cfg_dir / "test.profile.json"
        convention.write_text(json.dumps({
            "profile_version": 2,
            "profile_revision": "1.0.0",
            "profile_date": "2026-05-03",
            "device": {"name": "Convention Device"},
            "transport": {"protocol": "text"},
            "commands": {"DIFFERENT": {"help": "from convention"}},
        }))
        cfg = dict(DEFAULT_CFG)
        cfg["port"] = "DEMO"
        cfg["profile_path"] = str(explicit)  # explicit wins
        cfg["auto_include_on_connect"] = False
        config_path = cfg_dir / "test.cfg"
        config_path.write_text(json.dumps(cfg))
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            # Act
            h._connect()
            time.sleep(0.1)
            # Assert -- "PING" from explicit, NOT "DIFFERENT" from convention
            actual = h.ctx.ns("active_profile").get("commands", {})
            assert "PING" in actual, "explicit profile loaded"
            assert "DIFFERENT" not in actual, (
                "convention NOT loaded when explicit path is set"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()


# ── Helpers ─────────────────────────────────────────────────────────────────


def host_target_count(host: MCPHost) -> int:
    """Return the count of target_commands the host has loaded."""
    return len(host.ctx.ns("target_commands"))
