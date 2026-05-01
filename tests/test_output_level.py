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
    writes: list[tuple[str, str | None]] = []
    ctx = PluginContext(write=lambda text, color=None: writes.append((text, color)))
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
        ctx.result("ANSWER")
        ctx.output("DATA")
        ctx.status("CHATTER")

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
        ctx.status("CHATTER")

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
    cfg = {"port": "COM4", "baud_rate": 115200, "line_ending": "\r"}
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
        ctx.result("R")
        ctx.output("O")
        ctx.status("S")
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
