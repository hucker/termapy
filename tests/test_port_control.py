"""Tests for port_control.py - pure serial port control functions."""

from types import SimpleNamespace
from unittest.mock import MagicMock


from termapy.port_control import (
    MANUFACTURER_ALIASES,
    PORT_PROPS,
    SERIAL_KEYS,
    canonical_manufacturer,
    get_set_flow,
    get_set_hw_line,
    get_set_prop,
    parse_bool_value,
    parse_mode,
    parse_open_args,
    port_info,
    read_signal,
    send_break,
    set_mode,
)


def _mock_ser(**overrides):
    """Create a mock serial-like object with sensible defaults."""
    defaults = {
        "baudrate": 115200,
        "bytesize": 8,
        "parity": "N",
        "stopbits": 1,
        "dtr": True,
        "rts": True,
        "cts": False,
        "dsr": False,
        "ri": False,
        "cd": False,
        "rtscts": False,
        "xonxoff": False,
    }
    defaults.update(overrides)
    ser = SimpleNamespace(**defaults)
    ser.send_break = MagicMock()
    return ser


def _cfg(**overrides):
    """Create a minimal config dict."""
    defaults = {
        "port": "COM4",
        "baud_rate": 115200,
        "byte_size": 8,
        "parity": "N",
        "stop_bits": 1,
        "flow_control": "none",
        "encoding": "utf-8",
    }
    defaults.update(overrides)
    return defaults


# ── Constants ────────────────────────────────────────────────────────────────


class TestConstants:

    def test_serial_keys_contains_expected(self):
        assert "port" in SERIAL_KEYS, "SERIAL_KEYS should contain 'port'"
        assert "baud_rate" in SERIAL_KEYS, "SERIAL_KEYS should contain 'baud_rate'"
        assert "flow_control" in SERIAL_KEYS, "SERIAL_KEYS should contain 'flow_control'"

    def test_port_props_has_all_keys(self):
        assert "baud_rate" in PORT_PROPS, "PORT_PROPS should contain 'baud_rate'"
        assert "byte_size" in PORT_PROPS, "PORT_PROPS should contain 'byte_size'"
        assert "parity" in PORT_PROPS, "PORT_PROPS should contain 'parity'"
        assert "stop_bits" in PORT_PROPS, "PORT_PROPS should contain 'stop_bits'"


# ── parse_bool_value ─────────────────────────────────────────────────────────


class TestParseBoolValue:

    def test_true_values(self):
        for val in ("1", "on", "true", "high"):
            assert parse_bool_value(val) is True, f"'{val}' should parse as True"

    def test_false_values(self):
        for val in ("0", "off", "false", "low"):
            assert parse_bool_value(val) is False, f"'{val}' should parse as False"

    def test_invalid_returns_none(self):
        assert parse_bool_value("maybe") is None, "'maybe' should return None"
        assert parse_bool_value("") is None, "empty string should return None"


# ── port_info ────────────────────────────────────────────────────────────────


class TestPortInfo:

    def test_disconnected(self):
        # Act
        msgs, effects = port_info(_cfg(), None)

        # Assert - shows config values with disconnected state
        texts = [t for t, _ in msgs]
        assert any("disconnected" in t for t in texts), "should show disconnected state"
        assert any("115200" in t for t in texts), "should show baud rate config value"

    def test_connected_shows_hw_lines(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = port_info(_cfg(), ser)

        # Assert - shows hardware line values
        texts = [t for t, _ in msgs]
        assert any("connected" in t for t in texts), "should show connected state"
        assert any("DTR" in t for t in texts), "should show DTR line"
        assert any("RTS" in t for t in texts), "should show RTS line"


# ── get_set_prop ─────────────────────────────────────────────────────────────


class TestGetSetProp:

    def test_get_disconnected_shows_config(self):
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "")

        # Assert
        assert any("115200" in t for t, _ in msgs), "should show config baud rate"
        assert any("disconnected" in t for t, _ in msgs), "should indicate disconnected"

    def test_get_connected_shows_live_value(self):
        # Arrange
        ser = _mock_ser(baudrate=9600)

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "")

        # Assert
        assert any("9600" in t for t, _ in msgs), "should show live baud rate value"

    def test_set_valid_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "9600")

        # Assert - value changed, side effects requested
        assert ser.baudrate == 9600, "baudrate should be updated to 9600"
        assert effects.get("update_title") is True, "should request title update"
        assert effects["cfg_update"]["baud_rate"] == 9600, "cfg_update should contain new baud_rate"

    def test_set_invalid_parity(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "parity", "X")

        # Assert - error message, no side effects
        assert any("red" == c for _, c in msgs), "should show red error message"
        assert not effects.get("cfg_update"), "should not produce cfg_update on invalid parity"

    def test_set_when_disconnected(self):
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "9600")

        # Assert - not connected warning
        assert any("Not connected" in t for t, _ in msgs), "should warn not connected"


# ── get_set_flow ─────────────────────────────────────────────────────────────


class TestGetSetFlow:

    def test_get_flow(self):
        # Act
        msgs, _ = get_set_flow(None, _cfg(), "")

        # Assert
        assert any("none" in t for t, _ in msgs), "should show current flow control as 'none'"

    def test_set_valid_flow(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_flow(ser, _cfg(), "rtscts")

        # Assert
        assert ser.rtscts is True, "rtscts should be enabled on serial object"
        assert effects.get("sync_hw") is True, "should request hw sync"
        assert effects["cfg_update"]["flow_control"] == "rtscts", "cfg_update should set flow_control to rtscts"

    def test_set_invalid_flow(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = get_set_flow(ser, _cfg(), "invalid")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid flow"


# ── get_set_hw_line ──────────────────────────────────────────────────────────


class TestGetSetHwLine:

    def test_get_dtr(self):
        # Arrange
        ser = _mock_ser(dtr=True)

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs), "DTR=True should display as '1'"

    def test_set_dtr(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_hw_line(ser, "dtr", "0")

        # Assert
        assert ser.dtr is False, "DTR should be set to False"
        assert effects.get("sync_hw") is True, "should request hw sync after DTR change"

    def test_invalid_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "maybe")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid hw line value"

    def test_disconnected(self):
        # Act
        msgs, _ = get_set_hw_line(None, "dtr", "1")

        # Assert
        assert any("Not connected" in t for t, _ in msgs), "should warn not connected"


# ── read_signal ──────────────────────────────────────────────────────────────


class TestReadSignal:

    def test_read_cts(self):
        # Arrange
        ser = _mock_ser(cts=True)

        # Act
        msgs, _ = read_signal(ser, "cts", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs), "CTS=True should display as '1'"

    def test_read_only_rejects_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = read_signal(ser, "cts", "1")

        # Assert
        assert any("read-only" in t for t, _ in msgs), "should reject write to read-only signal"

    def test_disconnected(self):
        # Act
        msgs, _ = read_signal(None, "cts", "")

        # Assert
        assert any("Not connected" in t for t, _ in msgs), "should warn not connected"


# ── send_break ───────────────────────────────────────────────────────────────


class TestSendBreak:

    def test_default_duration(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "")

        # Assert
        ser.send_break.assert_called_once_with(duration=0.25)
        assert any("250ms" in t for t, _ in msgs), "should report 250ms default duration"

    def test_custom_duration(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "100")

        # Assert
        ser.send_break.assert_called_once_with(duration=0.1)

    def test_invalid_duration(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "abc")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid duration"
        ser.send_break.assert_not_called()

    def test_zero_duration_invalid(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "0")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for zero duration"

    def test_disconnected(self):
        # Act
        msgs, _ = send_break(None, "")

        # Assert
        assert any("Not connected" in t for t, _ in msgs), "should warn not connected"


# ── parse_mode ──────────────────────────────────────────────────────────────


class TestParseMode:

    def test_n81(self):
        # Act
        actual = parse_mode("N81")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, "N81 should parse to (N, 8, 1.0)"

    def test_e71(self):
        # Act
        actual = parse_mode("E71")

        # Assert
        expected = ("E", 7, 1.0)
        assert actual == expected, "E71 should parse to (E, 7, 1.0)"

    def test_o81_5(self):
        # Act
        actual = parse_mode("O81.5")

        # Assert
        expected = ("O", 8, 1.5)
        assert actual == expected, "O81.5 should parse to (O, 8, 1.5)"

    def test_s52(self):
        # Act
        actual = parse_mode("S52")

        # Assert
        expected = ("S", 5, 2.0)
        assert actual == expected, "S52 should parse to (S, 5, 2.0)"

    def test_lowercase(self):
        # Act
        actual = parse_mode("n81")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, "lowercase n81 should parse to (N, 8, 1.0)"

    def test_invalid_parity(self):
        assert parse_mode("X81") is None, "X is not a valid parity letter"

    def test_invalid_byte_size(self):
        assert parse_mode("N91") is None, "9 is not a valid byte size"

    def test_invalid_stop_bits(self):
        assert parse_mode("N83") is None, "3 is not a valid stop bits value"

    def test_empty(self):
        assert parse_mode("") is None, "empty string should return None"

    def test_garbage(self):
        assert parse_mode("COM3") is None, "port name should not parse as mode"

    def test_just_digits(self):
        assert parse_mode("9600") is None, "baud rate should not parse as mode"


# ── parse_open_args ─────────────────────────────────────────────────────────


class TestParseOpenArgs:

    def test_port_only(self):
        # Act
        port, baud, mode, err = parse_open_args("COM3")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert err is None, "no error expected"

    def test_port_and_baud(self):
        # Act
        port, baud, mode, err = parse_open_args("COM3 9600")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode is None, "mode should be None"
        assert err is None, "no error expected"

    def test_port_baud_mode(self):
        # Act
        port, baud, mode, err = parse_open_args("COM3 9600 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_port_and_mode_no_baud(self):
        # Act
        port, baud, mode, err = parse_open_args("COM3 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None when omitted"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_empty(self):
        # Act
        port, baud, mode, err = parse_open_args("")

        # Assert
        assert port is None, "port should be None"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert err is None, "no error for empty args"

    def test_linux_port(self):
        # Act
        port, baud, mode, err = parse_open_args("/dev/ttyUSB0 115200 E71")

        # Assert
        assert port == "/dev/ttyUSB0", "should handle Linux device paths"
        assert baud == 115200, "should extract baud rate"
        assert mode == ("E", 7, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_duplicate_baud_error(self):
        # Act
        _, _, _, err = parse_open_args("COM3 9600 115200")

        # Assert
        assert err is not None, "should error on duplicate baud rate"
        assert "Duplicate baud" in err, "error should mention duplicate baud"

    def test_duplicate_mode_error(self):
        # Act
        _, _, _, err = parse_open_args("COM3 N81 E71")

        # Assert
        assert err is not None, "should error on duplicate mode"
        assert "Duplicate mode" in err, "error should mention duplicate mode"

    def test_duplicate_port_error(self):
        # Act
        _, _, _, err = parse_open_args("COM3 COM4")

        # Assert
        assert err is not None, "should error on duplicate port name"
        assert "Unexpected" in err, "error should mention unexpected argument"


# ── set_mode ────────────────────────────────────────────────────────────────


class TestSetMode:

    def test_show_current_disconnected(self):
        # Act
        msgs, _ = set_mode(None, _cfg(), "")

        # Assert
        texts = " ".join(t for t, _ in msgs)
        assert "115200" in texts, "should show current baud rate"
        assert "8N1" in texts, "should show current mode"
        assert "disconnected" in texts, "should show disconnected"

    def test_show_current_connected(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "")

        # Assert
        texts = " ".join(t for t, _ in msgs)
        assert "115200" in texts, "should show current baud rate"
        assert "disconnected" not in texts, "should not show disconnected"

    def test_set_mode_only(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = set_mode(ser, _cfg(), "E71")

        # Assert
        assert ser.parity == "E", "parity should be set to E"
        assert ser.bytesize == 7, "bytesize should be set to 7"
        assert ser.stopbits == 1.0, "stopbits should be set to 1"
        assert effects["cfg_update"]["parity"] == "E", "cfg_update parity should be E"
        assert effects["cfg_update"]["byte_size"] == 7, "cfg_update byte_size should be 7"
        assert effects["cfg_update"]["stop_bits"] == 1.0, "cfg_update stop_bits should be 1.0"

    def test_set_baud_only(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = set_mode(ser, _cfg(), "9600")

        # Assert
        assert ser.baudrate == 9600, "baudrate should be set to 9600"
        assert effects["cfg_update"]["baud_rate"] == 9600, "cfg_update baud_rate should be 9600"
        assert "parity" not in effects["cfg_update"], "should not touch parity when only baud set"

    def test_set_baud_and_mode(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = set_mode(ser, _cfg(), "9600 N81")

        # Assert
        assert ser.baudrate == 9600, "baudrate should be set to 9600"
        assert ser.parity == "N", "parity should be set to N"
        assert ser.bytesize == 8, "bytesize should be set to 8"
        assert ser.stopbits == 1.0, "stopbits should be set to 1"
        text = msgs[0][0]
        assert "9600" in text, "summary should include baud rate"
        assert "8N1" in text, "summary should include mode"

    def test_disconnected(self):
        # Act
        msgs, _ = set_mode(None, _cfg(), "9600 N81")

        # Assert
        assert any("Not connected" in t for t, _ in msgs), "should warn not connected"

    def test_invalid_token(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "garbage")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid token"

    def test_summary_format(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "9600 O81.5")

        # Assert
        text = msgs[0][0]
        assert "Mode -> 9600 8O1.5" in text, "summary should show baud and frame"


class TestCanonicalManufacturer:
    """Display-only short-alias lookup for USB manufacturer strings.

    The raw descriptor varies between driver versions and OSes; the
    canonicalizer folds all the variants to a compact label suitable
    for a narrow column.  These tests guard both the folding rules
    and the "unknown passes through" contract.
    """

    def test_empty_returns_empty(self):
        # Act / Assert
        assert canonical_manufacturer("") == "", "empty string returns empty"
        assert canonical_manufacturer(None) == "", "None returns empty"

    def test_case_insensitive(self):
        # Act / Assert -- same alias regardless of case
        assert canonical_manufacturer("FTDI") == "FTDI", "upper case"
        assert canonical_manufacturer("ftdi") == "FTDI", "lower case"
        assert canonical_manufacturer("Ftdi") == "FTDI", "mixed case"

    def test_ftdi_variants(self):
        # Act / Assert -- the long descriptor also maps to FTDI
        actual = canonical_manufacturer("Future Technology Devices International")
        assert actual == "FTDI", "FTDI long form collapses"

    def test_microsoft_variants(self):
        # Act / Assert -- short and long forms both become MSFT
        assert canonical_manufacturer("Microsoft") == "MSFT", "Microsoft short"
        assert canonical_manufacturer("Microsoft Corporation") == "MSFT", \
            "Microsoft long"

    def test_silabs_variants(self):
        # Act / Assert -- both the short and long forms collapse
        assert canonical_manufacturer("Silicon Labs") == "SiLabs", "short form"
        assert canonical_manufacturer("Silicon Laboratories") == "SiLabs", \
            "long form"

    def test_wch_variants(self):
        # Act / Assert -- two distinct reported strings, same alias
        assert canonical_manufacturer("WCH.CN") == "WCH", "WCH short"
        assert canonical_manufacturer("QinHeng Electronics") == "WCH", \
            "QinHeng becomes WCH"

    def test_distinct_brands_not_merged(self):
        """Cypress and Infineon are the same company but report separately.

        Guards against a well-meaning refactor that would collapse them
        into a single alias.  If the chip self-identifies as Cypress,
        the user sees Cypress.
        """
        # Act / Assert
        assert canonical_manufacturer("Cypress") == "Cypress", \
            "Cypress stays Cypress"
        assert canonical_manufacturer("Infineon") == "Infineon", \
            "Infineon stays Infineon"
        # Same rule for Atmel vs Microchip
        assert canonical_manufacturer("Atmel") == "Atmel", "Atmel stays Atmel"
        assert canonical_manufacturer("Microchip Technology") == "Microchip", \
            "Microchip stays Microchip"

    def test_unknown_passes_through(self):
        # Act / Assert -- unrecognized vendors are returned verbatim
        assert canonical_manufacturer("Acme Corp") == "Acme Corp", \
            "unknown vendor unchanged"

    def test_whitespace_stripped(self):
        # Act / Assert -- leading/trailing whitespace doesn't break match
        assert canonical_manufacturer("  FTDI  ") == "FTDI", \
            "whitespace tolerated"

    def test_windows_standard_port_types_empty(self):
        """Windows built-in COM ports get blanked (not interesting)."""
        # Act / Assert
        assert canonical_manufacturer("(Standard port types)") == "", \
            "Windows generic reports as empty"

    def test_every_alias_is_short(self):
        """All canonical aliases fit in a ~9-char column.

        Guards the display contract.  If a future alias addition
        exceeds this width, the port-picker column will truncate
        (which defeats the point of the canonicalization).
        """
        # Act / Assert
        long_aliases = [a for _, a in MANUFACTURER_ALIASES if len(a) > 9]
        assert long_aliases == [], \
            f"all aliases must be <= 9 chars; too long: {long_aliases}"
