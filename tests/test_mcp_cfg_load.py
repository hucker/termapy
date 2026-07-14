"""Tests for /cfg.load via MCP frontend.

Verifies that ``MCPHost`` (via its inherited ``TerminalHost._load_config``
default) can hot-swap configs mid-session.  Before this branch the base
``_load_config`` returned a "not available in this frontend" failure;
the fix promotes the working machinery from ``CLITerminal`` up to
``TerminalHost`` so any headless host (CLI, MCP) gets it for free.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import default_cfg  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


def _make_cfg_dir(parent: Path, name: str, **overrides) -> Path:
    """Create a minimal cfg dir with the standard subfolders.

    Returns the path to the .cfg file.
    """
    cfg = default_cfg()
    cfg["serial"]["port"] = ""
    cfg.update(overrides)
    cfg_path = parent / name / f"{name}.cfg"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (cfg_path.parent / sub).mkdir(exist_ok=True)
    return cfg_path


@pytest.fixture
def two_cfgs(tmp_path, monkeypatch):
    """Create two cfg dirs and pin TERMAPY_CFG_DIR so resolve_config sees them."""
    monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
    cfg_a = _make_cfg_dir(tmp_path, "alpha", eol="\r")
    cfg_b = _make_cfg_dir(tmp_path, "beta", eol="\n")
    return cfg_a, cfg_b


@pytest.fixture
def host(two_cfgs):
    cfg_a, _ = two_cfgs
    cfg = json.loads(cfg_a.read_text())
    return MCPHost(cfg, str(cfg_a), verbose=False)


class TestMcpCfgLoad:
    def test_load_by_name_switches_cfg(self, host, two_cfgs):
        # Arrange
        cfg_a, cfg_b = two_cfgs

        # Act
        result = host._load_config("beta")

        # Assert
        assert result.success, f"load should succeed, got error: {result.error}"
        actual = host.config_path
        expected = str(cfg_b)
        assert actual == expected, "config_path now points at beta"

    def test_load_swaps_in_memory_cfg(self, host):
        # Arrange - alpha has line_ending=\r, beta has \n

        # Act
        host._load_config("beta")

        # Assert
        actual = host.cfg.get("eol")
        expected = "\n"
        assert actual == expected, "in-memory cfg replaced with beta's contents"

    def test_load_unknown_name_fails(self, host):
        # Act
        result = host._load_config("nonexistent_xyz")

        # Assert
        assert not result.success, "unknown name returns failure"
        assert "No config matching" in result.error, \
            f"error message names the missing cfg, got: {result.error}"

    def test_load_via_dispatch_works(self, host, two_cfgs):
        # Arrange - exercise the path end users hit: REPL dispatch of /cfg.load
        _, cfg_b = two_cfgs

        # Act - dispatch through ReplEngine just like an MCP run_command would
        result = host.repl.dispatch("cfg.load beta")

        # Assert
        assert result.success, f"dispatch should succeed, got: {result.error}"
        assert host.config_path == str(cfg_b), \
            "dispatched /cfg.load actually updated host state"

    def test_load_fires_on_config_load_hook(self, host, two_cfgs):
        # Arrange - register a marker hook so we can detect it firing
        from termapy.plugins import LifecycleHook

        fired = []
        host.repl._lifecycle_hooks.append(
            LifecycleHook(
                name="on_config_load",
                handler=lambda ctx: fired.append("yes"),
                source="test",
            )
        )

        # Act
        host._load_config("beta")

        # Assert
        assert fired == ["yes"], "on_config_load fired exactly once"

    def test_load_returns_path_in_value(self, host, two_cfgs):
        # Arrange
        _, cfg_b = two_cfgs

        # Act
        result = host._load_config("beta")

        # Assert
        actual = result.value
        expected = str(cfg_b)
        assert actual == expected, "result.value is the loaded cfg path"


class TestZeroConfigMcpHost:
    """Slot-pool case: ``termapy --mcp`` with no cfg starts cleanly,
    suppresses file logging, and routes logs to the cfg's mcp/ once
    /cfg.load runs.
    """

    @pytest.fixture
    def zero_host(self, tmp_path, monkeypatch):
        # Arrange - point cfg_dir at tmp_path and seed two cfgs
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
        _make_cfg_dir(tmp_path, "alpha")
        _make_cfg_dir(tmp_path, "beta")
        # MCPHost with no cfg loaded (mimics zero-config slot startup)

        cfg = default_cfg()
        return MCPHost(cfg, "", verbose=False)

    def test_starts_with_no_log_path(self, zero_host):
        # Assert
        assert zero_host._log_path is None, "no log path before cfg.load"
        assert zero_host._mcp_dir is None, "no mcp dir before cfg.load"

    def test_log_line_no_op_without_cfg(self, zero_host):
        # Act - should not raise even with _log_path=None
        zero_host._log_line("startup message")

        # Assert (no exception is the test; nothing to file-check)

    def test_load_routes_log_to_cfg_dir(self, zero_host, tmp_path):
        # Arrange - confirm starting state
        assert zero_host._log_path is None, "starts unbound"

        # Act
        zero_host._load_config("alpha")

        # Assert
        actual_log = zero_host._log_path
        expected_parent = tmp_path / "alpha" / "mcp"
        assert actual_log is not None, "log path now set"
        assert actual_log.parent == expected_parent, \
            f"log lives under cfg's mcp/, got: {actual_log}"

    def test_switch_re_routes_log(self, zero_host, tmp_path):
        # Arrange - load alpha first
        zero_host._load_config("alpha")
        first_log = zero_host._log_path
        assert first_log is not None and "alpha" in str(first_log), \
            "alpha log path"

        # Act - switch to beta
        zero_host._load_config("beta")

        # Assert
        actual_log = zero_host._log_path
        assert "beta" in str(actual_log), \
            f"log re-routed to beta cfg, got: {actual_log}"
        assert "alpha" not in str(actual_log), \
            "old alpha log path cleared"


class TestCfgLoadIdentity:
    """Regression: after /cfg.load, host.cfg and the engine's cfg must be
    ONE object.

    Pre-fix bug: ``_switch_to_cfg_path`` rebound ``self.cfg`` to the dict
    freshly loaded from disk while ``replace_cfg`` refreshed the engine's
    original dict in place.  From then on every session write
    (``_apply_cfg``: /cfg.auto, /term.request, port changes) landed in
    the engine's dict while ctx.cfg -- rebuilt from host.cfg -- served a
    stale snapshot.  Symptom: ``/cfg.auto default_response_timeout_ms
    5000`` printed ``(session)`` but ``/cfg default_response_timeout_ms``
    still answered 1000.
    """

    def test_host_and_engine_share_one_dict_after_load(self, host):
        # Act
        host._load_config("beta")

        # Assert -- identity, not equality: the fixed invariant
        assert host.cfg is host.repl._cfg_data, \
            "host.cfg rebinds to the engine's dict, not the transient load"

    def test_session_set_visible_to_cfg_query_after_load(self, host):
        # Arrange
        host._load_config("beta")

        # Act -- the user-facing sequence: /cfg.auto then /cfg <key>
        set_result = host.repl.dispatch(
            "cfg.auto default_response_timeout_ms 5000"
        )
        query = host.repl.dispatch("cfg default_response_timeout_ms")

        # Assert
        assert set_result.success, f"set failed: {set_result.error}"
        actual = query.value
        expected = "5000"
        assert actual == expected, \
            "session set is visible through the ctx.cfg read path"


class TestCfgLoadFiresOnConnected:
    """Regression: /cfg.load auto-connect must call _on_connected().

    Pre-fix bug: _switch_to_cfg_path inlined ``engine.connect()`` and
    fired the on_connect lifecycle hook but did NOT call
    ``self._on_connected()`` -- so MCPHost's overrides
    (``_on_connect_auto_load_profile``, ``_on_connect_auto_include``,
    ``_on_connect_run_commands``, ``_on_connect_banner_watch``) were
    silently skipped on cfg-load reconnects.  Symptom: setting
    ``mcp_on_connect_cmd: "echo off"`` in a cfg had no effect on a
    /cfg.load reconnect.

    Fix: replace the inline connect block with ``self._connect()`` so
    the same lifecycle that fires on a fresh /port.connect also fires
    on a /cfg.load that auto-connects.
    """

    def _make_cfg(self, parent, name, **overrides):

        cfg = default_cfg()
        cfg["serial"]["port"] = "DEMO"
        cfg["eol"] = "\r\n"
        cfg["auto_connect"] = True
        cfg.update(overrides)
        cfg_path = parent / name / f"{name}.cfg"
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg))
        for sub in ("plugin", "ss", "run", "cap"):
            (cfg_path.parent / sub).mkdir(exist_ok=True)
        return cfg_path

    def test_cfg_load_fires_mcp_on_connect_cmd(self, tmp_path, monkeypatch):
        # Arrange - cfg with mcp_on_connect_cmd set, monkeypatched cfg_dir
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
        self._make_cfg(tmp_path, "target", mcp_on_connect_cmd="/ver")

        # Build host on a different cfg with no on-connect cmds
        unrelated = self._make_cfg(tmp_path, "unrelated")
        cfg = json.loads(unrelated.read_text())
        h = MCPHost(cfg, str(unrelated), verbose=False)

        # Act - /cfg.load to the target cfg, which auto-connects
        try:
            h._connect()  # initial connect
            time.sleep(0.1)
            result = h._load_config("target")
            time.sleep(0.2)  # let on_connect fire
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        assert result.success, f"cfg.load succeeded: {result.error}"
        # Log path now points at the target cfg's mcp/ dir
        assert h._log_path is not None, "log path set after switch"
        log = h._log_path.read_text(encoding="utf-8")
        assert "$ /ver  (mcp_on_connect_cmd)" in log, (
            f"mcp_on_connect_cmd fired on cfg.load reconnect, log was:\n{log}"
        )

    def test_cfg_load_fires_on_connect_cmd(self, tmp_path, monkeypatch):
        """Same regression for the universal on_connect_cmd."""
        # Arrange
        monkeypatch.setenv("TERMAPY_CFG_DIR", str(tmp_path))
        self._make_cfg(tmp_path, "target", on_connect_cmd="/ver")
        unrelated = self._make_cfg(tmp_path, "unrelated")
        cfg = json.loads(unrelated.read_text())
        h = MCPHost(cfg, str(unrelated), verbose=False)

        # Act
        try:
            h._connect()
            time.sleep(0.1)
            h._load_config("target")
            time.sleep(0.2)
        finally:
            if h.engine.is_connected:
                h._disconnect()

        # Assert
        log = h._log_path.read_text(encoding="utf-8") if h._log_path else ""
        assert "$ /ver  (on_connect_cmd)" in log, (
            f"on_connect_cmd fired on cfg.load reconnect, log was:\n{log}"
        )
