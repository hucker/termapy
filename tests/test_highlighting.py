"""The editors' highlight map comes from Pygments (``termapy.highlighting``), not a tree-sitter query.

Textual colors a ``TextArea`` line from ``(start, end, name)`` spans in UTF-8
byte offsets; these tests pin that ``highlights`` produces that shape for a
config (JSON), a protocol script (TOML) and a run script, that a token spanning
lines becomes one span per row, and that non-ASCII text keeps byte offsets.
"""

from __future__ import annotations

import pytest

from termapy.highlighting import highlights

pytestmark = pytest.mark.rule("editor-highlight-pygments")

JSON = '{\n    "title": "Démo",\n    "baud": 115200,\n    "flag": true,\n    "none": null\n}\n'
TOML = '# Status\n[test]\nname = "ping"\nwhen = 1979-05-27T07:32:00Z\nok = true\n'
RUN = "# Status Check\n/print hello $(NAME)\n$(X) <- /delay 300ms\nAT+LED on\n"


def _names(spans: list[tuple[int, int | None, str]]) -> list[str]:
    """The highlight names of ``spans``, in order."""
    return [name for _, _, name in spans]


class TestJson:
    def test_a_key_is_a_label_and_its_value_a_string_in_bytes(self):
        """``"title": "Démo"`` gives a json.label and a string span whose end counts é as two bytes."""
        actual = highlights(JSON, "json")[1]
        assert (4, 11, "json.label") in actual, actual
        assert (13, 20, "string") in actual, "the string starts after the colon and spans 7 bytes"

    def test_true_and_null_are_told_apart(self):
        """``true`` is boolean.true, ``null`` is json.null, and ``false`` is boolean.false, as Rich colors them apart."""
        rows = highlights(JSON, "json")
        assert "boolean.true" in _names(rows[3]), rows[3]
        assert "json.null" in _names(rows[4]), rows[4]
        assert "boolean.false" in _names(highlights('{"off": false}', "json")[0])

    def test_a_number_and_the_braces(self):
        """115200 is a number; the opening brace is a bracket at byte 0 of row 0."""
        rows = highlights(JSON, "json")
        assert "number" in _names(rows[2]), rows[2]
        assert rows[0] == [(0, 1, "punctuation.bracket")], rows[0]


class TestToml:
    def test_a_key_an_operator_and_a_date(self):
        """A bare key is toml.type, ``=`` an operator, an RFC 3339 date toml.datetime, ``true`` boolean.true."""
        rows = highlights(TOML, "toml")
        assert (0, 4, "toml.type") in rows[2] and (5, 6, "operator") in rows[2], rows[2]
        assert "toml.datetime" in _names(rows[3]), rows[3]
        assert "boolean.true" in _names(rows[4]), rows[4]

    def test_a_comment_runs_to_the_line_end_and_a_table_header_is_a_keyword(self):
        """The comment line is one comment span; ``[test]`` is keyword spans."""
        rows = highlights(TOML, "toml")
        assert rows[0] == [(0, 8, "comment")], rows[0]
        assert set(_names(rows[1])) == {"keyword"}, rows[1]

    def test_a_string_across_lines_is_one_span_per_row(self):
        """A multi-line basic string ends row 0 with a span open to the line's end and starts row 1 at byte 0."""
        rows = highlights('s = """ab\ncd"""\n', "toml")
        assert rows[0][-1][1:] == (None, "string"), rows[0]
        assert rows[1][0][::2] == (0, "string"), rows[1]


class TestRun:
    def test_a_comment_a_command_and_a_variable(self):
        """``# Status Check`` is a comment, ``/print`` a function and ``$(NAME)`` a variable at its bytes."""
        rows = highlights(RUN, "run")
        assert rows[0] == [(0, 14, "comment")], rows[0]
        assert (0, 6, "function") in rows[1] and (13, 20, "variable") in rows[1], rows[1]

    def test_a_capture_names_its_command_and_a_duration_is_a_number(self):
        """``$(X) <- /delay 300ms`` is a variable, an operator, a function and a number."""
        actual = highlights(RUN, "run")[2]
        assert _names(actual) == ["variable", "operator", "function", "number"], actual

    def test_a_device_line_stays_plain(self):
        """``AT+LED on`` gets no span: the device's grammar is not termapy's to color."""
        assert 3 not in highlights(RUN, "run"), "a device line has no highlight"

    def test_an_escaped_dollar_is_not_a_variable(self):
        """``\\$(SLAVE)`` prints the name, so it is plain text, while ``$(SLAVE)`` beside it is a variable."""
        actual = highlights("/print \\$(SLAVE) = $(SLAVE)\n", "run")[0]
        assert _names(actual) == ["function", "variable"], actual


class TestShape:
    @pytest.mark.parametrize(("lexer", "text"), [("json", JSON), ("toml", TOML), ("run", RUN)])
    def test_spans_stay_inside_their_lines(self, lexer, text):
        """Every span's bytes fit the UTF-8 length of its row, so no offset drifts after a multi-byte character."""
        lines = text.split("\n")
        for row, spans in highlights(text, lexer).items():
            width = len(lines[row].encode("utf-8"))
            for start, end, _ in spans:
                assert 0 <= start < width, (row, start, width)
                assert end is None or start < end <= width, (row, end, width)

    def test_an_unknown_lexer_fails_loud(self):
        """A lexer name outside json, toml and run is a KeyError, never silently plain text."""
        with pytest.raises(KeyError):
            highlights("x", "bash")
