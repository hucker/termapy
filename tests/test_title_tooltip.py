"""Tests for the title-bar tooltip formatters (pure string logic).

``format_tooltip_value`` and ``format_title_tooltip`` are the shared
formatting spine of the config, connection-status, and port tooltips.
Both were app-coupled (a staticmethod on SerialTerminal reached back
into from title_bar) and so had zero coverage; now pure, they're
tested directly -- no app, no widgets.
"""

from __future__ import annotations

from termapy.title_bar import format_title_tooltip, format_tooltip_value


class TestFormatTooltipValue:
    def test_none_becomes_none_marker(self):
        assert format_tooltip_value(None) == "(none)", "None renders as (none)"

    def test_bools_become_on_off(self):
        assert format_tooltip_value(True) == "ON", "True renders as ON"
        assert format_tooltip_value(False) == "OFF", "False renders as OFF"

    def test_empty_string_becomes_empty_marker(self):
        assert format_tooltip_value("") == "(empty)", "empty string is called out"

    def test_control_characters_are_reprd(self):
        actual = format_tooltip_value("\r\n")
        assert actual == repr("\r\n"), "non-printables shown as repr, not raw"

    def test_plain_string_passes_through(self):
        assert format_tooltip_value("COM4") == "COM4", "plain text unchanged"

    def test_numbers_stringify(self):
        assert format_tooltip_value(115200) == "115200", "numbers stringify"


class TestFormatTitleTooltip:
    def test_three_section_layout(self):
        actual = format_title_tooltip(
            "demo.cfg", [("port", "COM4"), ("echo", True)], "edit config"
        )
        lines = actual.splitlines()
        assert lines[0] == "demo.cfg", "title on the first line"
        assert lines[1] == "", "blank line after the title"
        assert lines[-1] == "Click to: edit config", "action on the last line"
        assert lines[-2] == "", "blank line before the action"

    def test_equals_signs_align_across_rows(self):
        actual = format_title_tooltip(
            "t", [("port", "COM4"), ("baud_rate", 115200)], "act"
        )
        positions = {ln.index("=") for ln in actual.splitlines() if "=" in ln}
        assert len(positions) == 1, "keys padded so the = signs line up"

    def test_values_go_through_the_value_formatter(self):
        actual = format_title_tooltip("t", [("echo", True), ("log", None)], "act")
        assert "echo  = ON" in actual, "bool formatted as ON"
        assert "log   = (none)" in actual, "None formatted as (none)"

    def test_empty_keys_are_skipped(self):
        actual = format_title_tooltip("t", [("", "hidden"), ("port", "COM4")], "act")
        assert "hidden" not in actual, "empty-key rows dropped"
        assert "port  = COM4" in actual, "real rows kept"
