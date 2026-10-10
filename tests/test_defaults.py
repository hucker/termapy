"""Tests for defaults.py - validation constants, resolve_color, preview helpers."""

from __future__ import annotations

from unittest.mock import patch

from termapy.defaults import (
    CFG_HELP,
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
        """STANDARD_BAUD_RATES stays in ascending order so pickers list rates low to high."""
        # Assert
        assert STANDARD_BAUD_RATES == tuple(sorted(STANDARD_BAUD_RATES)), "ascending"

    def test_baud_rates_common(self):
        """The everyday rates 9600 and 115200 are offered as standard baud rates."""
        # Assert
        assert 9600 in STANDARD_BAUD_RATES, "common rate present"
        assert 115200 in STANDARD_BAUD_RATES, "common rate present"

    def test_byte_sizes(self):
        """Data bits are exactly the four sizes pyserial accepts: 5, 6, 7 and 8."""
        # Assert
        assert VALID_BYTE_SIZES == {5, 6, 7, 8}, "standard data bits"

    def test_parities(self):
        """None and Even parity are both accepted as valid parity codes."""
        # Assert
        assert "N" in VALID_PARITIES, "None parity"
        assert "E" in VALID_PARITIES, "Even parity"

    def test_stop_bits(self):
        """One and two stop bits are both accepted as valid stop-bit settings."""
        # Assert
        assert 1 in VALID_STOP_BITS, "standard stop bit"
        assert 2 in VALID_STOP_BITS, "two stop bits"

    def test_flow_controls(self):
        """Both no flow control and RTS/CTS hardware flow control are valid choices."""
        # Assert
        assert "none" in VALID_FLOW_CONTROLS, "no flow control"
        assert "rtscts" in VALID_FLOW_CONTROLS, "hardware flow control"


# -- DEFAULT_CFG -------------------------------------------------------------


def _landable_cfg_keys() -> set[str]:
    """Every key a config-editor cursor can land on: all keys anywhere in a
    default cfg, including nested serial.* / ndjson.* / custom-button fields.
    """
    keys: set[str] = set()

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                keys.add(k)
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(DEFAULT_CFG)
    return keys


class TestCfgHelpCoverage:
    """Guardrails keeping CFG_HELP in sync with DEFAULT_CFG.

    The config editor resolves help by the key on the cursor line, so any key
    that appears in a cfg (including nested ones) can be landed on.  These
    fail if a new cfg key ships without a one-line help entry, if a CFG_HELP
    entry outlives its key (e.g. after a rename), or if a help string grows
    past one line -- the editor panel wraps and it "goes forever."  This is
    the structural fix for CFG_HELP drifting out of sync by hand.
    """

    DESC_MAX = 80   # "Line N: <desc>" stays on one line in the help panel
    VALID_MAX = 90  # "Valid: <hint>" stays on one line too

    def test_every_landable_key_has_help(self):
        """Every cfg key, nested ones included, has a help entry -- no 'unknown key' line."""
        missing = sorted(_landable_cfg_keys() - set(CFG_HELP))
        assert missing == [], (
            f"cfg keys with no CFG_HELP entry (editor shows 'unknown key'): {missing}"
        )

    def test_no_stale_help_entries(self):
        """A help entry whose key was renamed or removed from DEFAULT_CFG is caught."""
        stale = sorted(set(CFG_HELP) - _landable_cfg_keys())
        assert stale == [], (
            f"CFG_HELP entries for keys not in DEFAULT_CFG (rename left them behind?): {stale}"
        )

    def test_descriptions_are_one_line(self):
        """Help descriptions fit one editor-panel line: no newline, at most DESC_MAX chars."""
        bad = []
        for key, entry in CFG_HELP.items():
            desc = entry[0] if isinstance(entry, tuple) else entry
            if isinstance(desc, str) and ("\n" in desc or len(desc) > self.DESC_MAX):
                bad.append((key, len(desc)))
        assert bad == [], (
            f"help descriptions must be one line (<= {self.DESC_MAX} chars, no newline): {bad}"
        )

    def test_valid_hints_are_one_line(self):
        """Static 'Valid:' hints fit one line; dynamic callable hints are exempt."""
        bad = []
        for key, entry in CFG_HELP.items():
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            valid = entry[1]
            # entry[1] may be a dynamic callable (e.g. _list_ports); skip those.
            if isinstance(valid, str) and ("\n" in valid or len(valid) > self.VALID_MAX):
                bad.append((key, len(valid)))
        assert bad == [], (
            f"help 'valid' hints must be one line (<= {self.VALID_MAX} chars): {bad}"
        )


class TestDefaultCfg:
    def test_has_config_version(self):
        """A default cfg carries config_version so later loads know what to migrate."""
        # Assert
        assert "config_version" in DEFAULT_CFG, "version key present"

    def test_default_baud_rate(self):
        """A fresh config defaults to 115200 baud, nested under the serial section."""
        # Assert -- nested under cfg["serial"] post-v22.
        serial = DEFAULT_CFG["serial"]
        assert isinstance(serial, dict), "serial is a section"
        assert serial["baud_rate"] == 115200, "standard default"

    def test_default_line_ending(self):
        """A fresh config sends a bare carriage return as its line ending."""
        # Assert
        assert DEFAULT_CFG["eol"] == "\r", "CR default"

    def test_default_prefix(self):
        """A fresh config uses / as the REPL command prefix."""
        # Assert
        assert DEFAULT_CFG["cmd_prefix"] == "/", "slash prefix"

    def test_custom_buttons_is_list(self):
        """A fresh config ships four custom-button slots as a list."""
        # Assert
        assert isinstance(DEFAULT_CFG["custom_buttons"], list), "buttons array"
        assert len(DEFAULT_CFG["custom_buttons"]) == 4, "four button slots"


# -- _list_ports -------------------------------------------------------------


class TestListPorts:
    def test_returns_string(self):
        """The port-list help hint is always a string, whatever hardware is attached."""
        # Act
        actual = _list_ports()

        # Assert
        assert isinstance(actual, str), "always returns a string"

    def test_with_no_ports(self):
        """An empty port enumeration says no ports were found rather than going blank."""
        # Arrange
        with patch("serial.tools.list_ports.comports", return_value=[]):
            # Act
            actual = _list_ports()

        # Assert
        assert "no ports found" in actual, "reports empty"

    def test_oserror_fallback(self):
        """An OSError from enumeration degrades to a 'cannot list' hint, not a traceback."""
        # Arrange -- OSError (udev / IOKit / WMI hiccup) degrades to a
        # friendly fallback string.  Any OTHER exception would propagate
        # so we'd see the traceback and fix whatever's actually broken.
        with patch("serial.tools.list_ports.comports", side_effect=OSError("no udev")):
            # Act
            actual = _list_ports()

        # Assert
        assert "cannot list" in actual, "graceful fallback on environmental failure"


# -- resolve_color -----------------------------------------------------------


class TestResolveColor:
    def test_direct_alias(self):
        """A termapy color alias like brown resolves to its hex value."""
        # Act
        actual = resolve_color("brown")

        # Assert
        assert actual == "#8B4513", "mapped to hex"

    def test_alias_case_insensitive(self):
        """Alias lookup ignores case, so BROWN resolves the same as brown."""
        # Act
        actual = resolve_color("BROWN")

        # Assert
        assert actual == "#8B4513", "case insensitive"

    def test_unknown_color_passthrough(self):
        """A name with no alias is handed through unchanged for Rich to interpret."""
        # Act
        actual = resolve_color("blue")

        # Assert
        assert actual == "blue", "not in aliases, passed through"

    def test_hex_passthrough(self):
        """A hex color code is returned as written, not looked up."""
        # Act
        actual = resolve_color("#ff0000")

        # Assert
        assert actual == "#ff0000", "hex codes pass through"

    def test_light_prefix_with_alias(self):
        """light_blue matches its own alias directly before any prefix stripping."""
        # Act
        actual = resolve_color("light_blue")

        # Assert
        assert actual == "#ADD8E6", "direct alias match for light_blue"

    def test_dark_prefix_resolves(self):
        """dark_brown matches its own alias directly to a darker hex."""
        # Act
        actual = resolve_color("dark_brown")

        # Assert
        assert actual == "#5C3317", "direct alias match"

    def test_light_prefix_strips_to_base_alias(self):
        """lightorange, with no alias of its own, resolves via Rich or the orange base."""
        # "lightorange" is not a direct alias, but "orange" is
        # Act
        actual = resolve_color("lightorange")

        # Assert
        assert actual in ("light_orange", COLOR_ALIASES["orange"]), "Rich resolves light_orange or falls back to orange hex"

    def test_dark_prefix_strips_to_base_alias(self):
        """darkorange, with no alias of its own, resolves via Rich or the orange base."""
        # "darkorange" is not a direct alias, but "orange" is
        # Act
        actual = resolve_color("darkorange")

        # Assert
        assert actual in ("dark_orange", COLOR_ALIASES["orange"]), "Rich resolves dark_orange or falls back to orange hex"


# -- _preview_color ----------------------------------------------------------


class TestPreviewColor:
    def test_empty_string(self):
        """An empty color value shows no preview at all."""
        # Act
        actual = _preview_color("")

        # Assert
        assert actual == "", "empty input returns empty"

    def test_valid_color(self):
        """A plain Rich color name gets a Color: preview line."""
        # Act
        actual = _preview_color("red")

        # Assert
        assert "Color:" in actual, "shows color info"

    def test_alias_color(self):
        """An alias preview shows the -> resolution to its underlying value."""
        # Act
        actual = _preview_color("brown")

        # Assert
        assert "Color:" in actual, "resolves alias"
        assert "->" in actual, "shows alias resolution"

    def test_unknown_color(self):
        """An unparseable color name is flagged as unknown in the preview."""
        # Act
        actual = _preview_color("notacolor999")

        # Assert
        assert "unknown color" in actual, "reports unknown"


# -- _preview_markup ---------------------------------------------------------


class TestPreviewMarkup:
    def test_empty_string(self):
        """An empty echo format shows no preview at all."""
        # Act
        actual = _preview_markup("")

        # Assert
        assert actual == "", "empty input returns empty"

    def test_with_cmd_placeholder(self):
        """A markup format previews with a sample command substituted for {cmd}."""
        # Act
        actual = _preview_markup("[purple]> {cmd}[/]")

        # Assert
        assert "AT+INFO" in actual, "sample command substituted"
        assert "Preview:" in actual, "labeled as preview"

    def test_plain_format(self):
        """A bare {cmd} format with no markup still gets the sample command substituted."""
        # Act
        actual = _preview_markup("{cmd}")

        # Assert
        assert "AT+INFO" in actual, "placeholder replaced"
