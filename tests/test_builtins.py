"""Tests for built-in REPL commands dispatched through ReplEngine."""

import json

import pytest

import re as _re

from termapy.plugins import CapabilitySet, EngineAPI, PluginContext
from termapy.repl import ReplEngine


@pytest.fixture
def repl_env(tmp_path):
    """Create a ReplEngine with a temp config file and capture output."""
    cfg = {
        "port": "COM4",
        "baud_rate": 115200,
        "echo_input": False,
        "line_ending": "\r",
        "os_cmd_enabled": False,
    }
    config_path = tmp_path / "test_cfg.cfg"
    config_path.write_text(json.dumps(cfg, indent=4))

    output = []

    def write(text, color=None):
        output.append((text, color))

    engine = ReplEngine(cfg, str(config_path), write)
    engine_api = EngineAPI(
        prefix="/",
        plugins=engine._plugins,
        in_script=lambda: engine.in_script,
        script_stop=lambda: engine._script_stop.set(),
        apply_cfg=engine._apply_cfg,
        coerce_type=ReplEngine._coerce_type,
        dispatch=engine.dispatch,
    )
    def write_markup(text):
        output.append((text, "markup"))

    ctx = PluginContext(
        write=write,
        write_markup=write_markup,
        cfg=cfg,
        config_path=str(config_path),
        engine=engine_api,
        # Test fixture publishes every capability so command-by-command
        # tests can exercise any handler.  Specific capability-gate tests
        # use their own restricted ctx (see test_engine.TestDispatchCapabilities).
        capabilities=CapabilitySet(
            block_until=True,
            confirm_dialog=True,
            ui_notify=True,
            status_bar=True,
            screen_capture=True,
            serial_connected=True,
            interactive=True,
            gui_apps=True,
        ),
        # Wire ctx.dispatch so handlers that forward to other commands
        # (legacy aliases like /echo -> /term.echo) actually reach the
        # target handler instead of hitting the default no-op lambda.
        dispatch=engine.dispatch,
    )
    engine.set_context(ctx)
    # Seed the engine-reserved `flags` namespace with the defaults that
    # _build_context would set in production.  Tests that construct
    # PluginContext directly bypass that path, so do it explicitly here.
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex_mode"] = False
    return engine, cfg, config_path, output


# -- /echo ----------------------------------------------------------------


class TestEcho:
    def test_echo_on(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine.ctx.ns("flags")["echo"] = False

        # Act
        engine.dispatch("echo on")

        # Assert
        assert engine.ctx.ns("flags")["echo"] is True, "echo enabled"
        assert any("on" in t for t, _ in output), "confirmation shown"

    def test_echo_off(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("echo off")

        # Assert
        assert engine.ctx.ns("flags")["echo"] is False, "echo disabled"
        assert any("off" in t for t, _ in output), "confirmation shown"

    def test_echo_toggle(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        assert engine.ctx.ns("flags")["echo"] is True, "starts enabled"

        # Act / Assert - toggle off
        engine.dispatch("echo")
        assert engine.ctx.ns("flags")["echo"] is False, "toggled off"

        # Act / Assert - toggle on
        engine.dispatch("echo")
        assert engine.ctx.ns("flags")["echo"] is True, "toggled back on"


# -- /print ---------------------------------------------------------------


class TestPrint:
    def test_print_text(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print Hello, world!")
        assert ("Hello, world!", None) in output, "text printed verbatim"

    def test_print_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print")
        assert ("", None) in output, "empty string printed"


# -- /seq -----------------------------------------------------------------


class TestSeq:
    def test_seq_show_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("seq")
        assert any("No counters" in t for t, _ in output), "empty state message"

    def test_seq_show_with_counters(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        seq = engine.ctx.ns("seq")
        seq[1] = 3
        seq[2] = 7

        # Act
        engine.dispatch("seq")

        # Assert
        assert any("seq1=3" in t for t, _ in output), "counter 1 shown"
        assert any("seq2=7" in t for t, _ in output), "counter 2 shown"

    def test_seq_reset(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        seq = engine.ctx.ns("seq")
        seq[1] = 5

        # Act
        engine.dispatch("seq.reset")

        # Assert
        remaining_counters = {k: v for k, v in seq.items() if isinstance(k, int)}
        assert remaining_counters == {}, "counters cleared"
        assert any("reset" in t.lower() for t, _ in output), "confirmation shown"


# -- /stop ----------------------------------------------------------------


class TestStop:
    def test_stop_no_script(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("stop")
        assert any("No script" in t for t, _ in output), "no-op message"

    def test_stop_with_script(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._script_depth = 1

        # Act
        engine.dispatch("stop")

        # Assert
        assert engine._script_stop.is_set(), "stop event set"
        assert any("Stopping" in t for t, _ in output), "confirmation shown"


# -- /help ----------------------------------------------------------------



def _plain(text: str) -> str:
    """Strip underline markup so substring checks are markup-insensitive."""
    return _re.sub(r"\[/?u\]", "", text)


class TestHelp:
    """Tests for the man-inspired /help command: landscape, exact detail,
    candidate list on miss, and hard-fail with /search hint when nothing
    matches name+help.
    """

    def test_help_lists_commands(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [t for t, _ in output]
        assert any("help" in t for t in texts), "help command listed"
        assert any("cfg" in t for t in texts), "cfg command listed"

    def test_help_landscape_has_footer_hint(self, repl_env):
        """Landscape view teaches the other two modes via a dim footer."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [t for t, _ in output]
        assert any("/help <term>" in t for t in texts), "footer mentions /help <term>"
        assert any("/search" in t for t in texts), "footer mentions /search"

    def test_help_specific_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help echo")

        # Assert
        texts = [t for t, _ in output]
        assert any("echo" in t.lower() for t in texts), "echo help shown"
        assert any("NAME" in t for t in texts), "man-page NAME section present"

    def test_help_man_page_sections(self, repl_env):
        """Leaf with args + long_help renders NAME, SYNOPSIS, DESCRIPTION."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="full_cmd", args="<arg>", help="Short help.",
            long_help="Line one.\nLine two.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help full_cmd")

        # Assert
        texts = [t for t, _ in output]
        assert any("NAME" in t for t in texts), "NAME section"
        assert any("SYNOPSIS" in t for t in texts), "SYNOPSIS section"
        assert any("DESCRIPTION" in t for t in texts), "DESCRIPTION section"
        assert any("Line one." in t for t in texts), "long_help line 1 present"
        assert any("Line two." in t for t in texts), "long_help line 2 present"

    def test_help_mcp_flag_drops_interactive_only_commands(self, repl_env):
        """``/help --mcp`` filters the landscape via ENVIRONMENTS["MCP"].

        Commands that need interactive/gui_apps drop because MCP
        advertises neither.
        """
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("help --mcp")

        # Assert
        assert result.success, "/help --mcp succeeds"
        texts = [_plain(t) for t, _ in output]
        joined = "\n".join(texts)
        # /grep needs interactive; must NOT appear under --mcp.
        assert "/grep " not in joined and "/grep[" not in joined, (
            "/grep needs interactive and should be filtered out"
        )
        # /help itself has no restrictive needs; must still appear.
        assert "/help" in joined, "/help has no needs and should remain"

    def test_help_mcp_flag_emits_filter_header(self, repl_env):
        """The header makes the filter mode unmistakable."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help --mcp")

        # Assert
        texts = [_plain(t) for t, _ in output]
        assert any("MCP-visible only" in t for t in texts), (
            "filter banner identifies the mode"
        )

    def test_help_without_mcp_flag_keeps_invisible_commands(self, repl_env):
        """Without ``--mcp`` the landscape is unfiltered: /grep still shows."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [_plain(t) for t, _ in output]
        joined = "\n".join(texts)
        assert "/grep" in joined, "plain /help still includes /grep"

    def test_help_man_page_renders_available_matrix(self, repl_env):
        """``/help <cmd>`` shows AVAILABLE row with TUI/CLI/MCP cells."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help help")

        # Assert
        texts = [_plain(t) for t, _ in output]
        assert any("AVAILABLE" in t for t in texts), "AVAILABLE section emitted"
        assert any("TUI:" in t for t in texts), "TUI cell present"
        assert any("CLI:" in t for t in texts), "CLI cell present"
        assert any("MCP:" in t for t in texts), "MCP cell present"

    def test_help_available_matrix_marks_interactive_commands(self, repl_env):
        """A command needing interactive shows MCP: no with reason."""
        # Arrange
        engine, _, _, output = repl_env

        # Act -- /grep needs interactive
        engine.dispatch("help grep")

        # Assert
        texts = [_plain(t) for t, _ in output]
        joined = "\n".join(texts)
        assert "MCP: no" in joined, (
            "/grep needs interactive; MCP cell should be 'no'"
        )
        assert "does not provide: interactive" in joined, (
            "the explanation should name the missing capability and "
            "phrase it from the environment's perspective"
        )

    def test_help_zero_matches_fails_with_search_hint(self, repl_env):
        """/help <term> with no name+help hits hard-fails and points at /search."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("help __does_not_exist__")

        # Assert
        assert result.success is False, "zero-hit miss hard-fails"
        assert "No command matches" in result.error, "error names the miss"
        assert "/search" in result.error, "hint points at /search"

    def test_help_term_shows_candidates_when_no_exact(self, repl_env):
        """/help <term> with no exact match renders a candidate list."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="one.shiny", args="", help="First shiny thing.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="two.shiny", args="", help="Second shiny thing.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help shiny")

        # Assert - both names surface as candidates, result is ok with names
        texts = [_plain(t) for t, _ in output]
        assert result.success, "candidate list succeeds"
        assert any("Candidates matching" in t for t in texts), "banner shown"
        assert any("one.shiny" in t for t in texts), "first candidate listed"
        assert any("two.shiny" in t for t in texts), "second candidate listed"
        assert "one.shiny" in result.value.splitlines(), "name in scripting value"
        assert "two.shiny" in result.value.splitlines(), "name in scripting value"

    def test_help_exact_prefers_over_candidates(self, repl_env):
        """Exact match wins even when the name is a substring of others."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="foo", args="", help="Exact.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="foobar", args="", help="Not exact.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help foo")

        # Assert - man-page detail for /foo, not a candidate list
        texts = [t for t, _ in output]
        assert any("NAME" in t for t in texts), "man-page rendered"
        assert not any("Candidates matching" in t for t in texts), (
            "no candidate list when exact match exists"
        )

    def test_help_term_matches_short_help(self, repl_env):
        """Candidate search hits when the term appears in a command's short help."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="widget_cmd", args="", help="Measures mumble_fritz devices.",
            handler=lambda ctx, args: None,
        ))

        # Act - term appears only in help, not in name
        result = engine.dispatch("help mumble_fritz")

        # Assert
        texts = [_plain(t) for t, _ in output]
        assert result.success, "help-text match succeeds"
        assert any("widget_cmd" in t for t in texts), "found via short help"

    def test_help_parent_shows_subcommand_section(self, repl_env):
        """/help <parent> renders SUBCOMMANDS with name+one-liner only."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="group", args="", help="Group of things.",
            handler=lambda ctx, args: None,
            children=["group.one", "group.two"],
        ))
        engine.register_plugin(PluginInfo(
            name="group.one", args="<really-long-arg-list>",
            help="First child.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="group.two", args="", help="Second child.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help group")

        # Assert - SUBCOMMANDS section present; child args are NOT rendered
        texts = [t for t, _ in output]
        assert any("SUBCOMMANDS" in t for t in texts), "SUBCOMMANDS section present"
        assert any("group.one" in t for t in texts), "first child listed"
        assert any("group.two" in t for t in texts), "second child listed"
        assert not any("really-long-arg-list" in t for t in texts), (
            "child args column is omitted in parent listing"
        )

    def test_help_see_also_contains_siblings_and_parent(self, repl_env):
        """SEE ALSO includes siblings of a leaf plus its parent."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="grp", args="", help="Parent.",
            handler=lambda ctx, args: None,
            children=["grp.a", "grp.b", "grp.c"],
        ))
        for leaf in ("grp.a", "grp.b", "grp.c"):
            engine.register_plugin(PluginInfo(
                name=leaf, args="", help=f"Leaf {leaf}.",
                handler=lambda ctx, args: None,
            ))

        # Act
        engine.dispatch("help grp.a")

        # Assert - SEE ALSO mentions siblings and parent
        texts = [t for t, _ in output]
        see_lines = [t for t in texts if "SEE ALSO" in t or "/grp" in t]
        combined = " ".join(see_lines)
        assert "SEE ALSO" in combined, "SEE ALSO header present"
        assert "/grp.b" in combined, "sibling /grp.b listed"
        assert "/grp.c" in combined, "sibling /grp.c listed"
        assert "/grp" in combined, "parent /grp listed"

    def test_help_see_also_skipped_for_rootless_leaf(self, repl_env):
        """Top-level root commands without a parent don't render SEE ALSO."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="lone_cmd", args="", help="Stands alone.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help lone_cmd")

        # Assert
        texts = [t for t, _ in output]
        assert not any("SEE ALSO" in t for t in texts), (
            "no SEE ALSO section when command has no peers"
        )

    def test_help_no_long_help_omits_description(self, repl_env):
        """/help <cmd> with empty long_help skips the DESCRIPTION section."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="briefcmd", args="", help="Just brief.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help briefcmd")

        # Assert - NAME section is present; DESCRIPTION is not (no long_help)
        texts = [t for t, _ in output]
        assert any("NAME" in t for t in texts), "NAME always shown"
        assert not any("DESCRIPTION" in t for t in texts), (
            "DESCRIPTION skipped when long_help is empty"
        )

    def test_help_callable_long_help_is_invoked(self, repl_env):
        """A callable long_help is called at render time and its result appears."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="dyn_cmd", args="", help="Dynamic test.",
            handler=lambda ctx, args: None,
            long_help=lambda ctx: "dynamic text here",
        ))

        # Act
        engine.dispatch("help dyn_cmd")

        # Assert
        texts = [t for t, _ in output]
        assert any("DESCRIPTION" in t for t in texts), "DESCRIPTION header emitted"
        assert any("dynamic text here" in t for t in texts), \
            "callable result appears in rendered output"

    def test_help_callable_long_help_receives_ctx(self, repl_env):
        """The callable gets the live PluginContext and can read ns/cfg."""
        # Arrange - callable that reads a namespace populated in the fixture
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def dyn(ctx):
            items = ctx.ns("my_test")
            return f"count={len(items)}"

        engine.register_plugin(PluginInfo(
            name="ctx_cmd", args="", help="Reads ctx.ns.",
            handler=lambda ctx, args: None,
            long_help=dyn,
        ))
        # Seed the ns after registration -- the callable reads live state.
        engine.ctx.ns("my_test").update({"a": 1, "b": 2})

        # Act
        engine.dispatch("help ctx_cmd")

        # Assert - the two items are reflected live.
        texts = [t for t, _ in output]
        assert any("count=2" in t for t in texts), \
            "callable receives live ctx and reads ns() correctly"

    def test_help_callable_long_help_exception_is_caught(self, repl_env):
        """A raising callable yields a fallback string; /help does not crash."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def boom(ctx):
            raise RuntimeError("boom")

        engine.register_plugin(PluginInfo(
            name="boom_cmd", args="", help="Broken help.",
            handler=lambda ctx, args: None,
            long_help=boom,
        ))

        # Act - must not propagate
        result = engine.dispatch("help boom_cmd")

        # Assert - fallback present, command reports success
        texts = [t for t, _ in output]
        assert result.success is True, "rendering survives exception"
        assert any("dynamic help failed" in t for t in texts), \
            "fallback prefix appears"
        assert any("boom" in t for t in texts), "exception message included"

    def test_help_callable_empty_result_omits_description(self, repl_env):
        """Callable returning '' omits DESCRIPTION, matching static-empty behavior."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="silent_cmd", args="", help="No dynamic text.",
            handler=lambda ctx, args: None,
            long_help=lambda ctx: "",
        ))

        # Act
        engine.dispatch("help silent_cmd")

        # Assert
        texts = [t for t, _ in output]
        assert not any("DESCRIPTION" in t for t in texts), \
            "empty callable result hides the DESCRIPTION section"

    def test_search_callable_long_help_is_indexed(self, repl_env):
        """/search invokes callable long_help so dynamic text is findable."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="unique_cmd", args="", help="Plain help.",
            handler=lambda ctx, args: None,
            long_help=lambda ctx: "__search_probe__ appears only in dynamic help",
        ))

        # Act
        result = engine.dispatch("search __search_probe__")

        # Assert - the command surfaces via its dynamic long_help text
        names = result.value.splitlines() if result.value else []
        assert "unique_cmd" in names, \
            "callable long_help contributes to /search index"

    def test_help_cfg_shows_active_cfg_line(self, repl_env):
        """/help cfg prepends the Active cfg line (dynamic help wired via cfg_status)."""
        # Arrange -- repl_env gives us a config_path under tmp_path.
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help cfg")

        # Assert -- the dynamic state line lands in DESCRIPTION, and the
        # existing prose still follows it. The cfg name is the parent
        # directory stem; with the test fixture that's the tmp_path's
        # pytest-generated name, so we just look for the prefix.
        texts = [t for t, _ in output]
        assert any("Active cfg =" in t for t in texts), \
            "Active cfg label appears in DESCRIPTION"
        assert any("Three modes" in t for t in texts), \
            "existing cfg prose still renders after dynamic line"

    def test_help_port_baud_rate_shows_current_value(self, repl_env):
        """/help port.baud_rate prints the single-value state line."""
        # Arrange -- fixture cfg["baud_rate"] = 115200
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help port.baud_rate")

        # Assert
        texts = [t for t, _ in output]
        assert any("Current baud rate = 115200" in t for t in texts), \
            "dynamic state line reflects cfg value"

    def test_help_include_reports_zero_included(self, repl_env):
        """/help include opens with 'Currently included: none' when ns is empty."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help include")

        # Assert
        texts = [t for t, _ in output]
        assert any("Currently included: none" in t for t in texts), \
            "empty target_commands ns reflected in help"

    def test_help_include_reports_count_when_populated(self, repl_env):
        """Populating ns('target_commands') updates /help include output."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import TargetCommand
        engine.ctx.ns("target_commands").update({
            "X": TargetCommand(name="X", help="x", args=""),
            "Y": TargetCommand(name="Y", help="y", args=""),
        })

        # Act
        engine.dispatch("help include")

        # Assert
        texts = [t for t, _ in output]
        assert any("2 device commands" in t for t in texts), \
            "count reflects populated ns"

    def test_help_renders_flags_section(self, repl_env):
        """/help <cmd> shows a FLAGS section for commands that declare flags."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="flag_cmd", args="<name>", help="Has flags.",
            handler=lambda ctx, args: None,
            flags={"--table": "Use 256-entry lookup table."},
        ))

        # Act
        engine.dispatch("help flag_cmd")

        # Assert
        texts = [t for t, _ in output]
        assert any("FLAGS" in t for t in texts), "FLAGS header rendered"
        assert any("--table" in t for t in texts), "flag name rendered"
        assert any("lookup table" in t for t in texts), "description rendered"

    def test_help_collapses_alias_onto_canonical(self, repl_env):
        """Alias flags render on the same line as their canonical form."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="runner", args="<script>", help="Run a script.",
            handler=lambda ctx, args: None,
            flags={"--verbose": "Verbose mode.", "-v": "--verbose"},
        ))

        # Act
        engine.dispatch("help runner")

        # Assert - one FLAGS row containing both names, not two separate rows.
        texts = [t for t, _ in output]
        combo_lines = [t for t in texts if "--verbose" in t and "-v" in t]
        assert combo_lines, "alias listed alongside canonical on one line"
        desc_count = sum(1 for t in texts if "Verbose mode." in t)
        assert desc_count == 1, f"description printed once, got {desc_count}"

    # ── /help.dev (still uses forgiving lookup + man-page frame) ─────────────

    def test_help_dev_shows_docstring(self, repl_env):
        """/help.dev <cmd> shows the handler's Python docstring."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def my_handler(ctx, args):
            """This is the dev docstring.

            Args:
                ctx: Plugin context.
                args: Command arguments.
            """

        engine.register_plugin(PluginInfo(
            name="devtest", args="", help="A test command.",
            handler=my_handler,
        ))

        # Act
        engine.dispatch("help.dev devtest")

        # Assert - docstring lines appear inside the man-page DESCRIPTION
        texts = [t for t, _ in output]
        assert any("dev docstring" in t for t in texts), "docstring content shown"
        assert any("DESCRIPTION" in t for t in texts), "rendered inside DESCRIPTION"

    def test_help_dev_summary_bold(self, repl_env):
        """/help.dev renders summary line bold when followed by blank line."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler_with_summary(ctx, args):
            """Summary line here.

            Body text follows.
            """

        engine.register_plugin(PluginInfo(
            name="boldsummary", args="", help="Test.",
            handler=handler_with_summary,
        ))

        # Act
        engine.dispatch("help.dev boldsummary")

        # Assert
        markup_lines = [t for t, c in output if c == "markup"]
        assert any("[bold]Summary line here.[/]" in t for t in markup_lines), "bold summary"

    def test_help_dev_section_headers_bold(self, repl_env):
        """/help.dev renders Google-style section headers (Args:, Returns:) bold."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler_with_sections(ctx, args):
            """Do something.

            Args:
                ctx: Context.

            Returns:
                Nothing.
            """

        engine.register_plugin(PluginInfo(
            name="sections", args="", help="Test.",
            handler=handler_with_sections,
        ))

        # Act
        engine.dispatch("help.dev sections")

        # Assert
        markup_lines = [t for t, c in output if c == "markup"]
        assert any("[bold]Args:[/]" in t for t in markup_lines), "Args header bold"
        assert any("[bold]Returns:[/]" in t for t in markup_lines), "Returns header bold"

    def test_help_dev_param_labels_bold(self, repl_env):
        """/help.dev renders 'param: description' with param: bold."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler_with_params(ctx, args):
            """Do a thing.

            Args:
                ctx: Plugin context for output.
                args: Command arguments string.
            """

        engine.register_plugin(PluginInfo(
            name="params", args="", help="Test.",
            handler=handler_with_params,
        ))

        # Act
        engine.dispatch("help.dev params")

        # Assert
        markup_lines = [t for t, c in output if c == "markup"]
        assert any("[bold]ctx:[/] Plugin context" in t for t in markup_lines), "ctx: bold"
        assert any("[bold]args:[/] Command arguments" in t for t in markup_lines), "args: bold"

    def test_help_dev_no_docstring(self, repl_env):
        """/help.dev <cmd> with no docstring shows a message."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="nodoc", args="", help="No doc.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help.dev nodoc")

        # Assert
        texts = [t for t, _ in output]
        assert any("no docstring" in t for t in texts), "no-doc message shown"

    def test_help_dev_skips_long_help(self, repl_env):
        """/help.dev shows docstring instead of the command's long_help."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def documented_handler(ctx, args):
            """Handler docstring here."""

        engine.register_plugin(PluginInfo(
            name="both", args="", help="Has both.",
            long_help="This is the long help.",
            handler=documented_handler,
        ))

        # Act
        engine.dispatch("help.dev both")

        # Assert - docstring shown, long_help not shown
        texts = [t for t, _ in output]
        assert any("Handler docstring" in t for t in texts), "docstring shown"
        assert not any("This is the long help" in t for t in texts), (
            "user-facing long_help suppressed in dev mode"
        )


# -- /search ----------------------------------------------------------------


class TestSearch:
    """Tests for the top-level /search command -- the Google-style deep
    search counterpart to /help's forgiving lookup.
    """

    def test_search_literal_match(self, repl_env):
        """/search <literal> finds commands whose help contains the substring."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="widget_timer", args="", help="Sets a widget timeout.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="unrelated", args="", help="Does other stuff.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search timeout")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "widget_timer" in names, "matching command present"
        assert "unrelated" not in names, "non-matching command absent"

    def test_search_regex_anchor(self, repl_env):
        """Patterns with regex metacharacters are compiled as regex."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="proto.foo", args="", help="Alpha.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="other.proto", args="", help="Beta.",
            handler=lambda ctx, args: None,
        ))

        # Act - anchor to names starting with "proto."
        result = engine.dispatch(r"search ^proto\.")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "proto.foo" in names, "anchored match included"
        assert "other.proto" not in names, "non-anchored excluded"

    def test_search_invalid_regex(self, repl_env):
        """Malformed regex returns a failure result, not a crash."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("search (unclosed")

        # Assert
        assert result.success is False, "invalid regex should fail"
        assert "Invalid regex" in result.error, "failure message explains why"

    def test_search_no_args(self, repl_env):
        """Empty pattern returns usage-style failure."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("search")

        # Assert
        assert result.success is False, "empty pattern should fail"
        assert "Usage" in result.error, "shows usage line"

    def test_search_dev_flag_searches_docstring(self, repl_env):
        """--dev extends the search to include handler docstrings."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler_with_secret(ctx, args):
            """This handler mentions zebras in its docstring."""
            return None

        engine.register_plugin(PluginInfo(
            name="zebra_cmd", args="", help="Unrelated short help.",
            handler=handler_with_secret,
        ))

        # Act - without --dev, docstring isn't searched
        result_plain = engine.dispatch("search zebras")
        plain_names = result_plain.value.splitlines() if result_plain.value else []
        # Act - with --dev, docstring is searched
        result_dev = engine.dispatch("search --dev zebras")
        dev_names = result_dev.value.splitlines() if result_dev.value else []

        # Assert
        assert "zebra_cmd" not in plain_names, "docstring not searched without --dev"
        assert "zebra_cmd" in dev_names, "docstring searched with --dev"

    def test_search_matches_long_help(self, repl_env):
        """/search finds substrings in long_help (deep field /help doesn't hit)."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="gadget", args="", help="A gadget.",
            handler=lambda ctx, args: None,
            long_help="Pass --special_flag to enable the special mode.",
        ))

        # Act
        result = engine.dispatch("search special_flag")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "gadget" in names, "long_help match surfaces in /search"

    def test_search_matches_args(self, repl_env):
        """/search finds substrings in the args string."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="widget", args="<unusual_param>", help="A widget.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search unusual_param")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "widget" in names, "args match surfaces in /search"

    def test_search_multi_term_and(self, repl_env):
        """/search term1 term2 requires both terms (AND)."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="alpha_cmd", args="", help="table and crc stuff",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="table_only", args="", help="table only",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="crc_only", args="", help="crc only",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search table crc")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "alpha_cmd" in names, "command with both terms included"
        assert "table_only" not in names, "single-term match excluded"
        assert "crc_only" not in names, "single-term match excluded"

    def test_search_negative_term_excludes(self, repl_env):
        """/search foo -bar excludes commands matching 'bar'."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="keep", args="", help="banana stuff",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="drop", args="", help="banana and apricot",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search banana -apricot")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "keep" in names, "command matching positive-only kept"
        assert "drop" not in names, "command matching negative excluded"

    def test_search_finds_flag_name(self, repl_env):
        """/search finds declared flag names."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="boring_cmd", args="", help="Does nothing interesting.",
            handler=lambda ctx, args: None,
            flags={"--nitro": "Go faster."},
        ))

        # Act - needle only appears in a flag name
        result = engine.dispatch("search nitro")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "boring_cmd" in names, "command surfaced via flag name"

    def test_search_finds_flag_description(self, repl_env):
        """/search finds words inside a flag's description."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="rocket_cmd", args="", help="Launch something.",
            handler=lambda ctx, args: None,
            flags={"--fuel": "Use 256-entry combustion booster."},
        ))

        # Act
        result = engine.dispatch("search combustion")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "rocket_cmd" in names, "command surfaced via flag description"

    def test_search_returns_value_for_scripting(self, repl_env):
        """CmdResult.value is the newline-joined matching command names."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="alpha_cmd", args="", help="zzz unique-token-xyz zzz.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search unique-token-xyz")

        # Assert
        actual = result.value
        expected = "alpha_cmd"
        assert actual == expected, f"{actual} == {expected}"

    def test_search_no_meta_chars_uses_literal_grammar(self, repl_env):
        """Without regex metacharacters, `foo bar` AND-matches separately,
        not as a literal `"foo bar"` substring.
        """
        # Arrange - plugin has 'foo' in name and 'bar' in args but never
        # the adjacent string "foo bar" anywhere.
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="foo_cmd", args="<bar>", help="Separate terms command.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("search foo bar")

        # Assert - literal AND grammar matches; a regex for "foo bar"
        # (literal with space) would not match across fields.
        names = result.value.splitlines() if result.value else []
        assert "foo_cmd" in names, "AND grammar matches across fields"


# -- /show ----------------------------------------------------------------


class TestShow:
    def test_show_cfg(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("show.cfg")

        # Assert
        texts = [t for t, _ in output]
        assert any("COM4" in t for t in texts), "config value shown"
        assert any("end" in t for t in texts), "end marker shown"

    def test_show_file(self, repl_env, tmp_path):
        # Arrange
        engine, _, _, output = repl_env
        test_file = tmp_path / "test.txt"
        test_file.write_text("line one\nline two", encoding="utf-8")

        # Act
        engine.dispatch(f"show {test_file}")

        # Assert
        texts = [t for t, _ in output]
        assert any("line one" in t for t in texts), "first line shown"
        assert any("line two" in t for t in texts), "second line shown"

    def test_show_missing_file(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("show /nonexistent/file.txt")

        # Assert
        assert any("not found" in t.lower() for t, _ in output), "error message"
        assert output[-1][1] == "red", "shown in red"

    def test_show_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show")
        assert any("Usage" in t for t, _ in output), "usage message"

    def test_show_nonexistent_name(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show $bogus")
        assert any("not found" in t.lower() for t, _ in output), "treated as filename"


# -- /os ------------------------------------------------------------------


class TestOs:
    def test_os_disabled(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("os echo hi")
        assert any("disabled" in t for t, _ in output), "blocked by default"

    def test_os_enabled(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os echo hello_from_os")

        # Assert
        texts = [t for t, _ in output]
        assert any("hello_from_os" in t for t in texts), "shell output captured"

    def test_os_no_args(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os")

        # Assert
        assert any("Usage" in t for t, _ in output), "usage message"


# -- dispatch edge cases ---------------------------------------------------


class TestDispatch:
    def test_unknown_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("totally_unknown_cmd")
        assert any("Unknown" in t for t, _ in output), "error message"
        assert output[-1][1] == "red", "shown in red"

    def test_empty_dispatch_shows_help(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("")
        texts = [t for t, _ in output]
        assert any("help" in t.lower() for t in texts), "empty triggers help"

    def test_command_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("ECHO off")
        assert engine.ctx.ns("flags")["echo"] is False, "uppercase command works"


# -- /grep ----------------------------------------------------------------

_SCREEN_TEXT = """\
Hello world
ERROR: something failed
All good here
warning: low battery
Another error line
normal line"""


class TestGrep:
    def _set_screen_text(self, engine, text):
        """Set get_screen_text on the engine's context."""
        engine.ctx = engine.ctx.__class__(
            **{**engine.ctx.__dict__, "get_screen_text": lambda: text}
        )

    def test_grep_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("grep")
        assert output[-1][1] == "red", "usage shown in red"
        assert "Usage" in output[-1][0], "usage text present"

    def test_grep_matches(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)

        # Act
        engine.dispatch("grep error")

        # Assert
        texts = [t for t, _ in output]
        assert any("2 match(es)" in t for t in texts), "match count shown"
        assert any("2 |" in t and "ERROR" in t for t in texts), "line 2 matched"
        assert any("5 |" in t and "error" in t for t in texts), "line 5 matched"

    def test_grep_no_matches(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep zzzz_not_found")
        texts = [t for t, _ in output]
        assert any("no matches" in t for t in texts), "no matches message shown"

    def test_grep_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep ERROR")
        texts = [t for t, _ in output]
        assert any("2 match(es)" in t for t in texts), "both cases matched"

    def test_grep_regex(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep error|warning")
        texts = [t for t, _ in output]
        assert any("3 match(es)" in t for t in texts), "regex alternation works"

    def test_grep_skips_own_output(self, repl_env):
        # Arrange - scrollback contains prior grep output and echoed command
        engine, _, _, output = repl_env
        text = (
            "real error line\n"
            "  grep: 'error' - 1 match(es)\n"
            "  grep:     1 | real error line\n"
            "> /grep error"
        )
        self._set_screen_text(engine, text)

        # Act
        engine.dispatch("grep error")

        # Assert - only the real line matches, grep noise is skipped
        texts = [t for t, _ in output]
        assert any("1 match(es)" in t for t in texts), "only 1 match"
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 1, "grep output and echoed cmd excluded"

    def test_grep_bad_regex(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep [invalid")
        assert output[-1][1] == "red", "error shown in red"
        assert "Invalid pattern" in output[-1][0], "error message shown"

    def test_grep_max_output_default(self, repl_env):
        # Arrange - create text with 150 matching lines, no max_grep_lines in cfg
        engine, _, _, output = repl_env
        lines = [f"match line {i}" for i in range(150)]
        self._set_screen_text(engine, "\n".join(lines))

        # Act
        engine.dispatch("grep match")

        # Assert - default cap is 100
        texts = [t for t, _ in output]
        assert any("first 100 of 150" in t for t in texts)  # assert cap message
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 100  # assert only 100 lines output

    def test_grep_max_output_from_config(self, repl_env):
        # Arrange - set custom max_grep_lines
        engine, cfg, _, output = repl_env
        cfg["max_grep_lines"] = 5
        lines = [f"match line {i}" for i in range(20)]
        self._set_screen_text(engine, "\n".join(lines))

        # Act
        engine.dispatch("grep match")

        # Assert - cap uses config value
        texts = [t for t, _ in output]
        assert any("first 5 of 20" in t for t in texts)  # assert config cap message
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 5  # assert only 5 lines output


# -- /cls -----------------------------------------------------------------


class TestCls:
    def test_cls_calls_clear_screen(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        cleared = []
        engine.ctx.clear_screen = lambda: cleared.append(True)

        # Act
        engine.dispatch("cls")

        # Assert
        assert len(cleared) == 1  # clear_screen called


# -- /show_line_endings ---------------------------------------------------


class TestEol:
    def test_eol_toggle_on(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["show_line_endings"] = False

        # Act
        engine.dispatch("show_line_endings")

        # Assert
        assert cfg["show_line_endings"] is True  # toggled on

    def test_eol_toggle_off(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["show_line_endings"] = True

        # Act
        engine.dispatch("show_line_endings")

        # Assert
        assert cfg["show_line_endings"] is False  # toggled off

    def test_eol_explicit_on(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["show_line_endings"] = False

        # Act
        engine.dispatch("show_line_endings on")

        # Assert
        assert cfg["show_line_endings"] is True  # set to on

    def test_eol_explicit_off(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["show_line_endings"] = True

        # Act
        engine.dispatch("show_line_endings off")

        # Assert
        assert cfg["show_line_endings"] is False  # set to off


# -- /cap (arg parsing) ---------------------------------------------------


class TestCapArgParsing:
    """Test cap.py keyword extraction - pure function, no serial needed."""

    def test_extract_keywords_basic(self):
        from termapy.builtins.plugins.cap import _extract_keyword_sections

        # Act
        result = _extract_keyword_sections(
            "data.csv fmt=Val:U1-2 records=50 cmd=AT+BINDUMP u16 50"
        )

        # Assert
        assert result["_positional"].strip() == "data.csv"
        assert result["fmt"] == "Val:U1-2"
        assert result["records"] == "50"
        assert result["cmd"] == "AT+BINDUMP u16 50"

    def test_extract_keywords_mode(self):
        from termapy.builtins.plugins.cap import _extract_keyword_sections

        # Act
        result = _extract_keyword_sections("out.txt timeout=5s mode=append echo=on")

        # Assert
        assert result["timeout"] == "5s"
        assert result["mode"] == "append"
        assert result["echo"] == "on"

    def test_extract_keywords_no_cmd(self):
        from termapy.builtins.plugins.cap import _extract_keyword_sections

        # Act
        result = _extract_keyword_sections("data.bin bytes=256")

        # Assert
        assert "cmd" not in result
        assert result["bytes"] == "256"

    def test_extract_keywords_fmt_multiword(self):
        from termapy.builtins.plugins.cap import _extract_keyword_sections

        # Act
        result = _extract_keyword_sections(
            "out.csv fmt=A:U1-2 B:F3-6 records=10"
        )

        # Assert
        assert result["fmt"] == "A:U1-2 B:F3-6"  # multi-word fmt preserved
        assert result["records"] == "10"

    def test_cap_text_missing_timeout(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cap.text data.txt")

        # Assert
        assert any("Usage" in t for t, _ in output)  # shows usage

    def test_cap_bin_missing_bytes(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cap.bin data.bin")

        # Assert
        assert any("Usage" in t for t, _ in output)  # shows usage

    def test_cap_struct_missing_fmt(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cap.struct data.csv records=50")

        # Assert
        assert any("Usage" in t for t, _ in output)  # shows usage

    def test_cap_stop_no_capture(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act - should not crash
        engine.dispatch("cap.stop")

    def test_parse_mode(self):
        from termapy.builtins.plugins.cap import _parse_mode

        # Assert
        assert _parse_mode({"mode": "new"}) == "w"
        assert _parse_mode({"mode": "n"}) == "w"
        assert _parse_mode({"mode": "append"}) == "a"
        assert _parse_mode({"mode": "a"}) == "a"
        assert _parse_mode({}) == "w"  # default is new
        assert _parse_mode({"mode": "bad"}) is None


# ── /cap.wire ────────────────────────────────────────────────────────────────


class TestCapWire:
    """Verify the wrap-and-show-hex handler.

    The handler registers RX/TX observers via ``ctx.rx_observer()`` /
    ``ctx.tx_observer()``, dispatches the wrapped command, and emits
    a two-line hex+repr envelope.  Tests inject fake observer hooks
    so we control exactly which bytes "flow" during the dispatch.
    """

    def _wire_observers(self, ctx):
        """Replace ctx's private observer hooks with a recording pair.

        Returns the lists of registered RX/TX callbacks so the test
        can fire them to simulate bytes arriving / being sent.
        """
        rx_callbacks: list = []
        tx_callbacks: list = []
        ctx._add_rx_observer = rx_callbacks.append
        ctx._remove_rx_observer = (
            lambda cb: rx_callbacks.remove(cb) if cb in rx_callbacks else None
        )
        ctx._add_tx_observer = tx_callbacks.append
        ctx._remove_tx_observer = (
            lambda cb: tx_callbacks.remove(cb) if cb in tx_callbacks else None
        )
        return rx_callbacks, tx_callbacks

    def test_wire_no_args_fails_with_usage(self, repl_env):
        # Arrange
        engine, _, _, _ = repl_env

        # Act
        result = engine.dispatch("cap.wire")

        # Assert
        assert result.success is False, "/cap.wire requires args"
        assert "Usage" in result.error, "error names usage"

    def test_wire_envelope_shape(self, repl_env):
        # Arrange
        engine, _, _, _ = repl_env
        ctx = engine.ctx
        rx_cbs, tx_cbs = self._wire_observers(ctx)
        # Replace ctx.dispatch so it fires observers to simulate bytes
        # flowing during the wrapped command.
        from termapy.plugins import CmdResult

        def fake_dispatch(cmd):
            for cb in tx_cbs:
                cb(b"AT+VER\r")
            for cb in rx_cbs:
                cb(b"VER=1.2.3\r\n")
            return CmdResult.ok()

        ctx.dispatch = fake_dispatch

        # Act
        result = engine.dispatch("cap.wire AT+VER")

        # Assert -- 7-byte TX ("AT+VER\r"), 11-byte RX ("VER=1.2.3\r\n")
        envelope = result.value
        assert envelope["tx_bytes"] == 7, "TX byte count"
        assert envelope["rx_bytes"] == 11, "RX byte count"
        assert envelope["tx_hex"] == "41542b5645520d", "TX hex"
        assert envelope["rx_hex"] == "5645523d312e322e330d0a", "RX hex"
        assert envelope["tx_text"] == "AT+VER\r", "TX decoded"
        assert envelope["rx_text"] == "VER=1.2.3\r\n", "RX decoded"

    def test_wire_renders_hex_and_repr(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        ctx = engine.ctx
        rx_cbs, tx_cbs = self._wire_observers(ctx)
        from termapy.plugins import CmdResult

        def fake_dispatch(cmd):
            for cb in tx_cbs:
                cb(b"x\r")
            for cb in rx_cbs:
                cb(b"y\r\n")
            return CmdResult.ok()

        ctx.dispatch = fake_dispatch

        # Act
        engine.dispatch("cap.wire x")

        # Assert -- TX line has hex AND a Python repr() that exposes
        # non-printing characters as escape sequences (the canonical
        # use case for /cap.wire is line-ending debugging).
        rendered = "\n".join(t for t, _ in output)
        assert "78 0d" in rendered, "TX bytes shown as hex"
        assert "79 0d 0a" in rendered, "RX bytes shown as hex"
        assert r"'x\r'" in rendered, "TX shown via repr() so \\r is visible"
        assert r"'y\r\n'" in rendered, "RX shown via repr() so \\r\\n is visible"

    def test_wire_no_traffic_emits_friendly_message(self, repl_env):
        # Arrange -- dispatch happens but no bytes flow (dispatched
        # something that doesn't touch serial, e.g. /help)
        engine, _, _, output = repl_env
        ctx = engine.ctx
        self._wire_observers(ctx)
        from termapy.plugins import CmdResult
        ctx.dispatch = lambda cmd: CmdResult.ok()

        # Act
        result = engine.dispatch("cap.wire help")

        # Assert
        rendered = "\n".join(t for t, _ in output)
        assert "no traffic observed" in rendered, "friendly message on zero bytes"
        envelope = result.value
        assert envelope["tx_bytes"] == 0, "zero TX"
        assert envelope["rx_bytes"] == 0, "zero RX"

    def test_wire_releases_observers_on_exception(self, repl_env):
        # Arrange -- dispatch raises mid-block; observers must still be
        # unregistered so subsequent commands aren't tapped.
        engine, _, _, _ = repl_env
        ctx = engine.ctx
        rx_cbs, tx_cbs = self._wire_observers(ctx)

        def boom_dispatch(cmd):
            raise RuntimeError("simulated handler crash")

        ctx.dispatch = boom_dispatch

        # Act -- wrapping dispatch is the engine.dispatch that runs
        # /cap.wire; the handler's BoundaryException-protected layer
        # swallows the exception and returns a fail result.
        result = engine.dispatch("cap.wire AT+CRASH")

        # Assert -- whatever the result, observers must have been
        # released.  This is the whole point of the with-block.
        assert rx_cbs == [], "RX observer released even on exception"
        assert tx_cbs == [], "TX observer released even on exception"


# -- /cap.text with mock start_capture ------------------------------------


class TestCapTextHandler:
    def test_cap_text_starts_capture(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        captures = []
        engine.ctx.engine.start_capture = lambda **kw: (
            captures.append(kw) or True
        )

        # Act
        engine.dispatch("cap.text log.txt timeout=3s")

        # Assert
        assert len(captures) == 1
        assert captures[0]["mode"] == "text"
        assert captures[0]["duration"] == 3.0

    def test_cap_text_with_mode_append(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        captures = []
        engine.ctx.engine.start_capture = lambda **kw: (
            captures.append(kw) or True
        )

        # Act
        engine.dispatch("cap.text log.txt timeout=5s mode=append")

        # Assert
        assert captures[0]["file_mode"] == "a"


# -- /cap.struct with mock start_capture -----------------------------------


class TestCapStructHandler:
    def test_cap_struct_starts_capture(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        captures = []
        engine.ctx.engine.start_capture = lambda **kw: (
            captures.append(kw) or True
        )

        # Act
        engine.dispatch("cap.struct data.csv fmt=Val:U1-2 records=50")

        # Assert
        assert len(captures) == 1
        assert captures[0]["mode"] == "bin"
        assert len(captures[0]["columns"]) == 1  # one column
        assert captures[0]["record_size"] == 2  # U1-2 = 2 bytes

    def test_cap_struct_with_cmd(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        captures = []
        dispatched = []
        engine.ctx.engine.start_capture = lambda **kw: (
            captures.append(kw) or True
        )
        engine.ctx.dispatch = lambda cmd: dispatched.append(cmd)
        engine.ctx.serial_drain = lambda: None

        # Act
        engine.dispatch("cap.struct data.csv fmt=Val:U1-2 records=50 cmd=AT+DUMP 50")

        # Assert
        assert len(captures) == 1
        assert len(dispatched) == 1
        assert dispatched[0] == "AT+DUMP 50"  # cmd dispatched after capture starts

    def test_cap_struct_invalid_fmt(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cap.struct data.csv fmt=INVALID records=50")

        # Assert
        assert any("Invalid format" in t.lower() or "invalid" in t.lower()
                    for t, _ in output)

    def test_cap_struct_must_specify_size(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cap.struct data.csv fmt=Val:U1-2")

        # Assert
        assert any("records=N" in t or "bytes=N" in t for t, _ in output)


# -- /exit ----------------------------------------------------------------


class TestExit:
    def test_exit_calls_exit_app(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        exited = []
        engine.ctx.exit_app = lambda: exited.append(True)

        # Act
        engine.dispatch("exit")

        # Assert
        assert len(exited) == 1  # exit_app called


# -- /confirm -------------------------------------------------------------


class TestConfirm:
    def test_confirm_yes_continues(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine.ctx.confirm = lambda msg: True  # user clicks Yes

        # Act
        engine.dispatch("confirm Are you sure?")

        # Assert - no "Cancelled" message, script_stop not called
        assert not any("Cancelled" in t for t, _ in output)

    def test_confirm_cancel_stops_script(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine.ctx.confirm = lambda msg: False  # user clicks Cancel

        # Act
        engine.dispatch("confirm Are you sure?")

        # Assert
        assert any("Cancelled" in t for t, _ in output)  # shows cancelled
        assert engine._script_stop.is_set()  # script stop triggered

    def test_confirm_default_message(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        messages = []
        engine.ctx.confirm = lambda msg: (messages.append(msg) or True)

        # Act
        engine.dispatch("confirm")

        # Assert
        assert messages == ["Continue?"]  # default message

    def test_confirm_custom_message(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        messages = []
        engine.ctx.confirm = lambda msg: (messages.append(msg) or True)

        # Act
        engine.dispatch("confirm Deploy to production?")

        # Assert
        assert messages == ["Deploy to production?"]  # custom message


# -- /cfg (read operations) -----------------------------------------------


class TestCfgRead:
    def test_cfg_dump_shows_all(self, repl_env):
        # Arrange -- bare /cfg now opens the Cfg picker (TUI) or shows
        # /help cfg (CLI); /cfg.dump is the explicit "print every key"
        # path that this test guards.
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg.dump")

        # Assert - should list config keys
        texts = [t for t, _ in output]
        assert any("port" in t for t in texts), "shows port key"
        assert any("baud_rate" in t or "115200" in t for t in texts), "shows baud_rate"

    def test_cfg_specific_key(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg port")

        # Assert
        texts = [t for t, _ in output]
        assert any("COM4" in t for t in texts)  # shows port value

    def test_cfg_unknown_key(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cfg nonexistent_key")

        # Assert
        texts = [t for t, _ in output]
        assert any("not found" in t.lower() or "unknown" in t.lower()
                    for t in texts)  # error message

    def test_cfg_info(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("cfg.info")

        # Assert - should output something about the config
        assert len(output) > 0  # produced some output

    def test_cfg_dump(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg.dump")

        # Assert - should dump JSON
        texts = [t for t, _ in output]
        assert any("port" in t for t in texts)  # JSON includes port


# -- /repeat ----------------------------------------------------------------


class TestRepeat:
    def test_missing_count(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat cmd=AT")

        # Assert - error about missing count
        actual = [t for t, _ in output]
        assert any("Count is required" in t for t in actual), f"expected 'Count is required' error, got: {actual}"

    def test_missing_cmd(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=3")

        # Assert - error about missing cmd
        actual = [t for t, _ in output]
        assert any("Usage:" in t for t in actual), f"expected 'Usage:' error, got: {actual}"

    def test_count_not_integer(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=abc cmd=AT")

        # Assert - error about non-integer count
        actual = [t for t, _ in output]
        assert any("integer" in t for t in actual), f"expected 'integer' error, got: {actual}"

    def test_count_zero(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=0 cmd=AT")

        # Assert - error about count > 0
        actual = [t for t, _ in output]
        assert any("> 0" in t for t in actual), f"expected '> 0' error, got: {actual}"

    def test_count_negative(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=-1 cmd=AT")

        # Assert - error about count > 0
        actual = [t for t, _ in output]
        assert any("> 0" in t for t in actual), f"expected '> 0' error, got: {actual}"

    def test_invalid_delay(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=1 delay=bogus cmd=AT")

        # Assert - error about invalid delay
        actual = [t for t, _ in output]
        assert any("duration" in t.lower() or "invalid" in t.lower() for t in actual), f"expected duration/invalid error, got: {actual}"

    def test_dispatches_n_times(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        dispatched = []
        engine.ctx.dispatch = lambda cmd: dispatched.append(cmd)

        # Act
        engine.dispatch("repeat count=3 cmd=AT+TEMP")

        # Assert - command dispatched 3 times
        assert dispatched == ["AT+TEMP", "AT+TEMP", "AT+TEMP"], f"expected 3 dispatches, got: {dispatched}"

    def test_sets_iteration_variable(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        seen_values = []
        engine.ctx.dispatch = lambda cmd: seen_values.append(_VARS.get("REPEAT_N"))

        # Act
        engine.dispatch("repeat count=3 cmd=AT")

        # Assert - variable was 1, 2, 3 during iterations
        assert seen_values == ["1", "2", "3"], f"expected iteration values ['1','2','3'], got: {seen_values}"

    def test_custom_variable_name(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        seen_values = []
        engine.ctx.dispatch = lambda cmd: seen_values.append(_VARS.get("I"))

        # Act
        engine.dispatch("repeat count=2 var=I cmd=AT")

        # Assert - custom variable name used
        assert seen_values == ["1", "2"], f"expected var=I values ['1','2'], got: {seen_values}"

    def test_variable_cleaned_up(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        engine.ctx.dispatch = lambda cmd: None

        # Act
        engine.dispatch("repeat count=2 cmd=AT")

        # Assert - variable removed after repeat
        assert "REPEAT_N" not in _VARS, f"REPEAT_N should be cleaned up, but _VARS contains: {_VARS}"

    def test_variable_cleaned_up_on_error(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env

        def boom(cmd):
            raise RuntimeError("dispatch error")

        engine.ctx.dispatch = boom

        # Act - handler should not crash the engine
        engine.dispatch("repeat count=2 cmd=AT")

        # Assert - variable cleaned up even on error
        assert "REPEAT_N" not in _VARS, f"REPEAT_N should be cleaned up after error, but _VARS contains: {_VARS}"

    def test_completes_silently(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine.ctx.dispatch = lambda cmd: None

        # Act
        engine.dispatch("repeat count=5 cmd=AT")

        # Assert - no output from repeat itself (commands produce their own output)
        actual = [t for t, _ in output]
        assert not any("Repeated" in t for t in actual), f"repeat should complete silently, got: {actual}"

    def test_stoppable_via_event(self, repl_env):
        # Arrange
        from threading import Event

        engine, _, _, output = repl_env
        stop_event = Event()
        engine.ctx.engine.script_stop_event = stop_event
        dispatched = []

        def dispatch_and_stop(cmd):
            dispatched.append(cmd)
            if len(dispatched) == 2:
                stop_event.set()  # stop after 2nd iteration

        engine.ctx.dispatch = dispatch_and_stop

        # Act
        engine.dispatch("repeat count=10 cmd=AT")

        # Assert - stopped after 2 iterations, not all 10
        assert len(dispatched) == 2, f"expected 2 dispatches before stop, got: {len(dispatched)}"
        actual = [t for t, _ in output]
        assert any("2/10" in t for t in actual), f"expected '2/10' in stop message, got: {actual}"

    def test_stoppable_during_delay(self, repl_env):
        # Arrange
        from threading import Event

        engine, _, _, output = repl_env
        stop_event = Event()
        engine.ctx.engine.script_stop_event = stop_event
        dispatched = []

        def dispatch_and_stop(cmd):
            dispatched.append(cmd)
            stop_event.set()  # stop after 1st iteration, during delay

        engine.ctx.dispatch = dispatch_and_stop

        # Act
        engine.dispatch("repeat count=100 delay=10s cmd=AT")

        # Assert - stopped during delay, not blocked for 10s * 99
        assert len(dispatched) == 1, f"expected 1 dispatch before stop, got: {len(dispatched)}"
        actual = [t for t, _ in output]
        assert any("1/100" in t for t in actual), f"expected '1/100' in stop message, got: {actual}"


# -- /port.chip.list ---------------------------------------------------------


class TestPortChipList:
    """/port.chip.list dumps the USB-serial chip lookup table."""

    def test_unfiltered_lists_many_chips(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("port.chip.list")

        # Assert -- dumps a header, divider, at least a handful of rows.
        assert result.success, "list succeeds with no filter"
        texts = [t for t, _ in output]
        assert any("VID:PID" in t for t in texts), "header row printed"
        # Spot-check one well-known chip makes it into the dump.
        assert any("FT232R" in t for t in texts), "FTDI FT232R present"
        # Return value is "Count=<N>" for script capture.
        assert result.value.startswith("Count="), \
            f"value should be 'Count=<N>', got: {result.value!r}"
        count = int(result.value.split("=")[1])
        assert count > 20, f"expected many chip rows, got Count={count}"

    def test_filter_narrows_output(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("port.chip.list ftdi")

        # Assert -- only FTDI rows printed, Count reflects the match size.
        assert result.success, "filtered list succeeds"
        texts = [t for t, _ in output]
        chip_rows = [t for t in texts if ":" in t and "FTDI" in t]
        assert chip_rows, "at least one FTDI row rendered"
        # There are ~10 FTDI chips in the table.
        count = int(result.value.split("=")[1])
        assert 5 <= count <= 25, \
            f"expected roughly a dozen FTDI rows, got Count={count}"

    def test_filter_is_case_insensitive(self, repl_env):
        # Arrange
        engine, *_ = repl_env

        # Act -- uppercase filter should still match "SparkFun" in the table
        result = engine.dispatch("port.chip.list SPARK")

        # Assert
        assert result.success, "case-insensitive filter works"
        count = int(result.value.split("=")[1])
        assert count > 0, "entries found despite uppercase filter"

    def test_no_matches_prints_yellow_warning(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act -- a filter that matches nothing in the table
        result = engine.dispatch("port.chip.list __does_not_exist__")

        # Assert
        assert result.success, "no-match still returns ok"
        assert result.value == "Count=0", \
            f"Count=0 on no matches, got: {result.value!r}"
        texts_and_colors = [(t, c) for t, c in output]
        assert any(
            "No chips match" in t and c == "yellow"
            for t, c in texts_and_colors
        ), "no-match warning rendered in yellow"
