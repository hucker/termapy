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
# the MCPHost fixture used here pulls in mcp.server at import.
pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.mcp.catalog import catalog_json  # noqa: E402
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
    orig_write = host.ctx.io._write
    orig_write_markup = host.ctx.io._write_markup

    def captured_write(text, color=""):
        output.append((text, color))
        orig_write(text, color)

    def captured_write_markup(text):
        output.append((text, "markup"))
        orig_write_markup(text)

    host.ctx.io._write = captured_write
    host.ctx.io._write_markup = captured_write_markup
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

    def test_destructive_count_zero_when_no_profile(self, env):
        # Arrange / Act -- no /profile.load, no /include
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- the destructive row is present and reads 0
        assert "destructive" in full, "destructive row present"
        assert "destructive" in full and ": 0" in full, (
            "no profile -> zero destructive commands"
        )

    def test_no_profile_omits_enabled_row(self, env):
        # Arrange / Act -- no /profile.load, no /include
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- "profile_enabled" row is suppressed when no profile loaded
        # so we don't render a meaningless "0 of 0" line
        assert "profile_enabled" not in full, (
            "no profile -> enabled row suppressed"
        )

    def test_enabled_split_when_profile_has_drafts(self, env):
        # Arrange -- seed profile with one enabled and two disabled entries
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update({
            "commands": {
                "AT": {"help": "x", "enabled": True},
                "RESET": {"help": "y", "enabled": False},
                "ERASE": {"help": "z", "enabled": False},
            },
        })
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- explicit count + drafts-pending phrasing
        assert "1 of 3" in full, "shows enabled-of-total ratio"
        assert "2 drafts pending review" in full, (
            "calls out unreviewed entries explicitly"
        )

    def test_enabled_split_all_enabled(self, env):
        # Arrange -- profile with everything enabled (curated steady state)
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update({
            "commands": {
                "AT": {"help": "x"},  # enabled defaults true
                "AT+TEMP": {"help": "y", "enabled": True},
            },
        })
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert
        assert "2 of 2" in full, "shows full count"
        assert "all enabled" in full, "happy-path phrasing"

    def test_destructive_count_lists_names_when_present(self, env):
        # Arrange -- seed an active profile with one destructive entry
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update({
            "commands": {
                "AT+RESET": {"help": "x", "safety": "destructive"},
                "AT": {"help": "y", "safety": "readonly"},
            },
        })
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        # Assert
        full = " ".join(t for t, _ in output)
        assert "AT+RESET" in full, "destructive name surfaces in /mcp.info"
        assert "1 (AT+RESET)" in full, (
            "count + name list rendered exactly"
        )


# ── /mcp.log + subcommands ─────────────────────────────────────────────────


class TestMcpLog:
    """Subcommands that surface the MCP server's session log to the user.

    The log itself is written by ``termapy --mcp`` to
    ``<cfg_dir>/mcp/session.log``.  These commands open / dump / locate
    the file from any frontend (TUI/CLI/MCP) so a user debugging an
    LLM session can review what happened without leaving termapy.
    """

    def _seed_log(self, ctx, content: str) -> Path:
        """Write a fake MCP session log to <cfg_dir>/mcp/session.log."""
        cfg_dir = Path(ctx.config_path).parent
        mcp_dir = cfg_dir / "mcp"
        mcp_dir.mkdir(exist_ok=True)
        log = mcp_dir / "session.log"
        log.write_text(content, encoding="utf-8")
        return log

    def test_log_path_reports_location_when_absent(self, env):
        # Arrange / Act -- no log file written yet
        eng, _ctx, output = env
        result = eng.dispatch("mcp.log.path")
        # Assert
        assert result.success, "/mcp.log.path always succeeds (informational)"
        full = " ".join(t for t, _ in output)
        assert "session.log" in full, "path includes session.log"
        assert "(not yet created)" in full, (
            "absent file marked explicitly so user knows MCP hasn't run"
        )

    def test_log_path_reports_location_when_present(self, env):
        # Arrange
        eng, ctx, output = env
        self._seed_log(ctx, "fake log content\n")
        # Act
        output.clear()
        result = eng.dispatch("mcp.log.path")
        # Assert
        assert result.success, "succeeds when file exists"
        full = " ".join(t for t, _ in output)
        assert "session.log" in full, "path printed"
        assert "(not yet created)" not in full, (
            "no absent-marker when file is there"
        )

    def test_log_dump_when_absent_fails_cleanly(self, env):
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.log.dump")
        # Assert
        assert not result.success, "fails when log file missing"
        assert "not found" in result.error, "names the failure mode"

    def test_log_dump_prints_full_log(self, env):
        # Arrange
        eng, ctx, output = env
        self._seed_log(ctx, "line 1\nline 2\nline 3\n")
        # Act
        output.clear()
        result = eng.dispatch("mcp.log.dump")
        # Assert
        assert result.success, "dump succeeds"
        assert int(result.value) == 3, "value is line count"
        text = "\n".join(t for t, _ in output)
        assert "line 1" in text and "line 3" in text, "all lines printed"

    def test_log_dump_n_prints_last_n(self, env):
        # Arrange
        eng, ctx, output = env
        self._seed_log(ctx, "a\nb\nc\nd\ne\n")
        # Act -- last 2 lines
        output.clear()
        result = eng.dispatch("mcp.log.dump 2")
        # Assert
        assert result.success, "tail-N succeeds"
        text = "\n".join(t for t, _ in output)
        assert "d" in text and "e" in text, "last 2 lines present"
        assert "a" not in text.split("\n")[0], "earlier lines suppressed"

    def test_log_dump_negative_n_prints_first_n(self, env):
        # Arrange
        eng, ctx, output = env
        self._seed_log(ctx, "a\nb\nc\nd\ne\n")
        # Act -- first 2 lines (head)
        output.clear()
        result = eng.dispatch("mcp.log.dump -2")
        # Assert
        assert result.success, "head-N (negative) succeeds"
        printed = [t for t, _ in output]
        actual = printed[:2]
        expected = ["a", "b"]
        assert actual == expected, "first 2 lines present, in order"
        assert "e" not in printed, "later lines suppressed"

    def test_log_dump_invalid_n_fails(self, env):
        # Arrange
        eng, ctx, _output = env
        self._seed_log(ctx, "x\n")
        # Act
        result = eng.dispatch("mcp.log.dump notanumber")
        # Assert
        assert not result.success, "non-int N rejected"
        assert "Usage" in result.error, "usage shown"

    def test_log_dump_zero_n_fails(self, env):
        # Arrange
        eng, ctx, _output = env
        self._seed_log(ctx, "x\ny\n")
        # Act
        result = eng.dispatch("mcp.log.dump 0")
        # Assert
        assert not result.success, "0 is rejected (ambiguous, -0 == 0)"
        assert "0" in result.error, "error names the rejected value"
