"""Tests for built-in REPL commands dispatched through ReplEngine."""

import json

import pytest

from termapy.plugins import EngineAPI, PluginContext
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
        get_echo=lambda: engine._echo,
        set_echo=lambda val: setattr(engine, "_echo", val),
        get_seq_counters=lambda: engine._seq_counters,
        set_seq_counters=lambda val: setattr(engine, "_seq_counters", val),
        reset_seq=engine._reset_seq,
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
    )
    engine.set_context(ctx)
    return engine, cfg, config_path, output


# -- /echo ----------------------------------------------------------------


class TestEcho:
    def test_echo_on(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._echo = False

        # Act
        engine.dispatch("echo on")

        # Assert
        assert engine._echo is True  # echo enabled
        assert any("on" in t for t, _ in output)  # confirmation shown

    def test_echo_off(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("echo off")

        # Assert
        assert engine._echo is False  # echo disabled
        assert any("off" in t for t, _ in output)  # confirmation shown

    def test_echo_toggle(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        assert engine._echo is True  # starts enabled

        # Act / Assert — toggle off
        engine.dispatch("echo")
        assert engine._echo is False  # toggled off

        # Act / Assert — toggle on
        engine.dispatch("echo")
        assert engine._echo is True  # toggled back on


# -- /print ---------------------------------------------------------------


class TestPrint:
    def test_print_text(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print Hello, world!")
        assert ("Hello, world!", None) in output  # text printed verbatim

    def test_print_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("print")
        assert ("", None) in output  # empty string printed


# -- /seq -----------------------------------------------------------------


class TestSeq:
    def test_seq_show_empty(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("seq")
        assert any("No counters" in t for t, _ in output)  # empty state message

    def test_seq_show_with_counters(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 3, 2: 7}

        # Act
        engine.dispatch("seq")

        # Assert
        assert any("seq1=3" in t for t, _ in output)  # counter 1 shown
        assert any("seq2=7" in t for t, _ in output)  # counter 2 shown

    def test_seq_reset(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._seq_counters = {1: 5}

        # Act
        engine.dispatch("seq.reset")

        # Assert
        assert engine._seq_counters == {}  # counters cleared
        assert any("reset" in t.lower() for t, _ in output)  # confirmation shown


# -- /stop ----------------------------------------------------------------


class TestStop:
    def test_stop_no_script(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("stop")
        assert any("No script" in t for t, _ in output)  # no-op message

    def test_stop_with_script(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        engine._script_depth = 1

        # Act
        engine.dispatch("stop")

        # Assert
        assert engine._script_stop.is_set()  # stop event set
        assert any("Stopping" in t for t, _ in output)  # confirmation shown


# -- /help ----------------------------------------------------------------


class TestHelp:
    def test_help_lists_commands(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help")

        # Assert
        texts = [t for t, _ in output]
        assert any("help" in t for t in texts)  # help command listed
        assert any("cfg" in t for t in texts)  # cfg command listed

    def test_help_specific_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help echo")

        # Assert
        texts = [t for t, _ in output]
        assert any("echo" in t.lower() for t in texts)  # echo help shown

    def test_help_unknown_command(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("help nonexistent")

        # Assert
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red

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
        assert any("Line one." in t for t in texts)  # first long_help line
        assert any("Line two." in t for t in texts)  # second long_help line

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
        assert any("dev docstring" in t for t in texts)  # docstring content shown
        assert any("developer docstring" in t for t in texts)  # header shown

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
        assert any("[bold]Summary line here.[/]" in t for t in markup_lines)  # bold summary

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
        assert any("[bold]Args:[/]" in t for t in markup_lines)  # Args header bold
        assert any("[bold]Returns:[/]" in t for t in markup_lines)  # Returns header bold

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
        assert any("[bold]ctx:[/] Plugin context" in t for t in markup_lines)  # ctx: bold
        assert any("[bold]args:[/] Command arguments" in t for t in markup_lines)  # args: bold

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
        assert any("no docstring" in t for t in texts)  # no-doc message shown

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
        assert any("Handler docstring" in t for t in texts)  # docstring shown
        assert not any("long help" in t.lower() for t in texts)  # LONG_HELP not shown

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
        assert actual == expected


# -- /show ----------------------------------------------------------------


class TestShow:
    def test_show_cfg(self, repl_env):
        # Arrange
        engine, cfg, config_path, output = repl_env

        # Act
        engine.dispatch("show.cfg")

        # Assert
        texts = [t for t, _ in output]
        assert any("COM4" in t for t in texts)  # config value shown
        assert any("end" in t for t in texts)  # end marker shown

    def test_show_file(self, repl_env, tmp_path):
        # Arrange
        engine, _, _, output = repl_env
        test_file = tmp_path / "test.txt"
        test_file.write_text("line one\nline two", encoding="utf-8")

        # Act
        engine.dispatch(f"show {test_file}")

        # Assert
        texts = [t for t, _ in output]
        assert any("line one" in t for t in texts)  # first line shown
        assert any("line two" in t for t in texts)  # second line shown

    def test_show_missing_file(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env

        # Act
        engine.dispatch("show /nonexistent/file.txt")

        # Assert
        assert any("not found" in t.lower() for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red

    def test_show_no_args(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show")
        assert any("Usage" in t for t, _ in output)  # usage message

    def test_show_nonexistent_name(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("show $bogus")
        assert any("not found" in t.lower() for t, _ in output)  # treated as filename


# -- /os ------------------------------------------------------------------


class TestOs:
    def test_os_disabled(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("os echo hi")
        assert any("disabled" in t for t, _ in output)  # blocked by default

    def test_os_enabled(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os echo hello_from_os")

        # Assert
        texts = [t for t, _ in output]
        assert any("hello_from_os" in t for t in texts)  # shell output captured

    def test_os_no_args(self, repl_env):
        # Arrange
        engine, cfg, _, output = repl_env
        cfg["os_cmd_enabled"] = True

        # Act
        engine.dispatch("os")

        # Assert
        assert any("Usage" in t for t, _ in output)  # usage message


# -- dispatch edge cases ---------------------------------------------------


class TestDispatch:
    def test_unknown_command(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("totally_unknown_cmd")
        assert any("Unknown" in t for t, _ in output)  # error message
        assert output[-1][1] == "red"  # shown in red

    def test_empty_dispatch_shows_help(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("")
        texts = [t for t, _ in output]
        assert any("help" in t.lower() for t in texts)  # empty triggers help

    def test_command_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        engine.dispatch("ECHO off")
        assert engine._echo is False  # uppercase command works


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
        assert output[-1][1] == "red"  # assert usage shown in red
        assert "Usage" in output[-1][0]

    def test_grep_matches(self, repl_env):
        # Arrange
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)

        # Act
        engine.dispatch("grep error")

        # Assert
        texts = [t for t, _ in output]
        assert any("2 match(es)" in t for t in texts)  # assert match count
        assert any("2 |" in t and "ERROR" in t for t in texts)  # assert line 2
        assert any("5 |" in t and "error" in t for t in texts)  # assert line 5

    def test_grep_no_matches(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep zzzznotfound")
        texts = [t for t, _ in output]
        assert any("no matches" in t for t in texts)  # assert no matches message

    def test_grep_case_insensitive(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep ERROR")
        texts = [t for t, _ in output]
        assert any("2 match(es)" in t for t in texts)  # assert both cases matched

    def test_grep_regex(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep error|warning")
        texts = [t for t, _ in output]
        assert any("3 match(es)" in t for t in texts)  # assert regex alternation works

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
        assert any("1 match(es)" in t for t in texts)  # assert only 1 match
        grep_lines = [t for t in texts if "grep:" in t and "|" in t]
        assert len(grep_lines) == 1  # assert grep output and echoed cmd excluded

    def test_grep_bad_regex(self, repl_env):
        engine, _, _, output = repl_env
        self._set_screen_text(engine, _SCREEN_TEXT)
        engine.dispatch("grep [invalid")
        assert output[-1][1] == "red"  # assert error shown in red
        assert "invalid pattern" in output[-1][0]  # assert error message

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
