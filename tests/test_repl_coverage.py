"""Additional tests for repl.py - covering suggest_command, edit_distance,
ScriptCtx, directive dispatch paths, and script special commands."""

from __future__ import annotations

import json
import time
from io import StringIO
from unittest.mock import MagicMock

import pytest

from termapy.plugins import CmdResult, DirectiveInfo, DirectiveResult
from termapy.repl import ReplEngine, ScriptCtx, _edit_distance, _suggest_command

# -- Fixtures ----------------------------------------------------------------


@pytest.fixture
def engine(tmp_path):
    """Create a ReplEngine with temp config and capture output."""
    cfg = {"port": "COM4", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "proto", "cap", "prof"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    output: list[tuple] = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: output.append((t, c)))
    flags = eng.ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex"] = False
    # Bare engines default to CONFINED caps (no host); these script tests
    # model the OPERATOR, so grant filesystem_unconfined like a real host
    # (both the ctx field and the fs handle hold the CapabilitySet).
    from termapy.plugins import CapabilitySet
    caps = CapabilitySet(filesystem_unconfined=True)
    eng.ctx.capabilities = caps
    eng.ctx.fs.capabilities = caps

    return eng, output


# -- _edit_distance ----------------------------------------------------------


class TestEditDistance:
    def test_identical_strings(self):
        """Equal strings are zero edits apart, so an exact name always ranks first."""
        # Act
        actual = _edit_distance("hello", "hello")

        # Assert
        assert actual == 0, "identical strings have distance 0"

    def test_single_insertion(self):
        """One added character costs exactly one edit."""
        # Act
        actual = _edit_distance("cat", "cats")

        # Assert
        assert actual == 1, "one insertion is distance 1"

    def test_single_deletion(self):
        """One dropped character costs exactly one edit."""
        # Act
        actual = _edit_distance("cats", "cat")

        # Assert
        assert actual == 1, "one deletion is distance 1"

    def test_substitution(self):
        """One replaced character costs exactly one edit."""
        # Act
        actual = _edit_distance("cat", "car")

        # Assert
        assert actual == 1, "one substitution is distance 1"

    def test_transposition(self):
        """Swapped adjacent letters count as one edit, not two -- the common typo."""
        # Act
        actual = _edit_distance("ab", "ba")

        # Assert
        assert actual == 1, "transposition counts as 1 edit"

    def test_empty_strings(self):
        """Two empty strings are zero edits apart rather than an error."""
        # Act
        actual = _edit_distance("", "")

        # Assert
        assert actual == 0, "empty strings have distance 0"

    def test_one_empty(self):
        """Distance to an empty string is the other string's full length."""
        # Act
        actual = _edit_distance("abc", "")

        # Assert
        assert actual == 3, "distance to empty is length of other string"

    def test_completely_different(self):
        """No shared characters costs one substitution per position."""
        # Act
        actual = _edit_distance("abc", "xyz")

        # Assert
        assert actual == 3, "all characters different"


# -- _suggest_command --------------------------------------------------------


class TestSuggestCommand:
    def test_close_match(self):
        """A one-letter typo suggests the intended command with its prefix."""
        # Arrange
        plugins = {"help": None, "echo": None, "exit": None}

        # Act
        actual = _suggest_command("halp", plugins, "/")

        # Assert
        assert actual is not None, "finds close match"
        assert "/help" in actual, "suggests /help for halp"

    def test_no_match(self):
        """A name far from every command yields no suggestion at all."""
        # Arrange
        plugins = {"help": None, "echo": None}

        # Act
        actual = _suggest_command("zzzzzzz", plugins, "/")

        # Assert
        assert actual is None, "no match for distant string"

    def test_exact_match_distance_zero(self):
        """An exact command name is itself offered as a suggestion."""
        # Arrange
        plugins = {"help": None, "echo": None}

        # Act
        actual = _suggest_command("help", plugins, "/")

        # Assert
        assert actual is not None, "exact match found"
        assert "/help" in actual, "includes exact match"

    def test_multiple_suggestions(self):
        """When several commands tie at distance 1, the suggestion includes them."""
        # Arrange
        plugins = {"cap": None, "car": None, "cat": None, "xyz": None}

        # Act
        actual = _suggest_command("cab", plugins, "/")

        # Assert
        assert actual is not None, "finds multiple close matches"
        # All three (cap, car, cat) are distance 1 from cab
        assert "/cap" in actual, "includes cap"


# -- ScriptCtx.record -------------------------------------------------------


class TestScriptCtxRecord:
    def test_verbose_output(self):
        """Verbose mode prints one line per step with [step/total] and the command."""
        # Arrange
        output: list[str] = []
        sctx = ScriptCtx(
            w=lambda t, c=None: output.append(t),
            dispatch_fn=None,
            prefix="/",
            profile=False,
            verbose=True,
            progress=None,
            on_nest=None,
        )
        sctx.step = 1
        sctx.total = 5

        # Act
        sctx.record("AT+RST", 0.123)

        # Assert
        assert len(output) == 1, "one verbose line printed"
        assert "[1/5]" in output[0], "includes step/total"
        assert "AT+RST" in output[0], "includes command label"

    def test_profile_writes_csv(self):
        """Profile mode keeps the step timing and appends a CSV row to the file."""
        # Arrange
        buf = StringIO()
        sctx = ScriptCtx(
            w=lambda t, c=None: None,
            dispatch_fn=None,
            prefix="/",
            profile=True,
            verbose=False,
            progress=None,
            on_nest=None,
        )
        sctx.prof_fh = buf  # ty: ignore[invalid-assignment]
        sctx.step = 1
        sctx.total = 1

        # Act
        sctx.record("AT", 0.042)

        # Assert
        assert len(sctx.profile_times) == 1, "timing recorded"
        assert "AT" in buf.getvalue(), "CSV line written"

    def test_sub_millisecond_formatting(self):
        """Sub-millisecond step times render as whole microseconds, not 0ms."""
        # Arrange
        output: list[str] = []
        sctx = ScriptCtx(
            w=lambda t, c=None: output.append(t),
            dispatch_fn=None,
            prefix="/",
            profile=False,
            verbose=True,
            progress=None,
            on_nest=None,
        )
        sctx.step = 1
        sctx.total = 1

        # Act
        sctx.record("fast", 0.0005)

        # Assert
        assert "500us" in output[0], "sub-ms renders as whole microseconds"


# -- ScriptCtx.finish -------------------------------------------------------


class TestScriptCtxFinish:
    def test_verbose_shows_summary(self):
        """Verbose finish prints one done line naming the script."""
        # Arrange
        output: list[str] = []
        sctx = ScriptCtx(
            w=lambda t, c=None: output.append(t),
            dispatch_fn=None,
            prefix="/",
            profile=False,
            verbose=True,
            progress=None,
            on_nest=None,
        )
        sctx.script_t0 = time.perf_counter() - 1.5

        # Act
        sctx.finish("test.run")

        # Assert
        assert len(output) == 1, "one summary line"
        assert "test.run" in output[0], "includes script name"
        assert "done" in output[0], "includes 'done'"

    def test_profile_prints_csv_contents(self, tmp_path):
        """Profile finish echoes the CSV rows plus a total line to the output."""
        # Arrange
        output: list[str] = []
        prof_path = tmp_path / "prof" / "test.csv"
        prof_path.parent.mkdir()
        prof_path.write_text("0.100000,AT\n0.200000,OK\n")
        sctx = ScriptCtx(
            w=lambda t, c=None: output.append(t),
            dispatch_fn=None,
            prefix="/",
            profile=True,
            verbose=False,
            progress=None,
            on_nest=None,
        )
        sctx.prof_fh = MagicMock()
        sctx.prof_path = prof_path
        sctx.prof_name = "test.csv"
        sctx.profile_times = [("AT", 0.1), ("OK", 0.2)]
        sctx.script_t0 = time.perf_counter()

        # Act
        sctx.finish("test.run")

        # Assert
        assert any("0.100000,AT" in line for line in output), "CSV content printed"
        assert any("total" in line for line in output), "total line printed"


# -- dispatch: unknown command with suggestion --------------------------------


class TestDispatchSuggestion:
    def test_unknown_command_suggests(self, engine):
        """An unknown command's error carries a did-you-mean for a near miss."""
        # Arrange
        eng, output = engine
        eng.register_hook("help", "", "Show help.", lambda ctx, args: CmdResult.ok(value=""),
                          source="test")

        # Act
        result = eng.dispatch("helm")

        # Assert
        assert result.success is False, "unknown command fails"
        assert "did you mean" in result.error.lower(), "includes suggestion"
        assert "/help" in result.error, "suggests /help"

    def test_unknown_command_no_suggestion(self, engine):
        """With no near miss, the error is the plain Unknown command message."""
        # Arrange
        eng, output = engine

        # Act
        result = eng.dispatch("zzzzzzz")

        # Assert
        assert result.success is False, "unknown command fails"
        assert "Unknown command" in result.error, "generic unknown message"


# -- dispatch: handler returns None ------------------------------------------


class TestDispatchNoneResult:
    def test_none_result_treated_as_ok(self, engine):
        """A handler returning None counts as success, not a dispatch failure."""
        # Arrange
        eng, output = engine
        eng.register_hook("noop", "", "Returns None.",
                          lambda ctx, args: None, source="test")

        # Act
        result = eng.dispatch("noop")

        # Assert
        assert result.success is True, "None treated as CmdResult.ok()"


# -- dispatch: handler raises exception --------------------------------------


class TestDispatchException:
    def test_handler_exception_caught(self, engine):
        """A raising handler becomes a failed result carrying the exception text."""
        # Arrange
        eng, output = engine

        def bad_handler(ctx, args):
            raise ValueError("boom")

        eng.register_hook("boom", "", "Raises.", bad_handler, source="test")

        # Act
        result = eng.dispatch("boom")

        # Assert
        assert result.success is False, "exception caught"
        assert "boom" in result.error, "error message includes exception text"


# -- dispatch_full: directive paths ------------------------------------------


class TestDispatchDirectives:
    def test_rewrite_directive(self, engine):
        """A rewrite directive swaps the line for another command and runs it."""
        # Arrange
        eng, output = engine
        eng.register_hook("ping", "", "Ping.",
                          lambda ctx, args: CmdResult.ok(value="pong"),
                          source="test")

        eng._directives = [
            DirectiveInfo(
                name="test_rewrite", help="test",
                handler=lambda cmd: DirectiveResult("rewrite", "ping")
                if cmd == "trigger" else None,
            )
        ]

        # Act
        result = eng.dispatch_full(
            "trigger",
            log=lambda d, t: None,
            echo_markup=lambda t: None,
            status=lambda t, c="": None,
            serial_write=lambda d: None,
            is_connected=lambda: False,
            eol_label=lambda le: "",
        )

        # Assert
        assert result.success is True, "rewrite dispatches to new command"
        assert result.value == "pong", "rewritten command executes"

    def test_warn_directive(self, engine):
        """A warn directive shows its message on status and lets dispatch succeed."""
        # Arrange
        eng, output = engine
        status_msgs: list[str] = []
        eng._directives = [
            DirectiveInfo(
                name="test_warn", help="test",
                handler=lambda cmd: DirectiveResult("warn", "watch out"),
            )
        ]

        # Act
        result = eng.dispatch_full(
            "anything",
            log=lambda d, t: None,
            echo_markup=lambda t: None,
            status=lambda t, c="": status_msgs.append(t),
            serial_write=lambda d: None,
            is_connected=lambda: False,
            eol_label=lambda le: "",
        )

        # Assert
        assert result.success is True, "warn directive returns ok"
        assert any("watch out" in status_msg for status_msg in status_msgs), "warning shown"

    def test_error_directive(self, engine):
        """An error directive fails the line with the directive's own message."""
        # Arrange
        eng, output = engine
        eng._directives = [
            DirectiveInfo(
                name="test_error", help="test",
                handler=lambda cmd: DirectiveResult("error", "bad input"),
            )
        ]

        # Act
        result = eng.dispatch_full(
            "anything",
            log=lambda d, t: None,
            echo_markup=lambda t: None,
            status=lambda t, c="": None,
            serial_write=lambda d: None,
            is_connected=lambda: False,
            eol_label=lambda le: "",
        )

        # Assert
        assert result.success is False, "error directive fails"
        assert "bad input" in result.error, "error message passed through"


class TestDispatchBarePrefix:
    """Regression: a lone prefix character (``/``) must not crash dispatch.

    Pre-fix: ``repl_cmd.split()[0]`` raised ``IndexError: list index out
    of range`` because the bare prefix strips to an empty string, and
    ``"".split()`` yields ``[]``.  Discovered 2026-05-23 when a user
    pressed Enter with just ``/`` in the REPL.
    """

    def test_bare_prefix_does_not_crash(self, engine):
        """A lone prefix returns a result instead of raising IndexError."""
        # Arrange
        eng, output = engine

        # Act -- the trigger that crashed before.  Should land on the
        # "unknown command" path (empty command name) without raising.
        result = eng.dispatch_full(
            "/",
            log=lambda d, t: None,
            echo_markup=lambda t: None,
            status=lambda t, c="": None,
            serial_write=lambda d: None,
            is_connected=lambda: False,
            eol_label=lambda le: "",
        )

        # Assert -- we don't care what the result is (success or fail);
        # the contract is "do not crash".  Any returned CmdResult means
        # the dispatcher completed normally.
        assert result is not None, (
            "dispatch of bare prefix returned a CmdResult instead of "
            "raising IndexError"
        )


# -- run_script with profile -------------------------------------------------


class TestRunScriptProfile:
    def test_profile_creates_csv(self, engine, tmp_path):
        """A profiled script run leaves a timing CSV in the config's prof/ folder."""
        # Arrange
        eng, output = engine
        script = tmp_path / "sub" / "run" / "test.run"
        script.write_text("/delay 10ms\n")
        eng.register_hook("delay", "<dur>", "Wait.",
                          lambda ctx, args: CmdResult.ok(value=""), source="test")

        # Act
        path, result = eng.start_script(str(script))
        if path:
            eng.run_script(
                path,
                write=lambda t, c=None: None,
                dispatch=lambda cmd: eng.dispatch(cmd[1:]) if cmd.startswith("/") else CmdResult.ok(value=""),
                profile=True,
                verbose=True,
            )

        # Assert
        prof_dir = tmp_path / "sub" / "prof"
        prof_files = list(prof_dir.glob("*.csv"))
        assert len(prof_files) >= 1, "profile CSV created"


# -- replace_cfg -------------------------------------------------------------


class TestReplaceCfg:
    def test_replaces_config(self, engine):
        """replace_cfg swaps both the cfg dict and the config path the engine holds."""
        # Arrange
        eng, output = engine
        new_cfg = {"port": "COM5", "baud_rate": 9600}

        # Act
        eng.replace_cfg(new_cfg, "/new/path.cfg")

        # Assert
        assert eng.cfg["port"] == "COM5", "cfg updated"
        assert eng.config_path == "/new/path.cfg", "path updated"
