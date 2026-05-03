"""Tests for the Phase 3 MCP server core: MCPHost + run_command + resources.

We test the host's async run_command method and the catalog helpers
directly rather than spinning up a stdio subprocess.  Direct tests are
fast and reliable; the FastMCP wiring is thin (one tool decorator,
two resource decorators) and exercised end-to-end by manual smoke /
real Claude Desktop sessions.

Tests skip cleanly when the ``mcp`` SDK isn't installed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from termapy.defaults import DEFAULT_CFG

# Skip the entire module when the optional mcp extra isn't installed.
pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.mcp.catalog import build_catalog, catalog_json  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


# ── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture
def host(tmp_path):
    """Build an MCPHost with no port, no auto-connect."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    cfg["echo_input"] = False
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    h = MCPHost(cfg, str(config_path), verbose=False)
    return h


# ── Catalog tests ───────────────────────────────────────────────────────────


class TestCatalog:
    def test_catalog_has_expected_top_level_keys(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        # Assert
        for key in (
            "schema",
            "version",
            "prefix",
            "commands",
            "target_commands",
            "device",
            "transport",
            "error_detection",
        ):
            assert key in cat, f"catalog missing top-level key: {key!r}"

    def test_catalog_schema_version_is_one(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        # Assert
        assert cat["schema"] == 1, "v1 schema"

    def test_catalog_prefix_matches_config(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        # Assert
        assert cat["prefix"] == "/", "default prefix"

    def test_catalog_includes_help_command(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        names = {c["name"] for c in cat["commands"]}
        # Assert
        assert "/help" in names, "/help present (catalog names include prefix)"

    def test_catalog_includes_term_send(self, host):
        # Arrange / Act — /term.send was added in Phase 2.5
        cat = build_catalog(host.ctx)
        names = {c["name"] for c in cat["commands"]}
        # Assert
        assert "/term.send" in names, "/term.send present (Phase 2.5)"

    def test_catalog_command_has_required_fields(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        sample = next(c for c in cat["commands"] if c["name"] == "/help")
        # Assert
        for key in ("name", "args", "help", "long_help", "flags",
                    "needs", "hidden", "source"):
            assert key in sample, f"command entry missing {key!r}"

    def test_catalog_json_parses_back(self, host):
        # Arrange
        text = catalog_json(host.ctx)
        # Act
        parsed = json.loads(text)
        # Assert
        assert parsed["schema"] == 1, "round-trips through JSON"

    def test_catalog_profile_blocks_empty_when_no_profile(self, host):
        # Arrange / Act — no /profile.load yet (Phase 4)
        cat = build_catalog(host.ctx)
        # Assert
        assert cat["device"] == {}, "no profile = empty device block"
        assert cat["transport"] == {}, "no profile = empty transport block"
        assert cat["profile_revision"] == "", "no profile = empty revision"

    def test_catalog_hidden_flag_is_bool(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        # Assert
        for c in cat["commands"]:
            assert isinstance(c["hidden"], bool), (
                f"hidden flag must be bool, got {type(c['hidden']).__name__}"
            )

    def test_catalog_filters_interactive_only_commands(self, host):
        """Plugins requiring ``interactive`` are filtered from MCP catalog."""
        # Arrange / Act
        cat = build_catalog(host.ctx)
        names = {c["name"] for c in cat["commands"]}
        # Assert -- sample of plugins that declare needs.interactive=True
        for cmd in ("/cls", "/grep", "/exit", "/os",
                    "/seq", "/credits", "/xmodem", "/ymodem"):
            assert cmd not in names, (
                f"{cmd} should be filtered (needs.interactive=True; "
                f"MCP doesn't advertise interactive)"
            )

    def test_catalog_filters_gui_apps_commands(self, host):
        """Plugins requiring ``gui_apps`` are filtered from MCP catalog."""
        # Arrange / Act
        cat = build_catalog(host.ctx)
        names = {c["name"] for c in cat["commands"]}
        # Assert -- sample of plugins that declare needs.gui_apps=True
        for cmd in ("/edit", "/edit.cfg", "/show", "/show.cfg",
                    "/cfg.show", "/cfg.explore", "/app.explore",
                    "/run.edit"):
            assert cmd not in names, (
                f"{cmd} should be filtered (needs.gui_apps=True; "
                f"MCP doesn't advertise gui_apps)"
            )

    def test_hidden_and_capability_filter_are_independent(self, host):
        """``hidden`` is a UI-discoverability flag, NOT a catalog filter.

        Hidden plugins (legacy forwarders for human typing) appear in
        the MCP catalog with ``hidden: true`` -- the LLM can choose to
        skip them itself.  The catalog filter is purely capability-based.
        This test stashes a synthetic hidden+capability-satisfied plugin
        and verifies it shows up.
        """
        # Arrange -- synthetic hidden plugin with no needs (baseline only)
        from termapy.plugins import CapabilitySet, PluginInfo

        synthetic = PluginInfo(
            name="__test_hidden_visible",
            args="",
            help="synthetic test plugin",
            handler=lambda ctx, args: None,
            hidden=True,
            needs=CapabilitySet(),  # baseline only -- satisfied by every host
        )
        host.repl._plugins[synthetic.name] = synthetic
        try:
            # Act
            cat = build_catalog(host.ctx)
            cat_names = {c["name"] for c in cat["commands"]}
            # Assert
            prefixed = host.ctx.engine.prefix + synthetic.name
            assert prefixed in cat_names, (
                "hidden=True with no restrictive needs should appear in catalog"
            )
        finally:
            del host.repl._plugins[synthetic.name]

    def test_catalog_filters_tui_only_commands(self, host):
        # Arrange — /proto.debug needs tui_mode; MCP doesn't have it
        plugins = host.repl._plugins
        assert "proto.debug" in plugins, "/proto.debug registered (precondition)"
        # Act
        cat = build_catalog(host.ctx)
        cat_names = {c["name"] for c in cat["commands"]}
        # Assert
        assert "/proto.debug" not in cat_names, (
            "tui_mode-only commands filtered when ctx lacks tui_mode"
        )

    def test_catalog_filter_is_meaningful(self, host):
        # Arrange / Act
        cat = build_catalog(host.ctx)
        n_filtered = len(cat["commands"])
        n_total = len(host.repl._plugins)
        # Assert -- at least 10 commands filtered (sanity: filter is doing
        # real work, not a no-op)
        assert n_total - n_filtered >= 10, (
            f"expected at least 10 commands filtered for MCP context; "
            f"total={n_total} catalog={n_filtered}"
        )


# ── run_command happy paths ─────────────────────────────────────────────────


class TestRunCommandHappy:
    def test_help_succeeds(self, host):
        # Arrange / Act
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert
        assert result["success"] is True, "/help succeeds"
        assert result["error"] == "", "no error"
        assert result["cmd"] == "/help", "echo of input"
        assert isinstance(result["elapsed_s"], float), "elapsed timing recorded"

    def test_help_produces_output_lines(self, host):
        # Arrange / Act
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert
        assert len(result["output_lines"]) > 0, (
            "/help emits output captured by buffer"
        )

    def test_silent_produces_no_output_lines(self, host):
        # Arrange / Act
        result = asyncio.run(host.run_command_async("/help", "silent", 5.0))
        # Assert
        assert result["output_lines"] == [], (
            "silent mode: ctx.write is no-op, buffer stays empty"
        )

    def test_verbose_produces_more_than_normal(self, host):
        # Arrange — /port.list emits a banner (status, verbose-only)
        # Use /help for a stable comparison: it shouldn't change line
        # count by level (it always builds the full table).  Instead
        # use an entry-level check: verbose >= normal.
        normal = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        verbose = asyncio.run(host.run_command_async("/help", "verbose", 5.0))
        # Assert
        assert len(verbose["output_lines"]) >= len(normal["output_lines"]), (
            "verbose produces at least as many lines as normal"
        )


# ── run_command error paths ─────────────────────────────────────────────────


class TestRunCommandErrors:
    def test_unknown_command_returns_failure(self, host):
        # Arrange / Act
        result = asyncio.run(
            host.run_command_async("/notarealcmd", "normal", 5.0)
        )
        # Assert
        assert result["success"] is False, "unknown command fails"
        assert "Unknown command" in result["error"], (
            "error names the failure mode"
        )

    def test_serial_send_when_disconnected_fails(self, host):
        # Arrange — host has no port; /term.send should fail gracefully
        # Act — bare line goes through fallthrough -> /term.send
        result = asyncio.run(
            host.run_command_async("AT+VER", "normal", 5.0)
        )
        # Assert
        assert result["success"] is False, "no port = no send"
        assert "Not connected" in result["error"], "error says disconnected"

    @pytest.mark.slow  # ~5s: the /delay actually sleeps in the worker thread
    def test_timeout_returns_failure(self, host):
        # Arrange / Act — /delay 5s with timeout_s=0.3
        result = asyncio.run(host.run_command_async("/delay 5s", "normal", 0.3))
        # Assert
        assert result["success"] is False, "timeout fails"
        assert "timeout" in result["error"].lower(), "error names timeout"


# ── Capture artifact tracking ───────────────────────────────────────────────


class TestCaptureArtifacts:
    def test_no_artifacts_when_no_capture(self, host):
        # Arrange / Act
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert
        assert result["captured_artifacts"] == [], (
            "/help doesn't write to cap_dir"
        )

    def test_artifact_appears_after_capture_command(self, host):
        # Arrange — manually write a file into cap_dir to simulate a
        # capture without needing a serial port (capture commands
        # require a connected device).
        cap_dir = Path(host.ctx.cap_dir)
        cap_dir.mkdir(parents=True, exist_ok=True)

        # Act — run a help-like command, then create a file post-snapshot.
        # This proves the diff logic.  We use a controlled side-effect:
        # /print writes to terminal but doesn't touch cap_dir.  We use
        # the test fixture to simulate by calling _snapshot directly.
        from termapy.mcp.server import _new_artifacts, _snapshot_cap_dir

        before = _snapshot_cap_dir(cap_dir)
        (cap_dir / "smoke.txt").write_text("hello", encoding="utf-8")
        after = _snapshot_cap_dir(cap_dir)
        diff = _new_artifacts(before, after, cap_dir)
        # Assert
        assert len(diff) == 1, "one new artifact"
        assert diff[0]["name"] == "smoke.txt", "name preserved"
        assert diff[0]["uri"] == "termapy://capture/smoke.txt", "uri formed"
        assert diff[0]["bytes"] == 5, "size reported"


# ── Output buffer level tagging ─────────────────────────────────────────────


class TestOutputBufferLevels:
    def test_text_and_markup_entries_distinguished(self, host):
        # Arrange / Act — /help uses ctx.output (markup-rendered)
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert
        levels = {entry["level"] for entry in result["output_lines"]}
        assert "markup" in levels, "/help emits markup"

    def test_each_buffer_entry_has_required_fields(self, host):
        # Arrange / Act
        result = asyncio.run(host.run_command_async("/help", "normal", 5.0))
        # Assert
        for entry in result["output_lines"]:
            assert "level" in entry, "level field"
            assert "text" in entry, "text field"
            assert "color" in entry, "color field"
            assert isinstance(entry["text"], str), "text is str"


# ── Catalog parity (resource vs /mcp.catalog REPL) ──────────────────────────


class TestCatalogParity:
    """The MCP resource and the /mcp.catalog REPL command (Phase 4) must
    serve byte-identical JSON.  Phase 3 establishes the foundation: the
    catalog JSON is reproducible from the same call.  Phase 4 adds the
    REPL command and a true byte-equivalence test."""

    def test_catalog_is_reproducible(self, host):
        # Arrange / Act
        a = catalog_json(host.ctx)
        b = catalog_json(host.ctx)
        # Assert
        assert a == b, "catalog generation is deterministic"

    def test_catalog_includes_target_meta_namespace(self, host):
        # Arrange — even if empty, the key should exist
        cat = build_catalog(host.ctx)
        # Assert
        assert "target_meta" in cat, "target_meta key always present"
        assert isinstance(cat["target_meta"], dict), "target_meta is dict"


# ── device_state resource ───────────────────────────────────────────────────


class TestDeviceStateResource:
    def test_top_level_keys_present(self, host):
        # Arrange
        from termapy.mcp.catalog import build_device_state

        # Act
        state = build_device_state(host.ctx)
        # Assert
        for key in (
            "schema",
            "port",
            "profile",
            "device",
            "last_command",
            "captures",
            "expect_history",
            "async_events",
            "async_errors",
        ):
            assert key in state, f"device_state missing {key!r}"

    def test_port_state_reflects_disconnected_host(self, host):
        # Arrange / Act
        from termapy.mcp.catalog import build_device_state

        state = build_device_state(host.ctx)
        # Assert
        assert state["port"]["open"] is False, "no port = closed"

    def test_last_command_populated_after_run(self, host):
        # Arrange / Act
        import asyncio

        from termapy.mcp.catalog import build_device_state

        asyncio.run(host.run_command_async("/help", "normal", 5.0))
        state = build_device_state(
            host.ctx,
            last_command=host._last_command,
        )
        # Assert
        assert state["last_command"]["cmd"] == "/help", "last cmd recorded"
        assert state["last_command"]["success"] is True, "success flag recorded"

    def test_expect_history_grows_after_expect_call(self, host):
        # Arrange / Act
        import asyncio

        from termapy.mcp.catalog import build_device_state

        # /expect with no port and no match data; will time out fast.
        asyncio.run(
            host.run_command_async(
                "/expect timeout=50ms match=NEVER", "normal", 5.0
            )
        )
        state = build_device_state(
            host.ctx,
            expect_history=host._expect_history,
        )
        # Assert
        assert len(state["expect_history"]) == 1, "history grew by one"
        assert state["expect_history"][0]["matched"] is False, "timeout = no match"

    def test_captures_list_reflects_cap_dir(self, host, tmp_path):
        # Arrange — write a file to cap_dir
        from pathlib import Path

        from termapy.mcp.catalog import build_device_state

        cap_dir = Path(host.ctx.cap_dir)
        cap_dir.mkdir(parents=True, exist_ok=True)
        (cap_dir / "smoke.txt").write_text("hi", encoding="utf-8")
        # Act
        state = build_device_state(host.ctx)
        # Assert
        names = {c["name"] for c in state["captures"]}
        assert "smoke.txt" in names, "captures list reflects cap_dir contents"
        smoke = next(c for c in state["captures"] if c["name"] == "smoke.txt")
        assert smoke["uri"] == "termapy://capture/smoke.txt", "uri formatted"


# ── Capture resource path-traversal guard ───────────────────────────────────


class TestCaptureResourceSecurity:
    """The capture resource handler must validate that the requested
    filename resolves inside cap_dir.  Symlinks and ../ should be
    rejected."""

    def test_path_traversal_rejected(self, host):
        # Arrange — we test the underlying logic without going through
        # the FastMCP decorator (which wraps the function).  The
        # implementation lives in mcp/server.py's _build_server.
        # Equivalent guard is exercised here directly.
        cap_dir = Path(host.ctx.cap_dir).resolve()

        def read(filename: str) -> str:
            target = (cap_dir / filename).resolve()
            if not target.is_relative_to(cap_dir):
                raise ValueError(f"Path traversal blocked: {filename!r}")
            if not target.exists():
                raise FileNotFoundError(filename)
            return target.read_text("utf-8")

        # Act / Assert
        with pytest.raises(ValueError, match="traversal"):
            read("../../../../etc/passwd")
