"""Tests for the /mcp.* REPL plugin (Phase 4).

Subcommands:
- /mcp.catalog -- prints JSON; byte-equivalent to termapy://commands.json
- /mcp.info -- shows MCP-mode status (catalog/profile/port/captures)

These tests run against a CLITerminal-equivalent context (regular
PluginContext, not MCPHost) so they verify the REPL surface.  The
MCP-resource <-> /mcp.catalog parity test pulls catalog_json directly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip the entire module when the optional mcp extra isn't installed:
# the MCPHost fixture used here pulls in mcp.server.fastmcp at import.
pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.catalog import build_catalog, catalog_json  # noqa: E402
from termapy.mcp.server import MCPHost  # noqa: E402


@pytest.fixture
def env(tmp_path):
    """Build an MCPHost (wires engine + ctx + plugins).  Verbose output."""
    cfg = dict(DEFAULT_CFG)
    cfg["port"] = ""
    config_path = tmp_path / "cfg" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    host = MCPHost(cfg, str(config_path), verbose=False)
    # Verbose so ctx.output() in handler isn't gated by quiet default.
    host.ctx.ns("flags")["output_level"] = "verbose"
    output: list = []
    # Capture both write and write_markup so info-style handlers that
    # use format_kv_lines (which routes through write_markup) are
    # observable to assertions.
    orig_write = host.ctx.write
    orig_write_markup = host.ctx.write_markup

    def captured_write(text, color=""):
        output.append((text, color))
        orig_write(text, color)

    def captured_write_markup(text):
        output.append((text, "markup"))
        orig_write_markup(text)

    host.ctx.write = captured_write
    host.ctx.write_markup = captured_write_markup
    return host.repl, host.ctx, output


# ── /mcp.catalog ────────────────────────────────────────────────────────────


class TestMcpCatalog:
    def test_dispatch_succeeds(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        # Assert
        assert result.success is True, "/mcp.catalog returns success"

    def test_value_is_parseable_json(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        parsed = json.loads(result.value)
        # Assert
        assert parsed["schema"] == 1, "schema=1"
        assert isinstance(parsed["commands"], list), "commands is list"

    def test_byte_identical_to_catalog_json(self, env):
        """The plan's invariant: /mcp.catalog and the resource serve the
        SAME JSON.  The handler emits via ctx.output and ALSO returns
        via CmdResult.value -- the latter must equal catalog_json(ctx)
        byte-for-byte so the resource handler can reuse the same builder.
        """
        # Arrange / Act
        eng, ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        # Assert
        assert result.value == catalog_json(ctx), (
            "/mcp.catalog value byte-identical to catalog_json(ctx)"
        )

    def test_includes_known_command(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        parsed = json.loads(result.value)
        names = {c["name"] for c in parsed["commands"]}
        # Assert -- catalog names are prefixed (LLM symbol-table style)
        assert "/help" in names, "/help in catalog"
        assert "/term.send" in names, "/term.send in catalog"
        assert "/expect" in names, "/expect in catalog"


# ── /mcp.info ──────────────────────────────────────────────────────────────


class TestMcpInfo:
    def test_dispatch_succeeds(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.info")
        # Assert
        assert result.success is True, "/mcp.info succeeds"

    def test_output_includes_command_count(self, env):
        # Arrange / Act
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        # Combine all written text; assert presence of expected fields.
        full = " ".join(t for t, _ in output)
        # Assert
        assert "commands" in full.lower(), "lists commands count"
        assert "port" in full.lower(), "lists port"
        assert "captures" in full.lower(), "lists captures"

    def test_value_is_command_count(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.info")
        # Assert
        assert int(result.value) > 0, "value is the command count integer"
