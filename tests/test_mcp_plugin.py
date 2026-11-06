"""Tests for the /mcp.* REPL plugin.

Subcommands:
- /mcp.catalog -- prints JSON; byte-equivalent to termapy://commands.json
- /mcp.info -- shows MCP-mode status (catalog/profile/port/captures)

These tests run against a CLITerminal-equivalent context (regular
PluginContext, not MCPHost) so they verify the REPL surface.  The
MCP-resource <-> /mcp.catalog parity test pulls catalog_json directly.
"""

from __future__ import annotations

import json
import os

import pytest

# Skip the entire module when the optional mcp extra isn't installed:
# the MCPHost fixture used here pulls in mcp.server at import.
pytest.importorskip("mcp", reason="mcp SDK not installed; install with [mcp] extra")

from termapy.defaults import DEFAULT_CFG  # noqa: E402
from termapy.markup import markup_to_plain  # noqa: E402
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
    # These tests read the prose a TUI/CLI user sees.  An MCP host wants
    # data, so dispatch would send /mcp.info's record and print no rows;
    # that path is pinned in test_mcp_server.py.
    host.ctx.wants_data = False
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
        """/mcp.catalog runs cleanly from a plain REPL context with no profile."""
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        # Assert
        assert result.success is True, "/mcp.catalog returns success"

    def test_value_is_parseable_json(self, env):
        """The returned value is schema-1 JSON with a commands list, not prose."""
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
        """Catalog entries carry the / prefix and include core commands like /help."""
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.catalog")
        parsed = json.loads(result.value)
        names = {command["name"] for command in parsed["commands"]}
        # Assert -- catalog names are prefixed (LLM symbol-table style)
        assert "/help" in names, "/help in catalog"
        assert "/term.send" in names, "/term.send in catalog"
        assert "/expect" in names, "/expect in catalog"

    def test_params_say_how_each_argument_binds(self, env):
        """A params entry marks positionals and the rest argument, so an agent does not write file=x for a positional."""
        # Arrange / Act
        eng, _ctx, _output = env
        parsed = json.loads(eng.dispatch("mcp.catalog").value)
        entry = next(command for command in parsed["commands"] if command["name"] == "/cfg.auto")
        params = {param["name"]: param for param in entry["params"]}

        # Assert
        assert (params["key"]["positional"], params["key"]["rest"]) == (True, False), (
            "given by place"
        )
        assert (params["value"]["positional"], params["value"]["rest"]) == (True, True), (
            "runs to end of line"
        )


# ── /mcp.info ──────────────────────────────────────────────────────────────


class TestMcpInfo:
    def test_dispatch_succeeds(self, env):
        """/mcp.info runs cleanly with no profile loaded."""
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.info")
        # Assert
        assert result.success is True, "/mcp.info succeeds"

    def test_output_includes_command_count(self, env):
        """The status page reports the commands, port and captures rows."""
        # Arrange / Act
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        # Combine all written text; assert presence of expected fields.
        full = " ".join(t for t, _ in output)
        # Assert
        assert "commands" in full.lower(), "lists commands count"
        assert "port" in full.lower(), "lists port"
        assert "captures" in full.lower(), "lists captures"

    def test_info_shows_this_process_pid(self, env):
        """The status page names this process's PID, the one fact that tells identical termapy --mcp servers apart in tasklist / ps."""
        # Arrange
        eng, _ctx, output = env

        # Act
        eng.dispatch("mcp.info")
        lines = [markup_to_plain(text) for text, _ in output]
        rows = dict(line.split(":", 1) for line in lines if ":" in line)
        actual = {label.strip(): value.strip() for label, value in rows.items()}.get("pid")

        # Assert
        expected = str(os.getpid())
        assert actual == expected, "the pid row is this process, so it matches the OS listing"

    def test_value_is_command_count(self, env):
        """The scriptable value is the positive command count."""
        # Arrange / Act
        eng, _ctx, _output = env
        result = eng.dispatch("mcp.info")
        # Assert
        assert int(result.value) > 0, "value is the command count integer"

    def test_destructive_count_zero_when_no_profile(self, env):
        """With no profile the destructive row still appears and reads 0."""
        # Arrange / Act -- no /profile.load, no /include
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- the destructive row is present and reads 0
        assert "destructive" in full, "destructive row present"
        assert "destructive" in full and ": 0" in full, "no profile -> zero destructive commands"

    def test_no_profile_omits_enabled_row(self, env):
        """No profile means no profile_enabled row, rather than a meaningless 0 of 0."""
        # Arrange / Act -- no /profile.load, no /include
        eng, _ctx, output = env
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- "profile_enabled" row is suppressed when no profile loaded
        # so we don't render a meaningless "0 of 0" line
        assert "profile_enabled" not in full, "no profile -> enabled row suppressed"

    def test_enabled_split_when_profile_has_drafts(self, env):
        """Disabled profile entries show as N of M with a drafts-pending-review count."""
        # Arrange -- seed profile with one enabled and two disabled entries
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update(
            {
                "commands": {
                    "AT": {"help": "x", "enabled": True},
                    "RESET": {"help": "y", "enabled": False},
                    "ERASE": {"help": "z", "enabled": False},
                },
            }
        )
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert -- explicit count + drafts-pending phrasing
        assert "1 of 3" in full, "shows enabled-of-total ratio"
        assert "2 drafts pending review" in full, "calls out unreviewed entries explicitly"

    def test_enabled_split_all_enabled(self, env):
        """A profile with every entry enabled reads N of N, all enabled."""
        # Arrange -- profile with everything enabled (curated steady state)
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update(
            {
                "commands": {
                    "AT": {"help": "x"},  # enabled defaults true
                    "AT+TEMP": {"help": "y", "enabled": True},
                },
            }
        )
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        full = " ".join(t for t, _ in output)
        # Assert
        assert "2 of 2" in full, "shows full count"
        assert "all enabled" in full, "happy-path phrasing"

    def test_destructive_count_lists_names_when_present(self, env):
        """Destructive profile entries are counted and named, e.g. 1 (AT+RESET)."""
        # Arrange -- seed an active profile with one destructive entry
        eng, ctx, output = env
        ns = ctx.ns("active_profile")
        ns.update(
            {
                "commands": {
                    "AT+RESET": {"help": "x", "safety": "destructive"},
                    "AT": {"help": "y", "safety": "readonly"},
                },
            }
        )
        # Act
        output.clear()
        eng.dispatch("mcp.info")
        # Assert
        full = " ".join(t for t, _ in output)
        assert "AT+RESET" in full, "destructive name surfaces in /mcp.info"
        assert "1 (AT+RESET)" in full, "count + name list rendered exactly"


# ── The MCP server's session log, from another frontend ────────────────────


class TestMcpLogFromAnotherFrontend:
    """The MCP server's <cfg>.mcp.log is one of the config's session logs: the CLI lists and prints it by name."""

    @staticmethod
    def _cli_beside_an_mcp_log(tmp_path):
        """A CLI host on a config whose MCP server has written its session log."""
        from termapy.cli import CLITerminal
        from termapy.defaults import default_cfg

        cfg = default_cfg()
        cfg["serial"]["port"] = ""
        path = tmp_path / "dev" / "dev.cfg"
        path.parent.mkdir()
        path.write_text(json.dumps(cfg), encoding="utf-8")
        (path.parent / "dev.mcp.log").write_text(
            "[2026-11-05 10:00:00.000] $ /ver  (normal)\n[2026-11-05 10:00:00.010] # termapy 1.0\n",
            encoding="utf-8",
        )
        cli = CLITerminal(cfg, str(path), no_color=True, term_width=120)
        cli.repl.global_root = tmp_path
        return cli

    def test_log_list_names_the_mcp_servers_log(self, tmp_path):
        """/log.list in the CLI, which keeps no log of its own, lists the MCP server's."""
        # Arrange
        cli = self._cli_beside_an_mcp_log(tmp_path)
        cli.repl.ctx.wants_data = True

        # Act
        result = cli.repl.dispatch("log.list", report=False)

        # Assert
        assert [record["name"] for record in result.data] == ["dev.mcp.log"]

    def test_log_dump_prints_it_by_name(self, tmp_path, capsys):
        """/log.dump dev.mcp 1 prints the MCP log's last line, the name taking its extension or not."""
        # Arrange
        cli = self._cli_beside_an_mcp_log(tmp_path)
        capsys.readouterr()

        # Act
        result = cli.repl.dispatch("log.dump dev.mcp 1", report=False)

        # Assert
        assert result.success is True
        out = capsys.readouterr().out
        assert "termapy 1.0" in out and "/ver" not in out, "the last line only"

    def test_log_dump_bare_says_the_cli_keeps_none(self, tmp_path):
        """Bare /log.dump in the CLI says it keeps no log and where to look, rather than the gate's words."""
        # Arrange
        cli = self._cli_beside_an_mcp_log(tmp_path)

        # Act
        result = cli.repl.dispatch("log.dump", report=False)

        # Assert
        assert result.error == "This frontend keeps no session log; /log.list names the config's"
