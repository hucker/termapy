"""Tests for termapy.scripting - pure functions, no serial or Textual needed."""

import json
import re

import pytest

from termapy.plugins import CmdResult
from termapy.scripting import (
    coerce_to_type,
    expand_template,
    format_duration,
    parse_bool,
    parse_count_arg,
    parse_duration,
    parse_duration_ms,
    parse_keywords,
    resolve_seq_filename,
    select_lines,
)


# ── coerce_to_type ───────────────────────────────────────────────


class TestCoerceToType:
    def test_bool_true_values(self):
        for val in ("true", "1", "yes", "on", "True", "YES"):
            actual = coerce_to_type(val, False)
            assert actual is True, "all truthy strings coerce to True"

    def test_bool_false_values(self):
        for val in ("false", "0", "no", "off", "False", "NO"):
            actual = coerce_to_type(val, True)
            assert actual is False, "all falsy strings coerce to False"

    def test_bool_invalid(self):
        with pytest.raises(ValueError):  # non-bool string raises
            coerce_to_type("maybe", True)

    def test_int(self):
        actual = coerce_to_type("42", 0)
        assert actual == 42, "string coerced to int"
        assert isinstance(actual, int), "type preserved as int"

    def test_int_invalid(self):
        with pytest.raises(ValueError):  # non-numeric string raises
            coerce_to_type("abc", 0)

    def test_float(self):
        actual = coerce_to_type("3.14", 0.0)
        assert actual == 3.14, "string coerced to float"
        assert isinstance(actual, float), "type preserved as float"

    def test_string(self):
        actual = coerce_to_type("hello", "default")
        assert actual == "hello", "string passes through unchanged"


# ── expand_template ──────────────────────────────────────────────


class TestExpandTemplate:
    def test_no_placeholders_passthrough(self):
        actual, actual_counters = expand_template("plain_name", {})
        assert actual == "plain_name", "text unchanged"
        assert actual_counters == {}, "no counters created"

    def test_seq_increment_from_zero(self):
        actual, actual_counters = expand_template("t_{seq1+}", {})
        assert actual == "t_1", "counter starts at 0, incremented to 1"
        assert actual_counters[1] == 1, "counter stored"

    def test_seq_increment_from_existing(self):
        actual, actual_counters = expand_template("t_{seq1+}", {1: 5})
        assert actual == "t_6", "5 + 1 = 6"
        assert actual_counters[1] == 6, "counter updated"

    def test_seq_read_without_increment(self):
        actual, actual_counters = expand_template("t_{seq1}", {1: 5})
        assert actual == "t_5", "reads current value"
        assert actual_counters[1] == 5, "counter unchanged"

    def test_seq_read_unset_counter(self):
        actual, _ = expand_template("t_{seq1}", {})
        assert actual == "t_0", "unset counter defaults to 0"

    def test_multiple_increments_same_level(self):
        actual, actual_counters = expand_template("{seq1+}_{seq1+}", {})
        assert actual == "1_2", "incremented twice"
        assert actual_counters[1] == 2, "final counter value"

    def test_cascade_reset(self):
        """Incrementing seq2 resets seq1 to 0."""
        actual, actual_counters = expand_template("t_{seq2+}_{seq1+}", {1: 3, 2: 1})
        assert actual == "t_2_1", "seq2 incremented, seq1 reset then incremented"
        assert actual_counters[1] == 1, "seq1 was reset to 0, then incremented"
        assert actual_counters[2] == 2, "seq2 incremented from 1"

    def test_cascade_three_levels(self):
        """Incrementing seq3 resets seq1 and seq2."""
        # Arrange
        counters = {1: 5, 2: 3, 3: 1}

        # Act
        actual, actual_counters = expand_template("{seq3+}_{seq2}_{seq1}", counters)

        # Assert
        assert actual == "2_0_0", "seq3 incremented, lower levels reset"
        assert actual_counters[1] == 0, "seq1 reset"
        assert actual_counters[2] == 0, "seq2 reset"
        assert actual_counters[3] == 2, "seq3 incremented"

    def test_read_after_cascade(self):
        """After cascade reset, reading lower counter returns 0."""
        _, c = expand_template("{seq2+}", {1: 5, 2: 0})
        actual, _ = expand_template("{seq2}_{seq1+}", c)
        assert actual == "1_1", "seq2 reads 1, seq1 increments from 0"

    def test_does_not_mutate_input(self):
        # Arrange
        original = {1: 3}

        # Act
        _, actual_new = expand_template("t_{seq1+}", original)

        # Assert
        assert original[1] == 3, "original dict unchanged"
        assert actual_new[1] == 4, "new dict has incremented value"

    def test_datetime_placeholder(self):
        actual, _ = expand_template("t_{datetime}", {})
        assert re.match(r"t_\d{8}_\d{6}$", actual), "YYYYmmdd_HHMMSS format"

    def test_mixed_seq_and_datetime(self):
        actual, actual_counters = expand_template("{seq1+}_{datetime}", {})
        parts = actual.split("_", 1)
        assert parts[0] == "1", "seq counter value"
        assert re.match(r"\d{8}_\d{6}$", parts[1]), "datetime portion"

    def test_starttime_placeholder(self):
        actual, _ = expand_template("t_{starttime}", {}, start_time="20260305_100000")
        assert actual == "t_20260305_100000", "start time substituted"

    def test_starttime_default_empty(self):
        actual, _ = expand_template("t_{starttime}", {})
        assert actual == "t_", "empty start time = empty string"

    def test_starttime_with_seq(self):
        actual, actual_counters = expand_template(
            "{starttime}_{seq1+}", {}, start_time="20260305_100000"
        )
        assert actual == "20260305_100000_1", "both placeholders expanded"
        assert actual_counters[1] == 1, "counter incremented"

    def test_hierarchical_script_sequence(self):
        """Simulate a typical script: two sections with two steps each."""
        c: dict[int, int] = {}

        # Section 1, step 1
        actual, c = expand_template("t_{seq2+}_{seq1+}", c)
        assert actual == "t_1_1", "first section, first step"

        # Section 1, step 2
        actual, c = expand_template("t_{seq2}_{seq1+}", c)
        assert actual == "t_1_2", "first section, second step"

        # Section 2, step 1 (seq2+ resets seq1)
        actual, c = expand_template("t_{seq2+}_{seq1+}", c)
        assert actual == "t_2_1", "second section resets step counter"

        # Section 2, step 2
        actual, c = expand_template("t_{seq2}_{seq1+}", c)
        assert actual == "t_2_2", "second section, second step"


# ── parse_duration ───────────────────────────────────────────────


class TestParseDuration:
    def test_milliseconds(self):
        assert parse_duration("500ms") == 0.5, "500ms = 0.5s"

    def test_seconds(self):
        assert parse_duration("1s") == 1.0, "1s = 1.0s"

    def test_fractional_seconds(self):
        assert parse_duration("1.5s") == 1.5, "fractional seconds"

    def test_fractional_milliseconds(self):
        assert parse_duration("0.5ms") == 0.0005, "fractional ms"

    def test_whitespace(self):
        assert parse_duration("  500ms  ") == 0.5, "whitespace stripped"

    def test_invalid_no_unit(self):
        with pytest.raises(ValueError):  # unit required
            parse_duration("500")

    def test_microseconds(self):
        assert parse_duration("500us") == 0.0005, "500us = 0.5ms"

    def test_invalid_bad_unit(self):
        with pytest.raises(ValueError):  # unknown unit rejected
            parse_duration("500ns")

    def test_invalid_text(self):
        with pytest.raises(ValueError):  # non-numeric rejected
            parse_duration("fast")

    def test_empty(self):
        with pytest.raises(ValueError):  # empty string rejected
            parse_duration("")

    def test_bare_zero(self):
        # The one unitless value the user-facing grammar accepts: 0 is zero
        # regardless of unit, so `timeout=0` / `/delay 0` work without a unit.
        assert parse_duration("0") == 0.0, "bare 0 = 0 seconds"

    def test_bare_zero_fractional(self):
        assert parse_duration("0.0") == 0.0, "0.0 = 0 seconds"

    def test_default_unit_reads_bare_number(self):
        # Protocol/config grammar: a unitless number is read in default_unit.
        assert parse_duration("50", default_unit="ms") == 0.05, "bare 50 = 50ms"

    def test_default_unit_yields_to_explicit_unit(self):
        actual = parse_duration("2s", default_unit="ms")
        assert actual == 2.0, "an explicit unit overrides default_unit"

    def test_default_unit_still_rejects_non_numeric(self):
        with pytest.raises(ValueError):  # a bad token is invalid in any mode
            parse_duration("fast", default_unit="ms")


# ── parse_duration_ms ────────────────────────────────────────────


class TestParseDurationMs:
    def test_bare_is_milliseconds(self):
        assert parse_duration_ms("50") == 50, "bare number = ms"

    def test_seconds_unit(self):
        assert parse_duration_ms("1.5s") == 1500, "1.5s = 1500ms"

    def test_milliseconds_unit(self):
        assert parse_duration_ms("500ms") == 500, "500ms = 500ms"

    def test_microseconds_round_to_whole_ms(self):
        assert parse_duration_ms("2000us") == 2, "2000us = 2ms"

    def test_bare_zero(self):
        assert parse_duration_ms("0") == 0, "bare 0 = 0ms"


# ── format_duration ──────────────────────────────────────────────


class TestFormatDuration:
    def test_microseconds(self):
        assert format_duration(0.00048) == "480us", "sub-ms renders as us"

    def test_milliseconds(self):
        assert format_duration(0.025) == "25ms", "sub-second renders as whole ms"

    def test_seconds_two_decimals(self):
        assert format_duration(1.5) == "1.50s", ">= 1s renders as 2dp seconds"

    def test_boundary_one_millisecond(self):
        assert format_duration(0.001) == "1ms", "exactly 1ms crosses into the ms tier"

    def test_boundary_one_second(self):
        assert format_duration(1.0) == "1.00s", "exactly 1s crosses into the s tier"

    def test_zero(self):
        assert format_duration(0.0) == "0us", "zero renders as 0us"


# ── resolve_seq_filename ────────────────────────────────────────


class TestResolveSeqFilename:
    def test_no_pattern_passthrough(self, tmp_path):
        actual = resolve_seq_filename("plain.txt", tmp_path)
        assert actual == "plain.txt", "no $(n...) = unchanged"

    def test_first_call_starts_at_zero(self, tmp_path):
        actual = resolve_seq_filename("data_$(n000).txt", tmp_path)
        assert actual == "data_000.txt", "first number is 000"

    def test_increments_on_each_call(self, tmp_path):
        # Act
        first = resolve_seq_filename("data_$(n000).txt", tmp_path)
        second = resolve_seq_filename("data_$(n000).txt", tmp_path)
        third = resolve_seq_filename("data_$(n000).txt", tmp_path)

        # Assert
        assert first == "data_000.txt", "starts at 0"
        assert second == "data_001.txt", "increments"
        assert third == "data_002.txt", "increments again"

    def test_counter_persists_in_file(self, tmp_path):
        # Arrange
        resolve_seq_filename("data_$(n000).txt", tmp_path)
        resolve_seq_filename("data_$(n000).txt", tmp_path)

        # Act - read counter file directly
        seq_path = tmp_path / ".cap_seq"
        counters = json.loads(seq_path.read_text(encoding="utf-8"))

        # Assert
        assert counters["data_$(n000).txt"] == 1, "last used = 1"

    def test_rollover(self, tmp_path):
        # Arrange - set counter to max
        seq_path = tmp_path / ".cap_seq"
        seq_path.write_text(
            json.dumps({"data_$(n0).txt": 9}), encoding="utf-8"
        )

        # Act
        actual = resolve_seq_filename("data_$(n0).txt", tmp_path)

        # Assert
        assert actual == "data_0.txt", "rolls over from 9 to 0"

    def test_width_1(self, tmp_path):
        actual = resolve_seq_filename("f_$(n0).txt", tmp_path)
        assert actual == "f_0.txt", "1-digit sequence"

    def test_width_2(self, tmp_path):
        actual = resolve_seq_filename("f_$(n00).txt", tmp_path)
        assert actual == "f_00.txt", "2-digit zero-padded"

    def test_width_3(self, tmp_path):
        actual = resolve_seq_filename("f_$(n000).txt", tmp_path)
        assert actual == "f_000.txt", "3-digit zero-padded"

    def test_width_4_raises(self, tmp_path):
        with pytest.raises(ValueError, match="too wide"):
            resolve_seq_filename("f_$(n0000).txt", tmp_path)

    def test_independent_patterns(self, tmp_path):
        # Act
        a1 = resolve_seq_filename("alpha_$(n00).txt", tmp_path)
        b1 = resolve_seq_filename("beta_$(n00).txt", tmp_path)
        a2 = resolve_seq_filename("alpha_$(n00).txt", tmp_path)

        # Assert
        assert a1 == "alpha_00.txt", "alpha starts at 0"
        assert b1 == "beta_00.txt", "beta starts at 0 independently"
        assert a2 == "alpha_01.txt", "alpha increments independently"

    def test_corrupt_counter_file_resets(self, tmp_path):
        # Arrange
        seq_path = tmp_path / ".cap_seq"
        seq_path.write_text("not json!", encoding="utf-8")

        # Act
        actual = resolve_seq_filename("f_$(n000).txt", tmp_path)

        # Assert
        assert actual == "f_000.txt", "resets to 0 on corrupt file"

    def test_missing_directory_created(self, tmp_path):
        # Arrange
        subdir = tmp_path / "sub" / "dir"

        # Act
        actual = resolve_seq_filename("f_$(n00).txt", subdir)

        # Assert
        assert actual == "f_00.txt", "works"
        assert subdir.exists(), "directory created"


# ── parse_keywords ──────────────────────────────────────────────


class TestParseKeywords:
    def test_basic(self):
        # Act
        actual = parse_keywords("timeout=2s match=OK", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert actual["timeout"] == "2s", "keyword extracted"
        assert actual["match"] == "OK", "rest keyword extracted"

    def test_rest_keyword_consumes_to_eol(self):
        # Act
        actual = parse_keywords(
            "timeout=2s match=hello world", {"timeout", "match"}, rest_keyword="match"
        )

        # Assert
        assert actual["match"] == "hello world", "everything after match="
        assert actual["timeout"] == "2s", "keyword before rest"

    def test_spaces_around_equals(self):
        # Act
        actual = parse_keywords(
            "timeout = 2s match = OK", {"timeout", "match"}, rest_keyword="match"
        )

        # Assert
        assert actual["timeout"] == "2s", "normalized despite spaces"
        assert actual["match"] == "OK", "normalized despite spaces"

    def test_no_keywords_gives_positional(self):
        # Act
        actual = parse_keywords("just positional", {"timeout"})

        # Assert
        assert actual["_positional"] == "just positional", "no keywords matched"

    def test_unknown_keyword_goes_to_positional(self):
        # Act
        actual = parse_keywords("foo=bar match=OK", {"match"}, rest_keyword="match")

        # Assert
        assert actual["match"] == "OK", "recognized keyword"
        assert actual["_positional"] == "foo=bar", "unrecognized \u2192 positional"

    def test_missing_rest_keyword(self):
        # Act
        actual = parse_keywords("timeout=2s", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert "match" not in actual, "rest keyword absent"
        assert actual["timeout"] == "2s", "other keyword present"

    def test_case_insensitive(self):
        # Act
        actual = parse_keywords("Timeout=2s Match=OK", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert actual["timeout"] == "2s", "case-insensitive match"
        assert actual["match"] == "OK", "case-insensitive match"

    def test_only_rest_keyword(self):
        # Act
        actual = parse_keywords("match=device is ready", {"match"}, rest_keyword="match")

        # Assert
        assert actual["match"] == "device is ready", "rest keyword only"

    def test_empty_string(self):
        # Act
        actual = parse_keywords("", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert "match" not in actual, "nothing parsed"
        assert "timeout" not in actual, "nothing parsed"

    def test_no_rest_keyword_specified(self):
        # Act
        actual = parse_keywords("timeout=2s quiet=on", {"timeout", "quiet"})

        # Assert
        assert actual["timeout"] == "2s", "keyword extracted"
        assert actual["quiet"] == "on", "keyword extracted"


# ── CmdResult ───────────────────────────────────────────────────


class TestCmdResult:
    def test_ok(self):
        actual = CmdResult.ok(value="")
        assert actual.success is True, "ok() returns success"
        assert actual.error == "", "no error on success"
        assert actual.elapsed_s == 0.0, "no timing yet"

    def test_fail_with_message(self):
        actual = CmdResult.fail(msg="bad input")
        assert actual.success is False, "failure"
        assert actual.error == "bad input", "error message"

    def test_fail_no_message(self):
        actual = CmdResult.fail()
        assert actual.success is False, "failure"
        assert actual.error == "", "no message"

    def test_default_is_success(self):
        actual = CmdResult()
        assert actual.success is True, "default = success"

    def test_elapsed_mutable(self):
        # Arrange
        actual = CmdResult.ok(value="")

        # Act
        actual.elapsed_s = 0.123

        # Assert
        assert actual.elapsed_s == 0.123, "dispatch sets elapsed_s post-handler"

    def test_value_required_keyword(self):
        # ``value`` is keyword-only with no default so callers can't
        # silently forget it.  Positional or missing args must error.
        import pytest as _pytest
        with _pytest.raises(TypeError):
            CmdResult.ok()  # type: ignore[call-arg]

    def test_value_empty_explicit(self):
        actual = CmdResult.ok(value="")
        assert actual.value == "", "explicit empty is preserved"

    def test_value_set(self):
        actual = CmdResult(value="0.35.0")
        assert actual.value == "0.35.0", "value captured"

    def test_path_auto_resolved(self):
        # Path values auto-convert to absolute strings via the
        # constructor; callers can hand a Path directly.
        from pathlib import Path
        p = Path("some/relative/path")
        actual = CmdResult.ok(value=p)
        assert isinstance(actual.value, str), "Path converted to string"
        assert Path(actual.value).is_absolute(), "value is an absolute path"


# ── parse_bool ──────────────────────────────────────────────────────────────


class TestParseBool:
    @pytest.mark.parametrize("val", ["on", "1", "true", "yes", "y", "t"])
    def test_truthy_lowercase(self, val):
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is True, f"expected True for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["off", "0", "false", "no", "n", "f"])
    def test_falsy_lowercase(self, val):
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is False, f"expected False for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["ON", "On", "TRUE", "True", "Yes", "Y", "T"])
    def test_truthy_mixed_case(self, val):
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is True, f"expected True for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["OFF", "Off", "FALSE", "False", "No", "N", "F"])
    def test_falsy_mixed_case(self, val):
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is False, f"expected False for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["  on  ", "\toff\n", " true "])
    def test_whitespace_stripped(self, val):
        # Act
        actual = parse_bool(val)

        # Assert - value should be recognized despite surrounding whitespace
        assert actual is not None, f"expected bool for {val!r}, got None"

    @pytest.mark.parametrize("val", ["", "maybe", "2", "high", "low", "enable", "x"])
    def test_unrecognized_returns_none(self, val):
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is None, f"expected None for {val!r}, got {actual!r}"


# ── select_lines (shared count scheme) ───────────────────────────


class TestSelectLines:
    """The +N=last / -N=first / None=all line-selection scheme."""

    LINES = ["a", "b", "c", "d", "e"]

    def test_none_returns_all(self):
        # Act
        actual = select_lines(self.LINES, None)

        # Assert
        assert actual == self.LINES, "no count -> every line, unchanged"

    def test_positive_returns_last_n(self):
        # Act -- positive N is the most-recent N (tail)
        actual = select_lines(self.LINES, 2)

        # Assert
        expected = ["d", "e"]
        assert actual == expected, "+2 -> last 2 lines (most recent)"

    def test_negative_returns_first_n(self):
        # Act -- negative N is the oldest N (head)
        actual = select_lines(self.LINES, -2)

        # Assert
        expected = ["a", "b"]
        assert actual == expected, "-2 -> first 2 lines (oldest)"

    def test_positive_overshoot_clamps_to_all(self):
        # Act
        actual = select_lines(self.LINES, 999)

        # Assert
        assert actual == self.LINES, "|N| >= count clamps to all (tail end)"

    def test_negative_overshoot_clamps_to_all(self):
        # Act
        actual = select_lines(self.LINES, -999)

        # Assert
        assert actual == self.LINES, "|N| >= count clamps to all (head end)"

    def test_single_line_buffer(self):
        # Act
        actual_last = select_lines(["only"], 1)
        actual_first = select_lines(["only"], -1)

        # Assert
        assert actual_last == ["only"], "+1 on single line -> that line"
        assert actual_first == ["only"], "-1 on single line -> that line"

    def test_empty_buffer(self):
        # Act / Assert -- never raises, always empty
        assert select_lines([], 5) == [], "+N on empty -> empty"
        assert select_lines([], -5) == [], "-N on empty -> empty"
        assert select_lines([], None) == [], "None on empty -> empty"


# ── parse_count_arg (name + optional signed count) ───────────────


class TestParseCountArg:
    """Splitting a `name`/`count` arg string for /ss.txt."""

    def test_empty_uses_default_name_and_no_count(self):
        # Act
        actual = parse_count_arg("", "screenshot")

        # Assert
        expected = ("screenshot", None)
        assert actual == expected, "no args -> default name, no count"

    def test_bare_positive_count(self):
        # Act
        actual = parse_count_arg("50", "screenshot")

        # Assert
        expected = ("screenshot", 50)
        assert actual == expected, "bare int -> count, default name"

    def test_bare_negative_count(self):
        # Act
        actual = parse_count_arg("-50", "screenshot")

        # Assert
        expected = ("screenshot", -50)
        assert actual == expected, "leading minus parsed as negative count"

    def test_name_only(self):
        # Act
        actual = parse_count_arg("mycap", "screenshot")

        # Assert
        expected = ("mycap", None)
        assert actual == expected, "non-int token -> name, no count"

    @pytest.mark.parametrize("args", ["mycap 5", "5 mycap"])
    def test_name_and_count_order_independent(self, args):
        # Act
        actual = parse_count_arg(args, "screenshot")

        # Assert
        expected = ("mycap", 5)
        assert actual == expected, f"{args!r} -> (name, count) regardless of order"

    def test_zero_count_rejected(self):
        # Act / Assert
        with pytest.raises(ValueError, match="Invalid line count: 0"):
            parse_count_arg("0", "screenshot")

    def test_two_int_tokens_rejected(self):
        # Act / Assert
        with pytest.raises(ValueError, match="Usage"):
            parse_count_arg("10 20", "screenshot")
