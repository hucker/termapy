"""Tests for the output-level dial: levels, channel gating, suffix/flag dispatch."""

from __future__ import annotations

import json

import pytest

from termapy.plugins import (
    DEFAULT_OUTPUT_LEVEL,
    LEVEL_FLAGS,
    OUTPUT_LEVELS,
    CmdResult,
    Command,
    PluginContext,
    PluginInfo,
    parse_output_level,
)
from termapy.repl import ReplEngine, _strip_level_flags

# ── parse_output_level / constants ────────────────────────────────────────────


class TestParseOutputLevel:
    def test_canonical_names(self):
        for name in OUTPUT_LEVELS:
            actual = parse_output_level(name)
            assert actual == name, "canonical name passes through"

    def test_case_insensitive(self):
        actual = parse_output_level("VERBOSE")
        expected = "verbose"
        assert actual == expected, "uppercase normalized"

    def test_unknown_returns_none(self):
        actual = parse_output_level("loud")
        assert actual is None, "unknown level returns None"

    def test_default_is_normal(self):
        actual = DEFAULT_OUTPUT_LEVEL
        expected = "normal"
        assert actual == expected, "default level is normal"

    def test_levels_are_ordered(self):
        # The tuple order encodes rank: silent < quiet < normal < verbose.
        actual = OUTPUT_LEVELS
        expected = ("silent", "quiet", "normal", "verbose")
        assert actual == expected, "levels listed quietest-to-loudest"


# ── ctx channel gating ────────────────────────────────────────────────────────


@pytest.fixture
def ctx_with_writes():
    """PluginContext that captures writes."""
    from termapy.plugins import IOHandle
    writes: list[tuple[str, str | None]] = []
    ctx = PluginContext(io=IOHandle(_write=lambda text, color=None: writes.append((text, color))))
    return ctx, writes


@pytest.fixture
def ctx_with_markup_writes():
    """PluginContext that captures both _write and _write_markup, tagged."""
    from termapy.plugins import IOHandle
    writes: list[tuple[str, str, str | None]] = []
    ctx = PluginContext(io=IOHandle(
        _write=lambda text, color=None: writes.append(("text", text, color)),
        _write_markup=lambda text: writes.append(("markup", text, None)),
    ))
    return ctx, writes


class TestChannelGating:
    """Each level shows the right channels."""

    @pytest.mark.parametrize(
        "level,result_shown,output_shown,status_shown",
        [
            ("silent", False, False, False),
            ("quiet", True, False, False),
            ("normal", True, True, False),
            ("verbose", True, True, True),
        ],
    )
    def test_channels_for_level(self, ctx_with_writes, level, result_shown,
                                 output_shown, status_shown):
        # Arrange
        ctx, writes = ctx_with_writes
        ctx.ns("flags")["output_level"] = level

        # Act
        ctx.io.result("ANSWER")
        ctx.io.output("DATA")
        ctx.io.status("CHATTER")

        # Assert
        texts = [t for t, _ in writes]
        actual_result = "ANSWER" in texts
        actual_output = "DATA" in texts
        actual_status = "CHATTER" in texts
        assert actual_result == result_shown, f"result at {level}"
        assert actual_output == output_shown, f"output at {level}"
        assert actual_status == status_shown, f"status at {level}"

    def test_call_level_overrides_global(self, ctx_with_writes):
        # Arrange
        ctx, writes = ctx_with_writes
        ctx.ns("flags")["output_level"] = "normal"
        ctx._call_level = "verbose"

        # Act
        ctx.io.status("CHATTER")

        # Assert -- per-call override wins.
        texts = [t for t, _ in writes]
        assert "CHATTER" in texts, "verbose override shows status"

    def test_default_when_unset(self, ctx_with_writes):
        # Arrange -- no flag, no override; falls back to DEFAULT_OUTPUT_LEVEL.
        ctx, writes = ctx_with_writes

        # Act
        actual = ctx.output_level

        # Assert
        expected = DEFAULT_OUTPUT_LEVEL
        assert actual == expected, "ctx.output_level falls back to default"


class TestMarkupChannelGating:
    """The markup-rendering variants gate at the same ranks as the plain ones."""

    @pytest.mark.parametrize(
        "level,result_shown,output_shown,status_shown",
        [
            ("silent",  False, False, False),
            ("quiet",   True,  False, False),
            ("normal",  True,  True,  False),
            ("verbose", True,  True,  True),
        ],
    )
    def test_markup_channels_for_level(
        self, ctx_with_markup_writes, level,
        result_shown, output_shown, status_shown,
    ):
        # Arrange
        ctx, writes = ctx_with_markup_writes
        ctx.ns("flags")["output_level"] = level

        # Act
        ctx.io.result_markup("[green]ANSWER[/]")
        ctx.io.output_markup("[dim]DATA[/]")
        ctx.io.status_markup("[yellow]CHATTER[/]")

        # Assert -- markup variant entries are tagged "markup"; assert by text.
        texts = [t for kind, t, _ in writes if kind == "markup"]
        actual_result = "[green]ANSWER[/]" in texts
        actual_output = "[dim]DATA[/]" in texts
        actual_status = "[yellow]CHATTER[/]" in texts
        assert actual_result == result_shown, (
            f"result_markup at {level}: expected {result_shown}, got {actual_result}"
        )
        assert actual_output == output_shown, (
            f"output_markup at {level}: expected {output_shown}, got {actual_output}"
        )
        assert actual_status == status_shown, (
            f"status_markup at {level}: expected {status_shown}, got {actual_status}"
        )

    def test_markup_channels_route_through_write_markup(self, ctx_with_markup_writes):
        # Arrange -- normal level so output_markup fires.
        ctx, writes = ctx_with_markup_writes
        ctx.ns("flags")["output_level"] = "normal"

        # Act
        ctx.io.output_markup("[red]X[/]")

        # Assert -- the markup callback should have fired, not the plain one.
        kinds = [k for k, _, _ in writes]
        assert "markup" in kinds, "output_markup routes through _write_markup"
        assert "text" not in kinds, "output_markup does NOT use _write"


# ── _strip_level_flags ────────────────────────────────────────────────────────


class TestStripLevelFlags:
    def test_strips_known_flag(self):
        actual_args, actual_level = _strip_level_flags("foo --silent bar")
        expected_args = "foo bar"
        expected_level = "silent"
        assert actual_args == expected_args, "flag removed from args"
        assert actual_level == expected_level, "level captured"

    def test_no_flag_returns_none(self):
        _, actual_level = _strip_level_flags("foo bar")
        assert actual_level is None, "no level flag means no level"

    def test_empty_args(self):
        actual_args, actual_level = _strip_level_flags("")
        assert actual_args == "", "empty args round-trip"
        assert actual_level is None, "no level for empty args"

    def test_all_levels_recognized(self):
        for flag, level in LEVEL_FLAGS.items():
            _, actual = _strip_level_flags(f"cmd {flag}")
            assert actual == level, f"{flag} maps to {level}"

    def test_last_wins_on_duplicate(self):
        _, actual = _strip_level_flags("cmd --quiet --verbose")
        expected = "verbose"
        assert actual == expected, "later flag wins on duplicate"


# ── ReplEngine: suffix and flag dispatch ──────────────────────────────────────


@pytest.fixture
def eng_with_test_command(tmp_path):
    """Engine with a test command that calls all three channels."""
    cfg = {"port": "COM4", "baud_rate": 115200, "eol": "\r"}
    config_path = tmp_path / "sub" / "test.cfg"
    config_path.parent.mkdir()
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    writes: list[tuple[str, str | None]] = []
    eng = ReplEngine(
        cfg, str(config_path), lambda t, c=None: writes.append((t, c))
    )
    eng.ctx.ns("flags")["output_level"] = "normal"  # default

    def _handler(ctx: PluginContext, args: str) -> CmdResult:
        ctx.io.result("R")
        ctx.io.output("O")
        ctx.io.status("S")
        return CmdResult.ok(value="VALUE")

    cmd = Command(name="probe", help="test", handler=_handler)
    eng._plugins["probe"] = PluginInfo(
        name="probe",
        args="",
        help="test",
        handler=_handler,
        flags={},
        needs=cmd.needs,
    )
    return eng, writes


class TestLevelSuffixDispatch:
    """`/cmd.<level>` overrides the level for that one call."""

    def test_quiet_suffix_shows_only_result(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act
        eng.dispatch("probe.quiet")

        # Assert
        texts = [t for t, _ in writes]
        assert "R" in texts, "result shown at quiet"
        assert "O" not in texts, "output suppressed at quiet"
        assert "S" not in texts, "status suppressed at quiet"

    def test_silent_suffix_shows_nothing(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act
        result = eng.dispatch("probe.silent")

        # Assert
        actual_count = len(writes)
        assert actual_count == 0, "silent suppresses every channel"
        # Value still flows through CmdResult for scripting.
        assert result.value == "VALUE", "silent preserves CmdResult.value"

    def test_verbose_suffix_shows_status(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act
        eng.dispatch("probe.verbose")

        # Assert
        texts = [t for t, _ in writes]
        assert "S" in texts, "status shown at verbose"

    def test_global_unaffected_by_suffix(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command
        eng.dispatch("probe.silent")

        # Act -- a follow-up call without suffix uses global (normal).
        writes.clear()
        eng.dispatch("probe")

        # Assert
        texts = [t for t, _ in writes]
        assert "R" in texts, "follow-up call back to normal"
        assert "O" in texts, "follow-up call back to normal"


class TestLevelFlagDispatch:
    """`/cmd --<level>` overrides the level for that one call."""

    def test_quiet_flag(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act
        eng.dispatch("probe --quiet")

        # Assert
        texts = [t for t, _ in writes]
        assert "R" in texts, "result shown at quiet flag"
        assert "O" not in texts, "output suppressed at quiet flag"

    def test_silent_flag(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act
        eng.dispatch("probe --silent")

        # Assert
        actual = len(writes)
        expected = 0
        assert actual == expected, "silent flag suppresses every channel"

    def test_conflicting_suffix_and_flag_errors(self, eng_with_test_command):
        # Arrange
        eng, writes = eng_with_test_command

        # Act -- .quiet suffix + --silent flag disagree.
        result = eng.dispatch("probe.quiet --silent")

        # Assert
        assert not result.success, "conflicting suffix+flag fails"
        actual = result.error or ""
        assert "Conflicting" in actual, "error message names the conflict"


# ── Builtin handlers respect output levels end-to-end ─────────────────────────


@pytest.fixture
def real_engine(tmp_path):
    """ReplEngine -- builtins are loaded automatically by its __init__."""
    cfg = {
        "port": "DEMO", "baud_rate": 115200, "eol": "\r",
        "echo": False,
    }
    config_path = tmp_path / "test.cfg"
    config_path.write_text(json.dumps(cfg))
    for sub in ("plugin", "ss", "run", "cap"):
        (config_path.parent / sub).mkdir(exist_ok=True)
    writes: list[tuple[str, str | None]] = []
    markup_writes: list[str] = []
    eng = ReplEngine(cfg, str(config_path), lambda t, c=None: writes.append((t, c)))
    # Wire markup writer too so output_markup calls land somewhere we can see.
    eng.ctx.io._write_markup = lambda text: markup_writes.append(text)
    return eng, writes, markup_writes


class TestBuiltinsRespectLevels:
    """End-to-end: real builtins gated correctly across the four levels."""

    def test_help_silent_emits_nothing(self, real_engine):
        # Arrange
        eng, writes, markup = real_engine

        # Act
        eng.dispatch("help.silent")

        # Assert
        assert writes == [] and markup == [], (
            f"/help.silent must produce no scrollback (got {len(writes)} text + "
            f"{len(markup)} markup writes)"
        )

    def test_help_normal_emits_markup(self, real_engine):
        # Arrange
        eng, writes, markup = real_engine

        # Act
        eng.dispatch("help")

        # Assert -- help routes through output_markup; should land here.
        assert len(markup) > 0, "/help at normal emits help text via output_markup"

    def test_help_verbose_emits_more_than_normal(self, real_engine):
        # Arrange / Act / Assert
        eng, writes, markup = real_engine
        eng.dispatch("help")
        normal_count = len(markup)
        markup.clear()
        eng.dispatch("help.verbose")
        verbose_count = len(markup)
        assert verbose_count >= normal_count, (
            f"verbose emits at least as much as normal (verbose={verbose_count}, "
            f"normal={normal_count})"
        )

    def test_print_silent_emits_nothing(self, real_engine):
        # Arrange
        eng, writes, markup = real_engine

        # Act
        eng.dispatch("print Hello --silent")

        # Assert
        texts_with_payload = [t for t, _ in writes if t == "Hello"]
        assert texts_with_payload == [], "/print.silent emits no payload"

    def test_print_normal_emits_text(self, real_engine):
        # Arrange
        eng, writes, markup = real_engine

        # Act
        eng.dispatch("print Hello")

        # Assert
        texts = [t for t, _ in writes]
        assert "Hello" in texts, "/print at normal emits the payload"
