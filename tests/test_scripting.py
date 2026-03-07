"""Tests for terapy.scripting — pure functions, no serial or Textual needed."""

import re

import pytest

from terapy.scripting import expand_template, parse_duration, parse_script_lines


# ── expand_template ──────────────────────────────────────────────


class TestExpandTemplate:
    def test_no_placeholders_passthrough(self):
        result, counters = expand_template("plain_name", {})
        assert result == "plain_name"
        assert counters == {}

    def test_seq_increment_from_zero(self):
        result, c = expand_template("t_{seq1+}", {})
        assert result == "t_1"
        assert c[1] == 1

    def test_seq_increment_from_existing(self):
        result, c = expand_template("t_{seq1+}", {1: 5})
        assert result == "t_6"
        assert c[1] == 6

    def test_seq_read_without_increment(self):
        result, c = expand_template("t_{seq1}", {1: 5})
        assert result == "t_5"
        assert c[1] == 5

    def test_seq_read_unset_counter(self):
        result, c = expand_template("t_{seq1}", {})
        assert result == "t_0"

    def test_multiple_increments_same_level(self):
        result, c = expand_template("{seq1+}_{seq1+}", {})
        assert result == "1_2"
        assert c[1] == 2

    def test_cascade_reset(self):
        """Incrementing seq2 resets seq1 to 0."""
        result, c = expand_template("t_{seq2+}_{seq1+}", {1: 3, 2: 1})
        assert result == "t_2_1"
        assert c[1] == 1
        assert c[2] == 2

    def test_cascade_three_levels(self):
        """Incrementing seq3 resets seq1 and seq2."""
        counters = {1: 5, 2: 3, 3: 1}
        result, c = expand_template("{seq3+}_{seq2}_{seq1}", counters)
        assert result == "2_0_0"
        assert c[1] == 0
        assert c[2] == 0
        assert c[3] == 2

    def test_read_after_cascade(self):
        """After cascade reset, reading lower counter returns 0."""
        _, c = expand_template("{seq2+}", {1: 5, 2: 0})
        result, c = expand_template("{seq2}_{seq1+}", c)
        assert result == "1_1"

    def test_does_not_mutate_input(self):
        original = {1: 3}
        _, new = expand_template("t_{seq1+}", original)
        assert original[1] == 3
        assert new[1] == 4

    def test_datetime_placeholder(self):
        result, _ = expand_template("t_{datetime}", {})
        assert re.match(r"t_\d{8}_\d{6}$", result)

    def test_mixed_seq_and_datetime(self):
        result, c = expand_template("{seq1+}_{datetime}", {})
        parts = result.split("_", 1)
        assert parts[0] == "1"
        assert re.match(r"\d{8}_\d{6}$", parts[1])

    def test_starttime_placeholder(self):
        result, _ = expand_template("t_{starttime}", {}, start_time="20260305_100000")
        assert result == "t_20260305_100000"

    def test_starttime_default_empty(self):
        result, _ = expand_template("t_{starttime}", {})
        assert result == "t_"

    def test_starttime_with_seq(self):
        result, c = expand_template(
            "{starttime}_{seq1+}", {}, start_time="20260305_100000"
        )
        assert result == "20260305_100000_1"
        assert c[1] == 1

    def test_hierarchical_script_sequence(self):
        """Simulate a typical script: two sections with two steps each."""
        c: dict[int, int] = {}

        # Section 1, step 1
        r, c = expand_template("t_{seq2+}_{seq1+}", c)
        assert r == "t_1_1"

        # Section 1, step 2
        r, c = expand_template("t_{seq2}_{seq1+}", c)
        assert r == "t_1_2"

        # Section 2, step 1 (seq2+ resets seq1)
        r, c = expand_template("t_{seq2+}_{seq1+}", c)
        assert r == "t_2_1"

        # Section 2, step 2
        r, c = expand_template("t_{seq2}_{seq1+}", c)
        assert r == "t_2_2"


# ── parse_duration ───────────────────────────────────────────────


class TestParseDuration:
    def test_milliseconds(self):
        assert parse_duration("500ms") == 0.5

    def test_seconds(self):
        assert parse_duration("1s") == 1.0

    def test_fractional_seconds(self):
        assert parse_duration("1.5s") == 1.5

    def test_fractional_milliseconds(self):
        assert parse_duration("0.5ms") == 0.0005

    def test_whitespace(self):
        assert parse_duration("  500ms  ") == 0.5

    def test_invalid_no_unit(self):
        with pytest.raises(ValueError):
            parse_duration("500")

    def test_invalid_bad_unit(self):
        with pytest.raises(ValueError):
            parse_duration("500us")

    def test_invalid_text(self):
        with pytest.raises(ValueError):
            parse_duration("fast")

    def test_empty(self):
        with pytest.raises(ValueError):
            parse_duration("")


# ── parse_script_lines ───────────────────────────────────────────


class TestParseScriptLines:
    def test_comment(self):
        result = parse_script_lines(["# a comment"])
        assert result == [("skip", "# a comment")]

    def test_blank_line(self):
        result = parse_script_lines(["", "   "])
        assert result == [("skip", ""), ("skip", "")]

    def test_serial_command(self):
        result = parse_script_lines(["rev"])
        assert result == [("serial", "rev")]

    def test_repl_command(self):
        result = parse_script_lines(["!!delay 500ms"])
        assert result == [("repl", "delay 500ms")]

    def test_repl_command_with_leading_space(self):
        result = parse_script_lines(["  !!ss_svg test"])
        assert result == [("repl", "ss_svg test")]

    def test_custom_prefix(self):
        result = parse_script_lines(["@@delay 1s"], prefix="@@")
        assert result == [("repl", "delay 1s")]

    def test_mixed_script(self):
        lines = [
            "# smoke test",
            "",
            "rev",
            "!!delay 500ms",
            "!!ss_svg test_{seq1+}",
            "help",
        ]
        result = parse_script_lines(lines)
        assert result == [
            ("skip", "# smoke test"),
            ("skip", ""),
            ("serial", "rev"),
            ("repl", "delay 500ms"),
            ("repl", "ss_svg test_{seq1+}"),
            ("serial", "help"),
        ]
