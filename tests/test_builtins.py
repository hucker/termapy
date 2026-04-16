"""Tests for built-in REPL commands dispatched through ReplEngine."""

import json

import pytest

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
        ),
    )
    engine.set_context(ctx)
    # Seed the engine-reserved `flags` namespace with the defaults that
    # _build_context would set in production.  Tests that construct
    # PluginContext directly bypass that path, so do it explicitly here.
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["verbose"] = True
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

        # Act / Assert — toggle off
        engine.dispatch("echo")
        assert engine.ctx.ns("flags")["echo"] is False, "toggled off"

        # Act / Assert — toggle on
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


class TestHelp:
    def test_help_lists_commands(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [t for t, _ in output]
        assert any("help" in t for t in texts), "help command listed"
        assert any("cfg" in t for t in texts), "cfg command listed"

    def test_help_specific_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help echo")

        # Assert
        texts = [t for t, _ in output]
        assert any("echo" in t.lower() for t in texts), "echo help shown"

    def test_help_unknown_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help nonexistent")

        # Assert
        assert any("Unknown" in t for t, _ in output), "error message"
        assert output[-1][1] == "red", "shown in red"

    def test_help_shows_long_help(self, repl_env):
        """/help <cmd> shows LONG_HELP lines when present."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="testcmd", args="<arg>", help="Short help.",
            long_help="Line one.\nLine two.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help testcmd")

        # Assert — long help lines appear indented
        texts = [t for t, _ in output]
        assert any("Line one." in t for t in texts), "first long_help line"
        assert any("Line two." in t for t in texts), "second long_help line"

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

        # Assert — docstring lines appear
        texts = [t for t, _ in output]
        assert any("dev docstring" in t for t in texts), "docstring content shown"
        assert any("developer docstring" in t for t in texts), "header shown"

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

        # Assert — summary rendered via write_markup with bold tags
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

        # Assert — Args: and Returns: rendered bold via write_markup
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

        # Assert — param names bold, descriptions not in bold tags
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
        """/help.dev shows docstring instead of LONG_HELP."""
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

        # Assert — docstring shown, LONG_HELP not shown
        texts = [t for t, _ in output]
        assert any("Handler docstring" in t for t in texts), "docstring shown"
        assert not any("long help" in t.lower() for t in texts), "LONG_HELP not shown"

    def test_help_no_long_help_omits_extra(self, repl_env):
        """/help <cmd> with empty LONG_HELP shows only the one-liner."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="briefcmd", args="", help="Just brief.",
            handler=lambda ctx, args: None,
        ))

        # Act
        engine.dispatch("help briefcmd")

        # Assert — only one output line (the usage line)
        texts = [t for t, _ in output]
        actual = len(texts)
        expected = 1  # just the "/briefcmd — Just brief." line
        assert actual == expected, f"expected {actual} == {expected}"

    def test_help_search_literal_match(self, repl_env):
        """/help.search <literal> finds commands whose help contains the substring."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="widgettimer", args="", help="Sets a widget timeout.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="unrelated", args="", help="Does other stuff.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help.search timeout")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "widgettimer" in names, "matching command present"
        assert "unrelated" not in names, "non-matching command absent"

    def test_help_search_regex_anchor(self, repl_env):
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

        # Act — anchor to names starting with "proto."
        result = engine.dispatch(r"help.search ^proto\.")

        # Assert
        names = result.value.splitlines() if result.value else []
        assert "proto.foo" in names, "anchored match included"
        assert "other.proto" not in names, "non-anchored excluded"

    def test_help_search_invalid_regex(self, repl_env):
        """Malformed regex returns a failure result, not a crash."""
        # Arrange
        engine, _, _, output = repl_env

        # Act — unbalanced paren is a regex error
        result = engine.dispatch("help.search (unclosed")

        # Assert
        assert result.success is False, "invalid regex should fail"
        assert "Invalid regex" in result.error, "failure message explains why"

    def test_help_search_no_args(self, repl_env):
        """Empty pattern returns usage-style failure."""
        # Arrange
        engine, _, _, output = repl_env

        # Act
        result = engine.dispatch("help.search")

        # Assert
        assert result.success is False, "empty pattern should fail"
        assert "Usage" in result.error, "shows usage line"

    def test_help_search_dev_flag_searches_docstring(self, repl_env):
        """--dev extends the search to include handler docstrings."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler_with_secret(ctx, args):
            """This handler mentions zebras in its docstring."""
            return None

        engine.register_plugin(PluginInfo(
            name="zebracmd", args="", help="Unrelated short help.",
            handler=handler_with_secret,
        ))

        # Act — without --dev, docstring isn't searched
        result_plain = engine.dispatch("help.search zebras")
        plain_names = result_plain.value.splitlines() if result_plain.value else []
        # Act — with --dev, docstring is searched
        result_dev = engine.dispatch("help.search --dev zebras")
        dev_names = result_dev.value.splitlines() if result_dev.value else []

        # Assert
        assert "zebracmd" not in plain_names, "docstring not searched without --dev"
        assert "zebracmd" in dev_names, "docstring searched with --dev"

    def test_help_partial_single_match(self, repl_env):
        """/help <partial> falls back to substring match when exactly one hits."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="zebralicious", args="", help="Has stripes.",
            handler=lambda ctx, args: None,
            long_help="Stripes long help.",
        ))

        # Act — user typed a fragment, not the full name
        result = engine.dispatch("help zebral")

        # Assert — rendered as if they typed the full name (no "Did you mean").
        # Strip [u]...[/u] since the match is underlined.
        import re as _re
        texts = [_re.sub(r"\[/?u\]", "", t) for t, _ in output]
        assert result.success, "single-match fallback succeeds"
        assert any("zebralicious" in t for t in texts), "shows the matched command"
        assert not any("Did you mean" in t for t in texts), "no disambiguation list"

    def test_help_partial_multi_match_did_you_mean(self, repl_env):
        """/help <partial> with multiple hits renders a 'Did you mean' list."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="stripe.alpha", args="", help="One.",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="stripe.beta", args="", help="Two.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help stripe")

        # Assert — both options shown under a "Did you mean" header.
        # Strip Rich [u]...[/u] underline markup so substring checks work
        # regardless of how the renderer highlights the matched needle.
        import re as _re
        texts = [_re.sub(r"\[/?u\]", "", t) for t, _ in output]
        assert result.success, "multi-match fallback still succeeds"
        assert any("Did you mean" in t for t in texts), "disambiguation header"
        assert any("stripe.alpha" in t for t in texts), "first option listed"
        assert any("stripe.beta" in t for t in texts), "second option listed"

    def test_help_partial_matches_long_help(self, repl_env):
        """Fallback finds substrings in long_help (e.g. --table inside docs)."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="gadget", args="", help="A gadget.",
            handler=lambda ctx, args: None,
            long_help="Pass --specialflag to enable the special mode.",
        ))

        # Act — needle appears only in long_help, not in the name
        result = engine.dispatch("help specialflag")

        # Assert — gadget is found via its long_help text
        assert result.success, "long_help substring match succeeds"
        texts = [t for t, _ in output]
        assert any("gadget" in t for t in texts), "gadget surfaced from long_help"

    def test_help_partial_matches_args(self, repl_env):
        """Fallback finds substrings in the args string too."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="widget", args="<unusualparam>", help="A widget.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help unusualparam")

        # Assert
        assert result.success, "args substring match succeeds"
        texts = [t for t, _ in output]
        assert any("widget" in t for t in texts), "widget surfaced from args"

    def test_help_multi_term_and(self, repl_env):
        """/help term1 term2 requires both terms to match (AND)."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="alpha", args="", help="table and crc stuff",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="tableonly", args="", help="table only, no other thing",
            handler=lambda ctx, args: None,
        ))
        engine.register_plugin(PluginInfo(
            name="crconly", args="", help="crc only, no other thing",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help table crc")

        # Assert — only the command matching BOTH terms surfaces
        import re as _re
        texts = [_re.sub(r"\[/?u\]", "", t) for t, _ in output]
        assert result.success, "AND match succeeds"
        assert any("alpha" in t for t in texts), "command matching both terms shown"
        assert not any("tableonly" in t for t in texts), "single-term match filtered"
        assert not any("crconly" in t for t in texts), "single-term match filtered"

    def test_help_negative_term_excludes(self, repl_env):
        """/help foo -bar excludes commands that also match 'bar'."""
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
        result = engine.dispatch("help banana -apricot")

        # Assert
        import re as _re
        texts = [_re.sub(r"\[/?u\]", "", t) for t, _ in output]
        assert result.success, "query succeeds"
        assert any("keep" in t for t in texts), "matching command kept"
        assert not any("drop" in t for t in texts), "excluded command removed"

    def test_help_partial_no_match_fails(self, repl_env):
        """/help with a fragment matching nothing still fails."""
        # Arrange
        engine, _, _, output = repl_env

        # Act — no command contains "xyzzynoplace"
        result = engine.dispatch("help xyzzynoplace")

        # Assert
        assert result.success is False, "no matches should fail"
        assert "Unknown command" in result.error, "error names the miss"

    def test_help_dev_partial_single_match(self, repl_env):
        """/help.dev <partial> uses the same substring fallback."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo

        def handler(ctx, args):
            """Docstring for fallback test."""
            return None

        engine.register_plugin(PluginInfo(
            name="uniquename", args="", help="Unique.",
            handler=handler,
        ))

        # Act
        result = engine.dispatch("help.dev uniqu")

        # Assert
        texts = [t for t, _ in output]
        assert result.success, "fallback resolves for help.dev too"
        assert any("Docstring for fallback" in t for t in texts), \
            "developer docstring shown via partial match"

    def test_help_renders_flags_section(self, repl_env):
        """/help <cmd> shows a 'Flags:' section for commands that declare flags."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="flagcmd", args="<name>", help="Has flags.",
            handler=lambda ctx, args: None,
            flags={"--table": "Use 256-entry lookup table."},
        ))

        # Act
        engine.dispatch("help flagcmd")

        # Assert
        texts = [t for t, _ in output]
        assert any("Flags:" in t for t in texts), "Flags header rendered"
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

        # Assert — one Flags: row containing both names, not two separate rows.
        texts = [t for t, _ in output]
        combo_lines = [t for t in texts if "--verbose" in t and "-v" in t]
        assert combo_lines, "alias listed alongside canonical on one line"
        # Description must not appear twice (once per alias).
        desc_count = sum(1 for t in texts if "Verbose mode." in t)
        assert desc_count == 1, f"description printed once, got {desc_count}"

    def test_help_fuzzy_finds_flag_name(self, repl_env):
        """/help <needle> matches declared flag names."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="boringcmd", args="", help="Does nothing interesting.",
            handler=lambda ctx, args: None,
            flags={"--nitro": "Go faster."},
        ))

        # Act — needle only appears in a flag name
        result = engine.dispatch("help nitro")

        # Assert — boringcmd surfaces via its flag
        assert result.success, "fuzzy match via flag name succeeds"
        texts = [t for t, _ in output]
        assert any("boringcmd" in t for t in texts), "command surfaced from flag"

    def test_help_fuzzy_finds_flag_description(self, repl_env):
        """/help <needle> matches words inside a flag's description."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="rocketcmd", args="", help="Launch something.",
            handler=lambda ctx, args: None,
            flags={"--fuel": "Use 256-entry combustion booster."},
        ))

        # Act — needle only appears in the flag description
        result = engine.dispatch("help combustion")

        # Assert
        assert result.success, "fuzzy match via flag description succeeds"
        texts = [t for t, _ in output]
        assert any("rocketcmd" in t for t in texts), "command surfaced from description"

    def test_help_search_returns_value_for_scripting(self, repl_env):
        """CmdResult.value is the newline-joined matching command names."""
        # Arrange
        engine, _, _, output = repl_env
        from termapy.plugins import PluginInfo
        engine.register_plugin(PluginInfo(
            name="alphacmd", args="", help="zzz unique-token-xyz zzz.",
            handler=lambda ctx, args: None,
        ))

        # Act
        result = engine.dispatch("help.search unique-token-xyz")

        # Assert
        actual = result.value
        expected = "alphacmd"
        assert actual == expected, f"{actual} == {expected}"


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
        engine.dispatch("grep zzzznotfound")
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
        # Arrange — scrollback contains prior grep output and echoed command
        engine, _, _, output = repl_env
        text = (
            "real error line\n"
            "  grep: 'error' — 1 match(es)\n"
            "  grep:     1 | real error line\n"
            "> /grep error"
        )
        self._set_screen_text(engine, text)

        # Act
        engine.dispatch("grep error")

        # Assert — only the real line matches, grep noise is skipped
        texts = [t for t, _ in output]
        assert any("1 match(es)" in t for t in texts), "only 1 match"
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 1, "grep output and echoed cmd excluded"

    def test_grep_bad_regex(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep [invalid")
        assert output[-1][1] == "red", "error shown in red"
        assert "invalid pattern" in output[-1][0], "error message shown"

    def test_grep_max_output_default(self, repl_env):
        # Arrange — create text with 150 matching lines, no max_grep_lines in cfg
        engine, _, _, output = repl_env
        lines = [f"match line {i}" for i in range(150)]
        self._set_screen_text(engine, "\n".join(lines))

        # Act
        engine.dispatch("grep match")

        # Assert — default cap is 100
        texts = [t for t, _ in output]
        assert any("first 100 of 150" in t for t in texts)  # assert cap message
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 100  # assert only 100 lines output

    def test_grep_max_output_from_config(self, repl_env):
        # Arrange — set custom max_grep_lines
        engine, cfg, _, output = repl_env
        cfg["max_grep_lines"] = 5
        lines = [f"match line {i}" for i in range(20)]
        self._set_screen_text(engine, "\n".join(lines))

        # Act
        engine.dispatch("grep match")

        # Assert — cap uses config value
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
    """Test cap.py keyword extraction — pure function, no serial needed."""

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

        # Act — should not crash
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

        # Assert — no "Cancelled" message, script_stop not called
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
    def test_cfg_no_args_shows_all(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg")

        # Assert — should list config keys
        texts = [t for t, _ in output]
        assert any("port" in t for t in texts)  # shows port key
        assert any("baud_rate" in t or "115200" in t for t in texts)

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

        # Assert — should output something about the config
        assert len(output) > 0  # produced some output

    def test_cfg_dump(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env

        # Act
        engine.dispatch("cfg.dump")

        # Assert — should dump JSON
        texts = [t for t, _ in output]
        assert any("port" in t for t in texts)  # JSON includes port


# -- /repeat ----------------------------------------------------------------


class TestRepeat:
    def test_missing_count(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat cmd=AT")

        # Assert — error about missing count
        actual = [t for t, _ in output]
        assert any("count is required" in t for t in actual), f"expected 'count is required' error, got: {actual}"

    def test_missing_cmd(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=3")

        # Assert — error about missing cmd
        actual = [t for t, _ in output]
        assert any("Usage:" in t for t in actual), f"expected 'Usage:' error, got: {actual}"

    def test_count_not_integer(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=abc cmd=AT")

        # Assert — error about non-integer count
        actual = [t for t, _ in output]
        assert any("integer" in t for t in actual), f"expected 'integer' error, got: {actual}"

    def test_count_zero(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=0 cmd=AT")

        # Assert — error about count > 0
        actual = [t for t, _ in output]
        assert any("> 0" in t for t in actual), f"expected '> 0' error, got: {actual}"

    def test_count_negative(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=-1 cmd=AT")

        # Assert — error about count > 0
        actual = [t for t, _ in output]
        assert any("> 0" in t for t in actual), f"expected '> 0' error, got: {actual}"

    def test_invalid_delay(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("repeat count=1 delay=bogus cmd=AT")

        # Assert — error about invalid delay
        actual = [t for t, _ in output]
        assert any("duration" in t.lower() or "invalid" in t.lower() for t in actual), f"expected duration/invalid error, got: {actual}"

    def test_dispatches_n_times(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        dispatched = []
        engine.ctx.dispatch = lambda cmd: dispatched.append(cmd)

        # Act
        engine.dispatch("repeat count=3 cmd=AT+TEMP")

        # Assert — command dispatched 3 times
        assert dispatched == ["AT+TEMP", "AT+TEMP", "AT+TEMP"], f"expected 3 dispatches, got: {dispatched}"

    def test_sets_iteration_variable(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        seen_values = []
        engine.ctx.dispatch = lambda cmd: seen_values.append(_VARS.get("REPEAT_N"))

        # Act
        engine.dispatch("repeat count=3 cmd=AT")

        # Assert — variable was 1, 2, 3 during iterations
        assert seen_values == ["1", "2", "3"], f"expected iteration values ['1','2','3'], got: {seen_values}"

    def test_custom_variable_name(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        seen_values = []
        engine.ctx.dispatch = lambda cmd: seen_values.append(_VARS.get("I"))

        # Act
        engine.dispatch("repeat count=2 var=I cmd=AT")

        # Assert — custom variable name used
        assert seen_values == ["1", "2"], f"expected var=I values ['1','2'], got: {seen_values}"

    def test_variable_cleaned_up(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env
        engine.ctx.dispatch = lambda cmd: None

        # Act
        engine.dispatch("repeat count=2 cmd=AT")

        # Assert — variable removed after repeat
        assert "REPEAT_N" not in _VARS, f"REPEAT_N should be cleaned up, but _VARS contains: {_VARS}"

    def test_variable_cleaned_up_on_error(self, repl_env):
        # Arrange
        from termapy.builtins.plugins.var import _VARS

        engine, _, _, output = repl_env

        def boom(cmd):
            raise RuntimeError("dispatch error")

        engine.ctx.dispatch = boom

        # Act — handler should not crash the engine
        engine.dispatch("repeat count=2 cmd=AT")

        # Assert — variable cleaned up even on error
        assert "REPEAT_N" not in _VARS, f"REPEAT_N should be cleaned up after error, but _VARS contains: {_VARS}"

    def test_completes_silently(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine.ctx.dispatch = lambda cmd: None

        # Act
        engine.dispatch("repeat count=5 cmd=AT")

        # Assert — no output from repeat itself (commands produce their own output)
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

        # Assert — stopped after 2 iterations, not all 10
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

        # Assert — stopped during delay, not blocked for 10s * 99
        assert len(dispatched) == 1, f"expected 1 dispatch before stop, got: {len(dispatched)}"
        actual = [t for t, _ in output]
        assert any("1/100" in t for t in actual), f"expected '1/100' in stop message, got: {actual}"
