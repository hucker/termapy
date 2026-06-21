"""Tests for vt100.py - pure cfg -> miniterm setting mappings.

The byte pump itself is the vendored pyserial miniterm (its own concern),
and raw passthrough is non-deterministic, so there is no CLI-gold entry.
These tests cover only the pure mapping helpers.
"""

from __future__ import annotations

from termapy.vt100 import _line_ending_to_eol, _miniterm_settings


class TestLineEndingToEol:
    def test_cr(self):
        # Assert
        assert _line_ending_to_eol("\r") == "cr", "CR maps to miniterm 'cr'"

    def test_lf(self):
        # Assert
        assert _line_ending_to_eol("\n") == "lf", "LF maps to miniterm 'lf'"

    def test_crlf(self):
        # Assert
        assert _line_ending_to_eol("\r\n") == "crlf", "CRLF maps to miniterm 'crlf'"

    def test_empty_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol("") == "crlf", "no Enter translation -> default"

    def test_nul_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol(chr(0)) == "crlf", "NUL has no eol mode -> default"

    def test_etx_falls_back_to_crlf(self):
        # Assert
        assert _line_ending_to_eol(chr(3)) == "crlf", "ETX has no eol mode -> default"


class TestMinitermSettings:
    def test_maps_all_fields(self):
        # Arrange
        cfg = {"echo_input": True, "line_ending": "\n", "encoding": "latin-1"}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": True, "eol": "lf", "encoding": "latin-1"}
        assert actual == expected, "cfg maps to miniterm settings"

    def test_defaults_when_keys_absent(self):
        # Arrange
        cfg = {}

        # Act
        actual = _miniterm_settings(cfg)

        # Assert
        expected = {"echo": False, "eol": "cr", "encoding": "utf-8"}
        assert actual == expected, "missing keys fall back to cfg defaults"

    def test_echo_defaults_false(self):
        # Arrange
        cfg = {"line_ending": "\r"}

        # Act
        actual_echo = _miniterm_settings(cfg)["echo"]

        # Assert
        assert actual_echo is False, "echo_input absent -> False"
