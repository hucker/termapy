"""Tests for termapy.scripting - pure functions, no serial or Textual needed."""

import json
import re
import time
from datetime import datetime

import pytest

from termapy.plugins import CmdResult
from termapy.scripting import (
    SETTING_QUERY,
    SETTING_TOGGLE,
    coerce_to_type,
    expand_template,
    filename_timestamp,
    format_age,
    format_duration,
    format_size,
    format_timestamp,
    next_in_cycle,
    parse_bool,
    parse_bool_setting,
    parse_count_arg,
    parse_duration,
    parse_duration_ms,
    parse_keywords,
    render_progress_bar,
    resolve_seq_filename,
    select_lines,
    strip_leading_echo,
)

# ── strip_leading_echo ───────────────────────────────────────────


class TestStripLeadingEcho:
    def test_strips_matching_first_line(self):
        """A device echo of the sent command is dropped, leaving only the answer."""
        actual = strip_leading_echo("AT+INFO\nBassomatic v77", "AT+INFO")
        assert actual == "Bassomatic v77", "echoed command line removed"

    def test_no_strip_when_first_line_differs(self):
        """A device that does not echo keeps its full response untouched."""
        actual = strip_leading_echo("OK\nmore", "AT+INFO")
        assert actual == "OK\nmore", "unchanged when device did not echo"

    def test_whitespace_insensitive_match(self):
        """Padding around the echoed line does not stop it being recognized and stripped."""
        actual = strip_leading_echo("  AT+INFO  \nanswer", "AT+INFO")
        assert actual == "answer", "match ignores surrounding whitespace"

    def test_only_echo_line_yields_empty(self):
        """A response that is nothing but the echo collapses to an empty string."""
        actual = strip_leading_echo("AT+INFO", "AT+INFO")
        assert actual == "", "a response that is only the echo becomes empty"

    def test_strips_first_line_only(self):
        """Only the leading echo goes; a later identical line is real output and stays."""
        actual = strip_leading_echo("AT\nAT\nOK", "AT")
        assert actual == "AT\nOK", "only the single leading echo line is removed"


# ── coerce_to_type ───────────────────────────────────────────────


class TestCoerceToType:
    def test_bool_true_values(self):
        """Truthy words in any case coerce to True when the default is a bool."""
        for val in ("true", "1", "yes", "on", "True", "YES"):
            actual = coerce_to_type(val, False)
            assert actual is True, "all truthy strings coerce to True"

    def test_bool_false_values(self):
        """Falsy words in any case coerce to False when the default is a bool."""
        for val in ("false", "0", "no", "off", "False", "NO"):
            actual = coerce_to_type(val, True)
            assert actual is False, "all falsy strings coerce to False"

    def test_bool_invalid(self):
        """A non-boolean word raises instead of silently becoming False."""
        with pytest.raises(ValueError):  # non-bool string raises
            coerce_to_type("maybe", True)

    def test_int(self):
        """An int default makes the string coerce to a real int, not a float."""
        actual = coerce_to_type("42", 0)
        assert actual == 42, "string coerced to int"
        assert isinstance(actual, int), "type preserved as int"

    def test_int_invalid(self):
        """Non-numeric text for an int setting raises ValueError."""
        with pytest.raises(ValueError):  # non-numeric string raises
            coerce_to_type("abc", 0)

    def test_float(self):
        """A float default makes the string coerce to a float."""
        actual = coerce_to_type("3.14", 0.0)
        assert actual == 3.14, "string coerced to float"
        assert isinstance(actual, float), "type preserved as float"

    def test_string(self):
        """A string default passes the value through untouched."""
        actual = coerce_to_type("hello", "default")
        assert actual == "hello", "string passes through unchanged"


# ── expand_template ──────────────────────────────────────────────


class TestExpandTemplate:
    def test_no_placeholders_passthrough(self):
        """Text with no placeholders comes back unchanged and creates no counters."""
        actual, actual_counters = expand_template("plain_name", {})
        assert actual == "plain_name", "text unchanged"
        assert actual_counters == {}, "no counters created"

    def test_seq_increment_from_zero(self):
        """{seq1+} on a fresh counter yields 1 and records it."""
        actual, actual_counters = expand_template("t_{seq1+}", {})
        assert actual == "t_1", "counter starts at 0, incremented to 1"
        assert actual_counters[1] == 1, "counter stored"

    def test_seq_increment_from_existing(self):
        """{seq1+} bumps a stored counter by one and stores the new value."""
        actual, actual_counters = expand_template("t_{seq1+}", {1: 5})
        assert actual == "t_6", "5 + 1 = 6"
        assert actual_counters[1] == 6, "counter updated"

    def test_seq_read_without_increment(self):
        """{seq1} without + reads the counter and leaves it as is."""
        actual, actual_counters = expand_template("t_{seq1}", {1: 5})
        assert actual == "t_5", "reads current value"
        assert actual_counters[1] == 5, "counter unchanged"

    def test_seq_read_unset_counter(self):
        """Reading a counter that was never set renders 0, not an error."""
        actual, _ = expand_template("t_{seq1}", {})
        assert actual == "t_0", "unset counter defaults to 0"

    def test_multiple_increments_same_level(self):
        """Each {seq1+} in one template bumps again, left to right."""
        actual, actual_counters = expand_template("{seq1+}_{seq1+}", {})
        assert actual == "1_2", "incremented twice"
        assert actual_counters[1] == 2, "final counter value"

    def test_cascade_reset(self):
        """Incrementing seq1 (the top level) resets seq2 to 0."""
        actual, actual_counters = expand_template("t_{seq1+}_{seq2+}", {1: 1, 2: 3})
        assert actual == "t_2_1", "seq1 incremented, seq2 reset then incremented"
        assert actual_counters[1] == 2, "seq1 incremented from 1"
        assert actual_counters[2] == 1, "seq2 was reset to 0, then incremented"

    def test_cascade_three_levels(self):
        """Incrementing seq1 resets the deeper seq2 and seq3."""
        # Arrange
        counters = {1: 1, 2: 3, 3: 5}

        # Act
        actual, actual_counters = expand_template("{seq1+}_{seq2}_{seq3}", counters)

        # Assert
        assert actual == "2_0_0", "seq1 incremented, deeper levels reset"
        assert actual_counters[1] == 2, "seq1 incremented"
        assert actual_counters[2] == 0, "seq2 reset"
        assert actual_counters[3] == 0, "seq3 reset"

    def test_read_after_cascade(self):
        """After a parent bump resets a deeper counter, reading it returns 0."""
        _, c = expand_template("{seq1+}", {1: 5, 2: 3})
        actual, _ = expand_template("{seq2}_{seq1}", c)
        assert actual == "0_6", "seq2 reads 0 (reset by seq1+), seq1 holds at 6"

    def test_does_not_mutate_input(self):
        """Expansion returns a new counter dict; the caller's dict is untouched."""
        # Arrange
        original = {1: 3}

        # Act
        _, actual_new = expand_template("t_{seq1+}", original)

        # Assert
        assert original[1] == 3, "original dict unchanged"
        assert actual_new[1] == 4, "new dict has incremented value"

    def test_starttime_placeholder(self):
        """{starttime} is replaced by the start_time string the caller passes."""
        actual, _ = expand_template("t_{starttime}", {}, start_time="20260305_100000")
        assert actual == "t_20260305_100000", "start time substituted"

    def test_starttime_default_empty(self):
        """{starttime} renders empty when no start time is given."""
        actual, _ = expand_template("t_{starttime}", {})
        assert actual == "t_", "empty start time = empty string"

    def test_starttime_with_seq(self):
        """{starttime} and {seq1+} expand together in one template."""
        actual, actual_counters = expand_template(
            "{starttime}_{seq1+}", {}, start_time="20260305_100000"
        )
        assert actual == "20260305_100000_1", "both placeholders expanded"
        assert actual_counters[1] == 1, "counter incremented"

    def test_elapsed_placeholder(self):
        """{elapsed} renders elapsed_s in format_duration's human form."""
        actual, _ = expand_template("t_{elapsed}", {}, elapsed_s=1.5)
        assert actual == "t_1.5s", "{elapsed} renders elapsed_s via format_duration"

    def test_elapsed_default_zero(self):
        """{elapsed} renders 0us when no elapsed time is supplied."""
        actual, _ = expand_template("t_{elapsed}", {})
        assert actual == "t_0us", "{elapsed} is 0 when no start reference is given"

    def test_hierarchical_script_sequence(self):
        """Simulate a typical script: two sections with two steps each.

        seq1 is the section (top level), seq2 the step (deeper level).
        """
        c: dict[int, int] = {}

        # Section 1, step 1
        actual, c = expand_template("t_{seq1+}_{seq2+}", c)
        assert actual == "t_1_1", "first section, first step"

        # Section 1, step 2
        actual, c = expand_template("t_{seq1}_{seq2+}", c)
        assert actual == "t_1_2", "first section, second step"

        # Section 2, step 1 (seq1+ resets the deeper seq2)
        actual, c = expand_template("t_{seq1+}_{seq2+}", c)
        assert actual == "t_2_1", "second section resets step counter"

        # Section 2, step 2
        actual, c = expand_template("t_{seq1}_{seq2+}", c)
        assert actual == "t_2_2", "second section, second step"


# ── parse_duration ───────────────────────────────────────────────


class TestParseDuration:
    def test_milliseconds(self):
        """The ms suffix scales to seconds."""
        assert parse_duration("500ms") == 0.5, "500ms = 0.5s"

    def test_seconds(self):
        """The s suffix is taken as seconds unchanged."""
        assert parse_duration("1s") == 1.0, "1s = 1.0s"

    def test_fractional_seconds(self):
        """Fractional seconds parse without truncation."""
        assert parse_duration("1.5s") == 1.5, "fractional seconds"

    def test_fractional_milliseconds(self):
        """Fractional milliseconds parse to a sub-millisecond value."""
        assert parse_duration("0.5ms") == 0.0005, "fractional ms"

    def test_whitespace(self):
        """Surrounding whitespace is ignored around a duration token."""
        assert parse_duration("  500ms  ") == 0.5, "whitespace stripped"

    def test_invalid_no_unit(self):
        """A nonzero number without a unit is rejected; the unit is mandatory."""
        with pytest.raises(ValueError):  # unit required
            parse_duration("500")

    def test_microseconds(self):
        """The us suffix scales to seconds."""
        assert parse_duration("500us") == 0.0005, "500us = 0.5ms"

    def test_invalid_bad_unit(self):
        """An unsupported unit such as ns is rejected rather than guessed."""
        with pytest.raises(ValueError):  # unknown unit rejected
            parse_duration("500ns")

    def test_invalid_text(self):
        """A word with no number is rejected."""
        with pytest.raises(ValueError):  # non-numeric rejected
            parse_duration("fast")

    def test_empty(self):
        """An empty string is rejected, not read as zero."""
        with pytest.raises(ValueError):  # empty string rejected
            parse_duration("")

    def test_bare_zero(self):
        """Bare 0 is the one unitless value allowed, so timeout=0 works."""
        # The one unitless value the user-facing grammar accepts: 0 is zero
        # regardless of unit, so `timeout=0` / `/delay 0` work without a unit.
        assert parse_duration("0") == 0.0, "bare 0 = 0 seconds"

    def test_bare_zero_fractional(self):
        """The unitless-zero exception also covers 0.0."""
        assert parse_duration("0.0") == 0.0, "0.0 = 0 seconds"

    def test_default_unit_reads_bare_number(self):
        """With default_unit, a bare number is read in that unit instead of rejected."""
        # Protocol/config grammar: a unitless number is read in default_unit.
        assert parse_duration("50", default_unit="ms") == 0.05, "bare 50 = 50ms"

    def test_default_unit_yields_to_explicit_unit(self):
        """An explicit unit wins over default_unit."""
        actual = parse_duration("2s", default_unit="ms")
        assert actual == 2.0, "an explicit unit overrides default_unit"

    def test_default_unit_still_rejects_non_numeric(self):
        """default_unit relaxes only the unit; non-numeric text still raises."""
        with pytest.raises(ValueError):  # a bad token is invalid in any mode
            parse_duration("fast", default_unit="ms")


# ── parse_duration_ms ────────────────────────────────────────────


class TestParseDurationMs:
    def test_bare_is_milliseconds(self):
        """In the ms reader a bare number means milliseconds."""
        assert parse_duration_ms("50") == 50, "bare number = ms"

    def test_seconds_unit(self):
        """An s suffix converts to whole milliseconds."""
        assert parse_duration_ms("1.5s") == 1500, "1.5s = 1500ms"

    def test_milliseconds_unit(self):
        """An ms suffix passes through as the same count."""
        assert parse_duration_ms("500ms") == 500, "500ms = 500ms"

    def test_microseconds_round_to_whole_ms(self):
        """A us value converts to an integer millisecond count."""
        assert parse_duration_ms("2000us") == 2, "2000us = 2ms"

    def test_bare_zero(self):
        """Bare 0 parses to 0 ms."""
        assert parse_duration_ms("0") == 0, "bare 0 = 0ms"


# ── format_duration ──────────────────────────────────────────────


class TestFormatDuration:
    def test_microseconds(self):
        """Durations under a millisecond render in us."""
        assert format_duration(0.00048) == "480us", "sub-ms renders as us"

    def test_milliseconds(self):
        """Durations under a second render as whole ms."""
        assert format_duration(0.025) == "25ms", "sub-second renders as whole ms"

    def test_seconds_three_significant(self):
        """Seconds use three significant figures with no trailing-zero padding."""
        assert format_duration(1.5) == "1.5s", ">= 1s renders to 3 significant figures"
        assert format_duration(2.64) == "2.64s", "no trailing-zero padding"

    def test_boundary_one_millisecond(self):
        """Exactly 1 ms lands in the ms tier, not us."""
        assert format_duration(0.001) == "1ms", "exactly 1ms crosses into the ms tier"

    def test_boundary_one_second(self):
        """Exactly 1 s lands in the s tier, not ms."""
        assert format_duration(1.0) == "1s", "exactly 1s crosses into the s tier"

    def test_zero(self):
        """Zero renders as 0us rather than an empty or odd string."""
        assert format_duration(0.0) == "0us", "zero renders as 0us"

    def test_rounding_up_to_tier_edge_never_uses_exponent(self):
        """A value rounding to 1000 at the tier edge is written out, never as 1e+03ms."""
        # 999.6 ms sits in the ms tier but rounds to 1000 at 3 significant
        # figures; it must be written out, not as 1e+03ms.  (The us tier
        # cannot hit this: frist resolves durations to whole microseconds.)
        assert format_duration(0.9996) == "1000ms"

    def test_under_a_minute_stays_seconds(self):
        """Tiers break on clock boundaries: 46 s stays seconds, not 0.77min."""
        # Clock boundaries, not humanize's 45 s: 46 s must not become 0.77min.
        assert format_duration(46) == "46s", "seconds tier runs to 60"

    def test_minutes_hours_days(self):
        """Past 60 s the scale steps to min, hr, then days, which is the top tier."""
        assert format_duration(90) == "1.5min", "60 s crosses into minutes"
        assert format_duration(7200) == "2hr", "60 min crosses into hours"
        assert format_duration(86400 * 3) == "3days", "24 hr crosses into days and stays there"


# ── format_size ──────────────────────────────────────────────────


class TestFormatSize:
    @pytest.mark.parametrize(
        "byte_count, expected",
        [
            (0, "0 B"),
            (512, "512 B"),
            (1023, "1023 B"),
            (1024, "1.0 KB"),
            (4300, "4.2 KB"),
            (48_000_000, "45.8 MB"),
            (1 << 40, "1.0 TB"),
            (1 << 50, "1.0 PB"),
        ],
    )
    def test_tiers(self, byte_count, expected):
        """Byte counts step through B/KB/MB/TB/PB at 1024 with one decimal above B."""
        assert format_size(byte_count) == expected


# ── format_age ───────────────────────────────────────────────────


class TestFormatAge:
    BASE = 1_700_000_000.0  # any fixed POSIX time; ``now`` is pinned relative to it

    @pytest.mark.parametrize(
        "delta_s, expected",
        [
            (10, "just now"),
            (44.9, "just now"),
            (-5, "just now"),  # mtime slightly in the future (clock skew) is not an error
            (45, "1 min ago"),
            (600, "10 min ago"),
            (3600 * 3, "3 hr ago"),
            (86400 * 1.4, "1 day ago"),
            (86400 * 3, "3 days ago"),
            (86400 * 40, "1 mo ago"),
            (86400 * 400, "1 yr ago"),
            (86400 * 800, "2 yr ago"),
        ],
    )
    def test_compact_wording(self, delta_s, expected):
        """Ages use compact wording per bucket; clock-skewed future times read just now."""
        actual = format_age(self.BASE, now=self.BASE + delta_s)
        assert actual == expected

    def test_defaults_to_current_time(self):
        """Omitting now= measures the age against the real clock."""
        assert format_age(time.time()) == "just now", "omitting now= measures against the clock"


# ── format_timestamp ─────────────────────────────────────────────


class TestFormatTimestamp:
    def test_millisecond_precision(self):
        """Timestamps render HH:MM:SS.mmm with microseconds truncated to ms."""
        dt = datetime(2026, 7, 5, 14, 30, 22, 123456)
        assert format_timestamp(dt) == "14:30:22.123", "HH:MM:SS.mmm, us truncated to ms"

    def test_defaults_to_now_shape(self):
        """With no argument the current time is rendered in the same shape."""
        actual = format_timestamp()
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", actual), (
            "no-arg call renders the current time as HH:MM:SS.mmm"
        )


# ── filename_timestamp ───────────────────────────────────────────


class TestFilenameTimestamp:
    def test_shape_is_filename_safe(self):
        """The stamp is YYYYmmdd_HHMMSS with no colons, so it is legal on Windows."""
        # Act
        actual = filename_timestamp()

        # Assert -- YYYYmmdd_HHMMSS, no colons (safe in a filename)
        assert re.fullmatch(r"\d{8}_\d{6}", actual), (
            "filename stamp is YYYYmmdd_HHMMSS with no separators"
        )
        assert ":" not in actual, "no colons (would be illegal on Windows)"


# ── render_progress_bar ──────────────────────────────────────────


class TestRenderProgressBar:
    def test_finished_state_is_full_bar(self):
        """elapsed == total draws a full bar, which the CLI done line relies on."""
        # Act -- elapsed >= total reports 100% (the CLI 'done' line relies on it)
        actual = render_progress_bar(10, 10, width=30)

        # Assert
        expected = "[" + "█" * 30 + "] 10s/10s"
        assert actual == expected, "elapsed==total renders a full 100% bar"

    def test_zero_total_is_full_bar(self):
        """A zero total draws a full bar instead of dividing by zero."""
        # Act -- guard against div-by-zero; an instantaneous delay is 'done'
        actual = render_progress_bar(0, 0, width=30)

        # Assert
        assert actual == "[" + "█" * 30 + "] 0s/0s", "0-total -> full bar"

    def test_midpoint_is_partial(self):
        """Halfway through, the bar mixes filled and empty cells with the right timing."""
        # Act
        actual = render_progress_bar(5, 10, width=30)

        # Assert -- moving, not finished: some full cells, some empty, right timing
        assert actual.startswith("[") and actual.endswith("] 5s/10s"), "framed + timed"
        assert "█" in actual, "has filled cells at the midpoint"
        assert " " in actual, "not yet 100% (has empty cells)"

    def test_ascii_only_uses_ascii_glyphs(self):
        """ascii_only swaps the Unicode block for # so no-color output stays ASCII."""
        # Act -- no-color mode swaps the Unicode blocks for ASCII
        actual = render_progress_bar(10, 10, width=30, ascii_only=True)

        # Assert
        assert actual == "[" + "#" * 30 + "] 10s/10s", "ascii full bar uses '#'"
        assert "█" not in actual, "no Unicode block in ASCII mode"


# ── resolve_seq_filename ────────────────────────────────────────


class TestResolveSeqFilename:
    def test_no_pattern_passthrough(self, tmp_path):
        """A name with no $(n...) pattern is returned unchanged."""
        actual = resolve_seq_filename("plain.txt", tmp_path)
        assert actual == "plain.txt", "no $(n...) = unchanged"

    def test_first_call_starts_at_zero(self, tmp_path):
        """The first use of a pattern yields sequence number 0."""
        actual = resolve_seq_filename("data_$(n000).txt", tmp_path)
        assert actual == "data_000.txt", "first number is 000"

    def test_increments_on_each_call(self, tmp_path):
        """Each call with the same pattern advances the number by one."""
        # Act
        first = resolve_seq_filename("data_$(n000).txt", tmp_path)
        second = resolve_seq_filename("data_$(n000).txt", tmp_path)
        third = resolve_seq_filename("data_$(n000).txt", tmp_path)

        # Assert
        assert first == "data_000.txt", "starts at 0"
        assert second == "data_001.txt", "increments"
        assert third == "data_002.txt", "increments again"

    def test_counter_persists_in_file(self, tmp_path):
        """The last-used number is saved in .cap_seq keyed by the raw pattern."""
        # Arrange
        resolve_seq_filename("data_$(n000).txt", tmp_path)
        resolve_seq_filename("data_$(n000).txt", tmp_path)

        # Act - read counter file directly
        seq_path = tmp_path / ".cap_seq"
        counters = json.loads(seq_path.read_text(encoding="utf-8"))

        # Assert
        assert counters["data_$(n000).txt"] == 1, "last used = 1"

    def test_rollover(self, tmp_path):
        """A counter at the pattern's maximum wraps back to 0."""
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
        """$(n0) yields a one-digit number."""
        actual = resolve_seq_filename("f_$(n0).txt", tmp_path)
        assert actual == "f_0.txt", "1-digit sequence"

    def test_width_2(self, tmp_path):
        """$(n00) yields a two-digit zero-padded number."""
        actual = resolve_seq_filename("f_$(n00).txt", tmp_path)
        assert actual == "f_00.txt", "2-digit zero-padded"

    def test_width_3(self, tmp_path):
        """$(n000) yields a three-digit zero-padded number."""
        actual = resolve_seq_filename("f_$(n000).txt", tmp_path)
        assert actual == "f_000.txt", "3-digit zero-padded"

    def test_width_4_raises(self, tmp_path):
        """Patterns wider than three digits are refused with a too wide error."""
        with pytest.raises(ValueError, match="too wide"):
            resolve_seq_filename("f_$(n0000).txt", tmp_path)

    def test_independent_patterns(self, tmp_path):
        """Each pattern keeps its own counter; one advancing never moves another."""
        # Act
        a1 = resolve_seq_filename("alpha_$(n00).txt", tmp_path)
        b1 = resolve_seq_filename("beta_$(n00).txt", tmp_path)
        a2 = resolve_seq_filename("alpha_$(n00).txt", tmp_path)

        # Assert
        assert a1 == "alpha_00.txt", "alpha starts at 0"
        assert b1 == "beta_00.txt", "beta starts at 0 independently"
        assert a2 == "alpha_01.txt", "alpha increments independently"

    def test_corrupt_counter_file_resets(self, tmp_path):
        """An unreadable .cap_seq restarts numbering at 0 instead of raising."""
        # Arrange
        seq_path = tmp_path / ".cap_seq"
        seq_path.write_text("not json!", encoding="utf-8")

        # Act
        actual = resolve_seq_filename("f_$(n000).txt", tmp_path)

        # Assert
        assert actual == "f_000.txt", "resets to 0 on corrupt file"

    def test_missing_directory_created(self, tmp_path):
        """A missing target folder is created so the counter file can be written."""
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
        """A plain keyword and the rest keyword are both extracted."""
        # Act
        actual = parse_keywords("timeout=2s match=OK", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert actual["timeout"] == "2s", "keyword extracted"
        assert actual["match"] == "OK", "rest keyword extracted"

    def test_rest_keyword_consumes_to_eol(self):
        """The rest keyword takes everything to end of line, spaces included."""
        # Act
        actual = parse_keywords(
            "timeout=2s match=hello world", {"timeout", "match"}, rest_keyword="match"
        )

        # Assert
        assert actual["match"] == "hello world", "everything after match="
        assert actual["timeout"] == "2s", "keyword before rest"

    def test_spaces_around_equals(self):
        """Spaces around = are tolerated for both kinds of keyword."""
        # Act
        actual = parse_keywords(
            "timeout = 2s match = OK", {"timeout", "match"}, rest_keyword="match"
        )

        # Assert
        assert actual["timeout"] == "2s", "normalized despite spaces"
        assert actual["match"] == "OK", "normalized despite spaces"

    def test_no_keywords_gives_positional(self):
        """Text with no known keywords lands whole in _positional."""
        # Act
        actual = parse_keywords("just positional", {"timeout"})

        # Assert
        assert actual["_positional"] == "just positional", "no keywords matched"

    def test_unknown_keyword_goes_to_positional(self):
        """An unrecognized key=value goes to _positional instead of being dropped."""
        # Act
        actual = parse_keywords("foo=bar match=OK", {"match"}, rest_keyword="match")

        # Assert
        assert actual["match"] == "OK", "recognized keyword"
        assert actual["_positional"] == "foo=bar", "unrecognized \u2192 positional"

    def test_missing_rest_keyword(self):
        """An absent rest keyword is left out of the result, not set to empty."""
        # Act
        actual = parse_keywords("timeout=2s", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert "match" not in actual, "rest keyword absent"
        assert actual["timeout"] == "2s", "other keyword present"

    def test_case_insensitive(self):
        """Keyword names match regardless of case and come back lowercase."""
        # Act
        actual = parse_keywords("Timeout=2s Match=OK", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert actual["timeout"] == "2s", "case-insensitive match"
        assert actual["match"] == "OK", "case-insensitive match"

    def test_only_rest_keyword(self):
        """A line holding only the rest keyword still captures its full value."""
        # Act
        actual = parse_keywords("match=device is ready", {"match"}, rest_keyword="match")

        # Assert
        assert actual["match"] == "device is ready", "rest keyword only"

    def test_empty_string(self):
        """Empty input yields no keyword entries."""
        # Act
        actual = parse_keywords("", {"timeout", "match"}, rest_keyword="match")

        # Assert
        assert "match" not in actual, "nothing parsed"
        assert "timeout" not in actual, "nothing parsed"

    def test_no_rest_keyword_specified(self):
        """Without a rest keyword every key=value is a single-token keyword."""
        # Act
        actual = parse_keywords("timeout=2s quiet=on", {"timeout", "quiet"})

        # Assert
        assert actual["timeout"] == "2s", "keyword extracted"
        assert actual["quiet"] == "on", "keyword extracted"


# ── CmdResult ───────────────────────────────────────────────────


class TestCmdResult:
    def test_ok(self):
        """ok() is a success with no error text and zero elapsed time."""
        actual = CmdResult.ok(value="")
        assert actual.success is True, "ok() returns success"
        assert actual.error == "", "no error on success"
        assert actual.elapsed_s == 0.0, "no timing yet"

    def test_fail_with_message(self):
        """fail(msg=) carries the message as the error."""
        actual = CmdResult.fail(msg="bad input")
        assert actual.success is False, "failure"
        assert actual.error == "bad input", "error message"

    def test_fail_no_message(self):
        """fail() with no message is still a failure, with an empty error."""
        actual = CmdResult.fail()
        assert actual.success is False, "failure"
        assert actual.error == "", "no message"

    def test_default_is_success(self):
        """A bare CmdResult() defaults to success."""
        actual = CmdResult()
        assert actual.success is True, "default = success"

    def test_elapsed_mutable(self):
        """elapsed_s stays writable so dispatch can stamp timing after the handler."""
        # Arrange
        actual = CmdResult.ok(value="")

        # Act
        actual.elapsed_s = 0.123

        # Assert
        assert actual.elapsed_s == 0.123, "dispatch sets elapsed_s post-handler"

    def test_value_required_keyword(self):
        """ok() without value= raises, so a handler cannot forget its scriptable value."""
        # ``value`` is keyword-only with no default so callers can't
        # silently forget it.  Positional or missing args must error.
        import pytest as _pytest
        with _pytest.raises(TypeError):
            CmdResult.ok()  # type: ignore[call-arg]

    def test_value_empty_explicit(self):
        """An explicit empty value is kept as the empty string."""
        actual = CmdResult.ok(value="")
        assert actual.value == "", "explicit empty is preserved"

    def test_value_set(self):
        """A value passed to the constructor is stored as given."""
        actual = CmdResult(value="0.35.0")
        assert actual.value == "0.35.0", "value captured"

    def test_path_auto_resolved(self):
        """A Path value is converted to an absolute path string."""
        # Path values auto-convert to absolute strings via the
        # constructor; callers can hand a Path directly.
        from pathlib import Path
        p = Path("some/relative/path")
        actual = CmdResult.ok(value=p)
        assert isinstance(actual.value, str), "Path converted to string"
        assert Path(actual.value).is_absolute(), "value is an absolute path"


# ── parse_bool ──────────────────────────────────────────────────────────────


class TestParseBoolSetting:
    """The setting-command grammar decision point: query / set / toggle /
    invalid.  A setting command NEVER flips on a bare (empty) invocation."""

    def test_empty_is_query(self):
        """Empty or blank input is a query, so a bare setting command never flips."""
        assert parse_bool_setting("") is SETTING_QUERY, "bare -> query"
        assert parse_bool_setting("   ") is SETTING_QUERY, "whitespace -> query"

    def test_toggle_verb(self):
        """Only the explicit toggle verb, in any case, requests a flip."""
        assert parse_bool_setting("toggle") is SETTING_TOGGLE, "toggle verb"
        assert parse_bool_setting("TOGGLE") is SETTING_TOGGLE, "case-insensitive"

    @pytest.mark.parametrize("val", ["on", "1", "true", "yes", "y", "t"])
    def test_bool_tokens_true(self, val):
        """Each truthy token from the shared vocabulary sets the setting True."""
        assert parse_bool_setting(val) is True, f"{val!r} -> set True"

    @pytest.mark.parametrize("val", ["off", "0", "false", "no", "n", "f"])
    def test_bool_tokens_false(self, val):
        """Each falsy token from the shared vocabulary sets the setting False."""
        assert parse_bool_setting(val) is False, f"{val!r} -> set False"

    @pytest.mark.parametrize("val", ["2", "banana", "-", "++", "query"])
    def test_invalid_is_none(self, val):
        """Unrecognized input returns None so the caller errors; the /term.color 2 bug."""
        # None -> caller MUST error; NEVER a silent flip (the /term.color 2 bug)
        assert parse_bool_setting(val) is None, f"{val!r} is invalid"


class TestNextInCycle:
    def test_advances_and_wraps(self):
        """cycle steps to the next value and wraps from the last back to the first."""
        vals = ["silent", "quiet", "normal", "verbose"]
        assert next_in_cycle("silent", vals) == "quiet", "steps to next"
        assert next_in_cycle("verbose", vals) == "silent", "wraps at the end"

    def test_unknown_current_resets_to_first(self):
        """An unknown current value restarts the cycle at the first entry."""
        assert next_in_cycle("bogus", ["a", "b", "c"]) == "a", "reset to first"


class TestParseBool:
    @pytest.mark.parametrize("val", ["on", "1", "true", "yes", "y", "t"])
    def test_truthy_lowercase(self, val):
        """Every lowercase truthy token in the vocabulary parses to True."""
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is True, f"expected True for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["off", "0", "false", "no", "n", "f"])
    def test_falsy_lowercase(self, val):
        """Every lowercase falsy token in the vocabulary parses to False."""
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is False, f"expected False for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["ON", "On", "TRUE", "True", "Yes", "Y", "T"])
    def test_truthy_mixed_case(self, val):
        """Truthy tokens are recognized in upper and mixed case."""
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is True, f"expected True for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["OFF", "Off", "FALSE", "False", "No", "N", "F"])
    def test_falsy_mixed_case(self, val):
        """Falsy tokens are recognized in upper and mixed case."""
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is False, f"expected False for {val!r}, got {actual!r}"

    @pytest.mark.parametrize("val", ["  on  ", "\toff\n", " true "])
    def test_whitespace_stripped(self, val):
        """Surrounding spaces, tabs and newlines do not stop a token being recognized."""
        # Act
        actual = parse_bool(val)

        # Assert - value should be recognized despite surrounding whitespace
        assert actual is not None, f"expected bool for {val!r}, got None"

    @pytest.mark.parametrize("val", ["", "maybe", "2", "high", "low", "enable", "x"])
    def test_unrecognized_returns_none(self, val):
        """Unknown words, including near-misses like enable, return None, not False."""
        # Act
        actual = parse_bool(val)

        # Assert
        assert actual is None, f"expected None for {val!r}, got {actual!r}"


# ── select_lines (shared count scheme) ───────────────────────────


class TestSelectLines:
    """The +N=last / -N=first / None=all line-selection scheme."""

    LINES = ["a", "b", "c", "d", "e"]

    def test_none_returns_all(self):
        """No count returns every line unchanged."""
        # Act
        actual = select_lines(self.LINES, None)

        # Assert
        assert actual == self.LINES, "no count -> every line, unchanged"

    def test_positive_returns_last_n(self):
        """A positive count takes the most recent lines from the tail."""
        # Act -- positive N is the most-recent N (tail)
        actual = select_lines(self.LINES, 2)

        # Assert
        expected = ["d", "e"]
        assert actual == expected, "+2 -> last 2 lines (most recent)"

    def test_negative_returns_first_n(self):
        """A negative count takes the oldest lines from the head."""
        # Act -- negative N is the oldest N (head)
        actual = select_lines(self.LINES, -2)

        # Assert
        expected = ["a", "b"]
        assert actual == expected, "-2 -> first 2 lines (oldest)"

    def test_positive_overshoot_clamps_to_all(self):
        """A positive count past the buffer size returns all lines, no error."""
        # Act
        actual = select_lines(self.LINES, 999)

        # Assert
        assert actual == self.LINES, "|N| >= count clamps to all (tail end)"

    def test_negative_overshoot_clamps_to_all(self):
        """A negative count past the buffer size returns all lines, no error."""
        # Act
        actual = select_lines(self.LINES, -999)

        # Assert
        assert actual == self.LINES, "|N| >= count clamps to all (head end)"

    def test_single_line_buffer(self):
        """On a one-line buffer, +1 and -1 both return that line."""
        # Act
        actual_last = select_lines(["only"], 1)
        actual_first = select_lines(["only"], -1)

        # Assert
        assert actual_last == ["only"], "+1 on single line -> that line"
        assert actual_first == ["only"], "-1 on single line -> that line"

    def test_empty_buffer(self):
        """An empty buffer returns empty for any count and never raises."""
        # Act / Assert -- never raises, always empty
        assert select_lines([], 5) == [], "+N on empty -> empty"
        assert select_lines([], -5) == [], "-N on empty -> empty"
        assert select_lines([], None) == [], "None on empty -> empty"


# ── parse_count_arg (name + optional signed count) ───────────────


class TestParseCountArg:
    """Splitting a `name`/`count` arg string for /ss.txt."""

    def test_empty_uses_default_name_and_no_count(self):
        """No args fall back to the default name with no count."""
        # Act
        actual = parse_count_arg("", "screenshot")

        # Assert
        expected = ("screenshot", None)
        assert actual == expected, "no args -> default name, no count"

    def test_bare_positive_count(self):
        """A lone integer is taken as the count, keeping the default name."""
        # Act
        actual = parse_count_arg("50", "screenshot")

        # Assert
        expected = ("screenshot", 50)
        assert actual == expected, "bare int -> count, default name"

    def test_bare_negative_count(self):
        """A leading minus is a negative count, not a flag or a name."""
        # Act
        actual = parse_count_arg("-50", "screenshot")

        # Assert
        expected = ("screenshot", -50)
        assert actual == expected, "leading minus parsed as negative count"

    def test_name_only(self):
        """A lone non-integer token is the name, with no count."""
        # Act
        actual = parse_count_arg("mycap", "screenshot")

        # Assert
        expected = ("mycap", None)
        assert actual == expected, "non-int token -> name, no count"

    @pytest.mark.parametrize("args", ["mycap 5", "5 mycap"])
    def test_name_and_count_order_independent(self, args):
        """Name and count are recognized in either order."""
        # Act
        actual = parse_count_arg(args, "screenshot")

        # Assert
        expected = ("mycap", 5)
        assert actual == expected, f"{args!r} -> (name, count) regardless of order"

    def test_zero_count_rejected(self):
        """A count of 0 is an Invalid line count error, not an empty selection."""
        # Act / Assert
        with pytest.raises(ValueError, match="Invalid line count: 0"):
            parse_count_arg("0", "screenshot")

    def test_two_int_tokens_rejected(self):
        """Two integer tokens are refused; only one count is allowed."""
        # Act / Assert
        with pytest.raises(ValueError, match="Only one count"):
            parse_count_arg("10 20", "screenshot")
