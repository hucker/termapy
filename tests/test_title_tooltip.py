"""Tests for the title-bar tooltip formatters (pure string logic).

``format_tooltip_value`` and ``format_title_tooltip`` are the shared
formatting spine of the config, connection-status, and port tooltips.
Both were app-coupled (a staticmethod on SerialTerminal reached back
into from title_bar) and so had zero coverage; now pure, they're
tested directly -- no app, no widgets.
"""

from __future__ import annotations

from termapy.port_control import ChipFacts
from termapy.title_bar import (
    format_title_tooltip,
    format_tooltip_value,
    port_tooltip_pairs,
)


class TestFormatTooltipValue:
    def test_none_becomes_none_marker(self):
        """An unset value shows as (none) so a blank never hides a missing setting."""
        assert format_tooltip_value(None) == "(none)", "None renders as (none)"

    def test_bools_become_on_off(self):
        """Booleans render as ON/OFF, the vocabulary the setting commands use."""
        assert format_tooltip_value(True) == "ON", "True renders as ON"
        assert format_tooltip_value(False) == "OFF", "False renders as OFF"

    def test_empty_string_becomes_empty_marker(self):
        """An empty string shows as (empty), distinct from both None and blank."""
        assert format_tooltip_value("") == "(empty)", "empty string is called out"

    def test_control_characters_are_reprd(self):
        """Control characters like an EOL of CR LF render as repr, not raw line breaks."""
        actual = format_tooltip_value("\r\n")
        assert actual == repr("\r\n"), "non-printables shown as repr, not raw"

    def test_plain_string_passes_through(self):
        """Printable strings are shown unchanged, with no quotes or escaping."""
        assert format_tooltip_value("COM4") == "COM4", "plain text unchanged"

    def test_numbers_stringify(self):
        """Numbers render as plain str() with no separators or formatting."""
        assert format_tooltip_value(115200) == "115200", "numbers stringify"


class TestFormatTitleTooltip:
    def test_three_section_layout(self):
        """Tooltip is title, blank, key rows, blank, then a "Click to:" action line."""
        actual = format_title_tooltip("demo.cfg", [("port", "COM4"), ("echo", True)], "edit config")
        lines = actual.splitlines()
        assert lines[0] == "demo.cfg", "title on the first line"
        assert lines[1] == "", "blank line after the title"
        assert lines[-1] == "Click to: edit config", "action on the last line"
        assert lines[-2] == "", "blank line before the action"

    def test_equals_signs_align_across_rows(self):
        """Keys are padded to the longest so every row's = sign sits in one column."""
        actual = format_title_tooltip("t", [("port", "COM4"), ("baud_rate", 115200)], "act")
        positions = {line.index("=") for line in actual.splitlines() if "=" in line}
        assert len(positions) == 1, "keys padded so the = signs line up"

    def test_values_go_through_the_value_formatter(self):
        """Row values use format_tooltip_value, so bools and None get their markers."""
        actual = format_title_tooltip("t", [("echo", True), ("log", None)], "act")
        assert "echo  = ON" in actual, "bool formatted as ON"
        assert "log   = (none)" in actual, "None formatted as (none)"

    def test_empty_keys_are_skipped(self):
        """A pair with an empty key is dropped, letting callers omit rows conditionally."""
        actual = format_title_tooltip("t", [("", "hidden"), ("port", "COM4")], "act")
        assert "hidden" not in actual, "empty-key rows dropped"
        assert "port  = COM4" in actual, "real rows kept"


class TestPortTooltipPairs:
    def test_none_facts_gives_status_fallback(self):
        """A port with no chip facts gets one status row instead of an empty tooltip."""
        actual = port_tooltip_pairs(None)
        expected = [("status", "no USB chip info available")]
        assert actual == expected, "unenumerable port -> single status line"

    def test_none_fields_are_dropped(self):
        """Unknown (None) fields are omitted rather than shown as (none) rows."""
        # Arrange -- only two fields known (typical non-USB / partial gather).
        facts = ChipFacts(description="USB Serial Port", vid_pid="0403:6015")

        # Act
        actual = port_tooltip_pairs(facts)

        # Assert
        expected = [("description", "USB Serial Port"), ("vid_pid", "0403:6015")]
        assert actual == expected, "None fields skipped; known fields kept in order"

    def test_field_order_is_stable(self):
        """Rows follow a fixed display order, independent of which fields were set first."""
        # Arrange -- populate fields out of display order.
        facts = ChipFacts(in_use="no", description="d", model="m")

        # Act
        keys = [k for k, _ in port_tooltip_pairs(facts)]

        # Assert -- description before model before in_use (display order,
        # not the order they were set).
        assert keys == ["description", "model", "in_use"], "fixed display order"

    def test_non_display_fields_are_excluded(self):
        """device, product, interface and permissions are gathered but never shown."""
        # Arrange -- device/product/interface/permissions are gathered but
        # deliberately not shown in the tooltip.
        facts = ChipFacts(
            device="COM4", product="p", interface="i", permissions="rw", description="d"
        )

        # Act
        keys = [k for k, _ in port_tooltip_pairs(facts)]

        # Assert
        assert keys == ["description"], "only display fields appear"
