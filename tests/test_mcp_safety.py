"""``Command.safety``: termapy commands carry the profile's safety tiers.

A profile entry marked ``destructive`` already needs ``confirm=true`` over
MCP.  These pin the same rule for termapy's own commands: the tier is
declared on ``Command``, validated at load, exposed in the catalog, and
enforced by ``MCPHost.run_command_async`` BEFORE dispatch -- with the
identical structured refusal, so an agent handles one shape.
"""

from __future__ import annotations

import asyncio
import json
import textwrap

import pytest

from termapy.defaults import DEFAULT_CFG
from termapy.plugins import CmdResult, Command, PluginInfo
from termapy.plugins.loader import load_plugins_from_dir

pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.mcp.catalog import build_catalog  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


def _zap(ctx, args) -> CmdResult:
    return CmdResult.ok(value="zapped")


@pytest.fixture
def host(tmp_path):
    """An MCPHost with no port plus one destructive termapy command, /zap."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    host = MCPHost(cfg, str(config_path), verbose=False)
    host.repl.register_plugin(
        PluginInfo(name="zap", args="", help="Zap the thing.", handler=_zap, safety="destructive"),
    )
    return host


def _run(host: MCPHost, line: str, *, confirm: bool = False) -> dict:
    return asyncio.run(host.run_command_async(line, "normal", 5.0, confirm=confirm))


class TestCommandDeclaration:

    def test_default_tier_is_safe(self):
        assert Command("Help text", name="x").safety == "safe", "commands are safe unless declared"
        assert PluginInfo(name="x", args="", help="h", handler=_zap).safety == "safe"

    def test_unknown_tier_fails_at_construction(self):
        with pytest.raises(ValueError, match="/x: unknown safety tier 'wild'"):
            Command("Help text", name="x", safety="wild")

    def test_tier_survives_plugin_loading(self, tmp_path):
        # Arrange -- a plugin file declaring a destructive subcommand
        (tmp_path / "boom.py").write_text(textwrap.dedent('''
            from termapy.plugins import CmdResult, Command

            def _go(ctx, args):
                return CmdResult.ok()

            # ── COMMAND (must be at end of file) ──────────────────────────
            COMMAND = Command(
                "Boom.", name="boom", handler=_go, safety="readonly",
                sub_commands={"now": Command("Now.", handler=_go, safety="destructive")},
            )
        '''), encoding="utf-8")

        # Act
        loaded = {info.name: info for info in load_plugins_from_dir(tmp_path, "test").plugins}

        # Assert
        assert loaded["boom"].safety == "readonly", "root tier carried into PluginInfo"
        assert loaded["boom.now"].safety == "destructive", "subcommand tier carried too"


class TestMcpGate:

    def test_destructive_refused_without_confirm(self, host):
        # Act
        result = _run(host, "/zap")

        # Assert -- the same shape _dispatch_via_profile returns for a device command
        assert result["success"] is False, "refused"
        assert "Confirmation required: '/zap' is destructive" in result["error"], "names the command"
        assert result["value"]["needs_confirmation"] is True, "machine-readable marker"
        assert result["value"]["safety"] == "destructive", "the tier is reported"

    def test_destructive_runs_with_confirm(self, host):
        result = _run(host, "/zap", confirm=True)
        assert result["success"] is True, result["error"]
        assert result["value"] == "zapped", "the handler ran"

    def test_level_suffix_does_not_dodge_the_gate(self, host):
        result = _run(host, "/zap.silent")
        assert result["value"]["needs_confirmation"] is True, "resolved like dispatch resolves it"

    def test_mem_write_is_destructive(self, host):
        # Act -- the real command that motivated the tier; no port needed,
        # the gate runs before dispatch
        result = _run(host, "/mem.write gTemp 1B00")

        # Assert
        assert result["value"]["needs_confirmation"] is True, "/mem.write pokes memory: confirm first"
        assert result["value"]["command"] == "/mem.write", "the resolved subcommand, prefixed"

    def test_mem_dump_is_not_gated(self, host):
        result = _run(host, "/mem.dump gTemp")
        assert not isinstance(result["value"], dict) or "needs_confirmation" not in result["value"], (
            "a readonly command never asks for confirmation (it fails on 'not connected' instead)"
        )

    def test_safe_command_ignores_confirm(self, host):
        result = _run(host, "/help")
        assert result["success"] is True, "a safe command needs no confirmation"

    def test_bare_device_line_is_not_gated_here(self, host):
        # Arrange / Act -- no port, no profile: the literal-write path fails on
        # its own terms, never with a confirmation marker
        result = _run(host, "AT")

        # Assert
        assert not isinstance(result["value"], dict) or "needs_confirmation" not in result["value"], (
            "the Command.safety gate only looks at slash commands"
        )


class TestCatalog:

    def test_tier_exposed_like_device_commands(self, host):
        # Act
        entries = {entry["name"]: entry for entry in build_catalog(host.ctx)["commands"]}

        # Assert
        assert entries["/zap"]["safety"] == "destructive", "the agent can see it before calling"
        assert "safety" not in entries["/help"], "safe commands keep their entry shape"
