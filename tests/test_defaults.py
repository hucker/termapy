"""Tests for defaults.py — validation constants, resolve_color, preview helpers."""

from __future__ import annotations

from unittest.mock import patch

from termapy.defaults import (
    COLOR_ALIASES,
    DEFAULT_CFG,
    STANDARD_BAUD_RATES,
    VALID_BYTE_SIZES,
    VALID_FLOW_CONTROLS,
    VALID_PARITIES,
    VALID_STOP_BITS,
    _list_ports,
    _preview_color,
    _preview_markup,
    resolve_color,
)


# -- Validation constants ----------------------------------------------------


class TestValidationConstants:
    def test_baud_rates_sorted(self):
        # Assert
        assert STANDARD_BAUD_RATES == tuple(sorted(STANDARD_BAUD_RATES))  # ascending

    def test_baud_rates_common(self):
        # Assert
        assert 9600 in STANDARD_BAUD_RATES  # common rate present
        assert 115200 in STANDARD_BAUD_RATES  # common rate present

    def test_byte_sizes(self):
        # Assert
        assert VALID_BYTE_SIZES == {5, 6, 7, 8}  # standard data bits

    def test_parities(self):
        # Assert
        assert "N" in VALID_PARITIES  # None parity
        assert "E" in VALID_PARITIES  # Even parity

    def test_stop_bits(self):
        # Assert
        assert 1 in VALID_STOP_BITS  # standard stop bit
        assert 2 in VALID_STOP_BITS  # two stop bits

    def test_flow_controls(self):
        # Assert
        assert "none" in VALID_FLOW_CONTROLS  # no flow control
        assert "rtscts" in VALID_FLOW_CONTROLS  # hardware flow control


# -- DEFAULT_CFG -------------------------------------------------------------


class TestDefaultCfg:
    def test_has_config_version(self):
        # Assert
        assert "config_version" in DEFAULT_CFG  # version key present

    def test_default_baud_rate(self):
        # Assert
        assert DEFAULT_CFG["baud_rate"] == 115200  # standard default

    def test_default_line_ending(self):
        # Assert
        assert DEFAULT_CFG["line_ending"] == "\r"  # CR default

    def test_default_prefix(self):
        # Assert
        assert DEFAULT_CFG["cmd_prefix"] == "/"  # slash prefix

    def test_custom_buttons_is_list(self):
        # Assert
        assert isinstance(DEFAULT_CFG["custom_buttons"], list)  # buttons array
        assert len(DEFAULT_CFG["custom_buttons"]) == 4  # four button slots


# -- _list_ports -------------------------------------------------------------


class TestListPorts:
    def test_returns_string(self):
        # Act
        actual = _list_ports()

        # Assert
        assert isinstance(actual, str)  # always returns a string

    def test_with_no_ports(self):
        # Arrange
        with patch("serial.tools.list_ports.comports", return_value=[]):
            # Act
            actual = _list_ports()

        # Assert
        assert "no ports found" in actual  # reports empty

    def test_exception_handling(self):
        # Arrange
        with patch("serial.tools.list_ports.comports", side_effect=Exception("fail")):
            # Act
            actual = _list_ports()

        # Assert
        assert "cannot list" in actual  # graceful fallback


# -- resolve_color -----------------------------------------------------------


class TestResolveColor:
    def test_direct_alias(self):
        # Act
        actual = resolve_color("brown")

        # Assert
        assert actual == "#8B4513"  # mapped to hex

    def test_alias_case_insensitive(self):
        # Act
        actual = resolve_color("BROWN")

        # Assert
        assert actual == "#8B4513"  # case insensitive

    def test_unknown_color_passthrough(self):
        # Act
        actual = resolve_color("blue")

        # Assert
        assert actual == "blue"  # not in aliases, passed through

    def test_hex_passthrough(self):
        # Act
        actual = resolve_color("#ff0000")

        # Assert
        assert actual == "#ff0000"  # hex codes pass through

    def test_light_prefix_with_alias(self):
        # Act
        actual = resolve_color("light_blue")

        # Assert
        assert actual == "#ADD8E6"  # direct alias match for light_blue

    def test_dark_prefix_resolves(self):
        # Act
        actual = resolve_color("dark_brown")

        # Assert
        assert actual == "#5C3317"  # direct alias match

    def test_light_prefix_strips_to_base_alias(self):
        # "lightorange" is not a direct alias, but "orange" is
        # Act
        actual = resolve_color("lightorange")

        # Assert — either Rich resolves light_orange or falls back to orange hex
        assert actual in ("light_orange", COLOR_ALIASES["orange"])

    def test_dark_prefix_strips_to_base_alias(self):
        # "darkorange" is not a direct alias, but "orange" is
        # Act
        actual = resolve_color("darkorange")

        # Assert — either Rich resolves dark_orange or falls back to orange hex
        assert actual in ("dark_orange", COLOR_ALIASES["orange"])


# -- _preview_color ----------------------------------------------------------


class TestPreviewColor:
    def test_empty_string(self):
        # Act
        actual = _preview_color("")

        # Assert
        assert actual == ""  # empty input returns empty

    def test_valid_color(self):
        # Act
        actual = _preview_color("red")

        # Assert
        assert "Color:" in actual  # shows color info

    def test_alias_color(self):
        # Act
        actual = _preview_color("brown")

        # Assert
        assert "Color:" in actual  # resolves alias
        assert "->" in actual  # shows alias resolution

    def test_unknown_color(self):
        # Act
        actual = _preview_color("notacolor999")

        # Assert
        assert "unknown color" in actual  # reports unknown


# -- _preview_markup ---------------------------------------------------------


class TestPreviewMarkup:
    def test_empty_string(self):
        # Act
        actual = _preview_markup("")

        # Assert
        assert actual == ""  # empty input returns empty

    def test_with_cmd_placeholder(self):
        # Act
        actual = _preview_markup("[purple]> {cmd}[/]")

        # Assert
        assert "AT+INFO" in actual  # sample command substituted
        assert "Preview:" in actual  # labeled as preview

    def test_plain_format(self):
        # Act
        actual = _preview_markup("{cmd}")

        # Assert
        assert "AT+INFO" in actual  # placeholder replaced
