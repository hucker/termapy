"""Tests for MCP lifecycle: profile-load rejection, on-connect fetch, banner watcher.

Three pieces:
- A profile carrying a `transport` block is rejected by the loader
  with a clear error (the transport block was retired; wire-level
  settings live in cfg now).
- on_connect_cmd entries fire after connect (used for the v2-only
  device-fetch path: ``/profile.load cmd=<command>``).
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

from termapy.defaults import default_cfg  # noqa: E402
from termapy.mcp.catalog import build_device_state  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402
from termapy.profile import validate_profile  # noqa: E402

DEMO_NDJSON_PROFILE = (
    Path(__file__).parent.parent
    / "src"
    / "termapy"
    / "builtins"
    / "demo"
    / "demo_ndjson.profile.json"
)


@pytest.fixture(autouse=True)
def _unconfined_mcp(monkeypatch):
    """Run the lifecycle tests unconfined.

    These exercise profile-load-from-path, banners, and disconnect as
    *features*; loading the demo profile fixture (an absolute path) would
    otherwise be refused by the MCP filesystem sandbox.  The sandbox
    itself is tested in test_fs_sandbox.py.
    """
    monkeypatch.setattr("termapy.env_flags.MCP_FS_UNCONFINED", True)


# ── Profile schema rejects the retired `transport` block ────────────────────


class TestTransportBlockRejected:
    def test_profile_with_transport_block_fails_validation(self):
        # Arrange -- a minimal v2 profile that carries the retired
        # transport block.  This is exactly the shape that older
        # hand-rolled profiles would have on disk.
        profile = {
            "profile_version": 2,
            "transport": {"baud_rate": 9600},
            "commands": {"AT": {"help": "test"}},
        }
        # Act
        result = validate_profile(profile)
        # Assert -- not OK; error mentions the transport block by name
        # so the author can find what to remove.
        assert not result.ok, "transport block must fail validation"
        assert any("transport" in error for error in result.errors), (
            f"error must name 'transport'; got {result.errors}"
        )


# ── /profile.load cmd= via on_connect_cmd ───────────────────────────────────


class TestOnConnectFetchProfile:
    """The v2-only path: device-fetch on connect is composed via the
    existing on_connect_cmd machinery, NOT a dedicated cfg flag.
    Replaces the retired auto_include_on_connect / device_json_cmd pair."""

    def test_on_connect_cmd_fetches_profile_from_device(self, tmp_path):
        # Arrange -- DEMO answers AT+HELP.JSON with a v2 profile JSON.
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
        cfg["eol"] = "\r\n"
        cfg["mcp_on_connect_cmd"] = "/profile.load cmd=AT+HELP.JSON"
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        h = MCPHost(cfg, str(config_path), verbose=False)
        try:
            h._connect()
            time.sleep(0.3)  # let on_connect_cmd run
            # Assert -- active profile populated by the on_connect command
            active = h.ctx.ns("active_profile")
            commands = active.get("commands") or {}
            assert len(commands) > 0, (
                "/profile.load cmd= via mcp_on_connect_cmd populated active_profile"
            )
        finally:
            if h.engine.is_connected:
                h._disconnect()


# ── Banner watcher ──────────────────────────────────────────────────────────


class TestBannerWatcher:
    @pytest.fixture
    def host_with_profile(self, tmp_path):
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO_JSON"
        cfg["eol"] = "\n"
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

    Pinning the contract: after a disconnect, ``active_profile`` is
    empty, and the MCP host's banner/expect/async-event/last-command
    attributes are reset.  Carrying any of these across a port switch
    is the bug that motivated this whole cleanup -- the next connect
    lands fresh.
    """

    @pytest.fixture
    def host(self, tmp_path):
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
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
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
        cfg["profile_path"] = str(profile)
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
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
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
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
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
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
        cfg["profile_path"] = str(explicit)  # explicit wins
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


class TestOnConnectCmd:
    """``on_connect_cmd`` and ``mcp_on_connect_cmd`` firing in MCP mode.

    Pre-v15 MCP had a latent bug: ``on_connect_cmd`` was never fired
    on connect (TUI/CLI did fire it).  v15 fixes that AND adds
    ``mcp_on_connect_cmd`` for MCP-only device-setup commands.
    """

    def _make_host(self, tmp_path, **cfg_overrides) -> MCPHost:
        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
        cfg["eol"] = "\r\n"
        cfg.update(cfg_overrides)
        config_path = tmp_path / "cfg" / "test.cfg"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (config_path.parent / sub).mkdir(exist_ok=True)
        return MCPHost(cfg, str(config_path), verbose=False)

    def test_on_connect_cmd_fires_in_mcp(self, tmp_path):
        """v15 closes the latent bug: on_connect_cmd now fires in MCP."""
        # Arrange
        h = self._make_host(tmp_path, on_connect_cmd="/ver")

        # Act
        try:
            h._connect()
            time.sleep(0.2)
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        log = h._log_path.read_text(encoding="utf-8") if h._log_path else ""
        assert "$ /ver  (on_connect_cmd)" in log, \
            f"universal on_connect_cmd fires in MCP, log was:\n{log}"

    def test_mcp_on_connect_cmd_fires_in_mcp(self, tmp_path):
        """mcp_on_connect_cmd is the MCP-only device-setup hook."""
        # Arrange
        h = self._make_host(tmp_path, mcp_on_connect_cmd="/ver")

        # Act
        try:
            h._connect()
            time.sleep(0.2)
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        log = h._log_path.read_text(encoding="utf-8") if h._log_path else ""
        assert "$ /ver  (mcp_on_connect_cmd)" in log, \
            f"mcp_on_connect_cmd fires in MCP, log was:\n{log}"

    def test_universal_runs_before_mcp_only(self, tmp_path):
        """Ordering: on_connect_cmd first, then mcp_on_connect_cmd."""
        # Arrange
        h = self._make_host(
            tmp_path,
            on_connect_cmd="/cls",            # silly but distinguishable
            mcp_on_connect_cmd="/ver",
        )

        # Act
        try:
            h._connect()
            time.sleep(0.2)
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        log = h._log_path.read_text(encoding="utf-8") if h._log_path else ""
        cls_idx = log.find("$ /cls  (on_connect_cmd)")
        ver_idx = log.find("$ /ver  (mcp_on_connect_cmd)")
        assert cls_idx >= 0, f"universal cmd logged, log was:\n{log}"
        assert ver_idx >= 0, f"mcp-only cmd logged, log was:\n{log}"
        assert cls_idx < ver_idx, "universal fires before mcp-only"

    def test_empty_keys_are_noops(self, tmp_path):
        """No on-connect commands logged when both keys are empty."""
        # Arrange
        h = self._make_host(tmp_path, on_connect_cmd="", mcp_on_connect_cmd="")

        # Act
        try:
            h._connect()
            time.sleep(0.2)
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        log = h._log_path.read_text(encoding="utf-8") if h._log_path else ""
        assert "(on_connect_cmd)" not in log, "no universal cmd ran"
        assert "(mcp_on_connect_cmd)" not in log, "no mcp-only cmd ran"
