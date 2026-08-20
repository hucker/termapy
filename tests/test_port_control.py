"""Tests for port_control.py - pure serial port control functions."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from termapy.port_control import (
    PORT_PROPS,
    SERIAL_KEYS,
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
    """Create a minimal config dict in the v22 nested-serial shape.

    Pyserial keys (port/baud_rate/byte_size/parity/stop_bits/flow_control)
    nest under cfg["serial"].  Caller can override flat top-level keys
    (encoding, etc.) directly, or nested serial keys via ``serial=`` dict.
    """
    defaults = {
        "serial": {
            "port": "COM4",
            "baud_rate": 115200,
            "custom_baud": False,
            "byte_size": 8,
            "parity": "N",
            "stop_bits": 1,
            "flow_control": "none",
        },
        "encoding": "utf-8",
    }
    serial_override = overrides.pop("serial", None)
    if serial_override:
        defaults["serial"].update(serial_override)
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
        texts = [text for text, _ in msgs]
        assert any("disconnected" in text for text in texts), "should show disconnected state"
        assert any("115200" in text for text in texts), "should show baud rate config value"

    def test_connected_shows_hw_lines(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = port_info(_cfg(), ser)

        # Assert - shows hardware line values
        texts = [text for text, _ in msgs]
        assert any("connected" in text for text in texts), "should show connected state"
        assert any("DTR" in text for text in texts), "should show DTR line"
        assert any("RTS" in text for text in texts), "should show RTS line"


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

    def test_databits_first_8n1(self):
        # Act -- the conventional order (data bits first)
        actual = parse_mode("8N1")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, (
            f"8N1 (data-bits-first) must parse like N81, got {actual}"
        )

    def test_databits_first_fractional_stop(self):
        # Act
        actual = parse_mode("7E1.5")

        # Assert
        expected = ("E", 7, 1.5)
        assert actual == expected, f"7E1.5 parses to {expected}, got {actual}"


# ── parse_open_args ─────────────────────────────────────────────────────────


class TestParseOpenArgs:

    def test_port_only(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("COM3")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert le is None, "line_ending should be None"
        assert echo is None, "echo should be None"
        assert err is None, "no error expected"

    def test_8n1_first_token_is_mode_not_port(self):
        # 8N1 now parses as a mode; before, the unrecognized "8N1" was
        # treated as a port name -> "Cannot open 8N1".
        port, baud, mode, le, echo, err = parse_open_args("8N1")

        assert err is None, "8N1 is a valid mode, not an error"
        assert mode == ("N", 8, 1.0), f"8N1 -> mode, got {mode}"
        assert port is None, "8N1 must not be treated as a port name"

    def test_port_equals_forces_numeric_serial(self):
        # port= is the explicit escape hatch for a numeric SN that a bare
        # token would otherwise read as a baud rate.
        port, baud, mode, le, echo, err = parse_open_args("port=0001 9600")

        assert err is None, "port=0001 9600 parses cleanly"
        assert port == "0001", f"port= forces the value as the port, got {port!r}"
        assert baud == 9600, f"the bare 9600 is still baud, got {baud}"

    def test_bare_numeric_still_baud(self):
        # Unchanged: a bare numeric first token is baud (use port= for an SN).
        port, baud, _, _, _, err = parse_open_args("0001")

        assert err is None, "bare 0001 parses as baud"
        assert port is None and baud == 1, (
            f"bare numeric is baud, got port={port} baud={baud}"
        )

    def test_port_equals_requires_value(self):
        *_, err = parse_open_args("port=")
        assert err and "value" in err, f"empty port= is an error, got {err!r}"

    def test_duplicate_port_rejected(self):
        *_, err = parse_open_args("COM3 port=0001")
        assert err and "Duplicate port" in err, f"two ports is an error, got {err!r}"

    def test_port_and_baud(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("COM3 9600")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode is None, "mode should be None"
        assert err is None, "no error expected"

    def test_port_baud_mode(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("COM3 9600 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_port_and_mode_no_baud(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("COM3 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None when omitted"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_empty(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("")

        # Assert
        assert port is None, "port should be None"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert le is None, "line_ending should be None"
        assert echo is None, "echo should be None"
        assert err is None, "no error for empty args"

    def test_linux_port(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args(
            "/dev/ttyUSB0 115200 E71"
        )

        # Assert
        assert port == "/dev/ttyUSB0", "should handle Linux device paths"
        assert baud == 115200, "should extract baud rate"
        assert mode == ("E", 7, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_duplicate_baud_error(self):
        # Act
        _, _, _, _, _, err = parse_open_args("COM3 9600 115200")

        # Assert
        assert err is not None, "should error on duplicate baud rate"
        assert "Duplicate baud" in err, "error should mention duplicate baud"

    def test_duplicate_mode_error(self):
        # Act
        _, _, _, _, _, err = parse_open_args("COM3 N81 E71")

        # Assert
        assert err is not None, "should error on duplicate mode"
        assert "Duplicate mode" in err, "error should mention duplicate mode"

    def test_duplicate_port_error(self):
        # Act -- COM4 is not a line-ending / echo / mode / baud token,
        # and it isn't first, so the port-first enforcement rejects it
        # as "unexpected".
        _, _, _, _, _, err = parse_open_args("COM3 COM4")

        # Assert
        assert err is not None, "should error on duplicate port name"
        assert "Unexpected" in err, "error should mention unexpected argument"

    # -- Line ending tokens ------------------------------------------------

    def test_line_ending_cr(self):
        # Act
        port, baud, mode, le, echo, err = parse_open_args("COM3 cr")

        # Assert
        assert le == "\r", "cr -> carriage return"
        assert err is None, "no error"

    def test_line_ending_lf(self):
        # Act
        _, _, _, le, _, err = parse_open_args("COM3 lf")

        # Assert
        assert le == "\n", "lf -> newline"
        assert err is None, "no error"

    def test_line_ending_crlf(self):
        # Act
        _, _, _, le, _, err = parse_open_args("COM3 crlf")

        # Assert
        assert le == "\r\n", "crlf -> carriage return + newline"
        assert err is None, "no error"

    def test_line_ending_case_insensitive(self):
        # Act -- uppercase/mixed should also match.
        _, _, _, le, _, err = parse_open_args("COM3 CRLF")

        # Assert
        assert le == "\r\n", "line ending match is case-insensitive"
        assert err is None, "no error"

    def test_duplicate_line_ending(self):
        # Act
        _, _, _, _, _, err = parse_open_args("COM3 cr lf")

        # Assert
        assert err is not None, "two line endings should error"
        assert "Duplicate line ending" in err, (
            f"error names the duplicate; got {err!r}"
        )

    # -- Echo tokens -------------------------------------------------------

    def test_echo_on(self):
        # Act
        _, _, _, _, echo, err = parse_open_args("COM3 echo")

        # Assert
        assert echo is True, "echo token -> True"
        assert err is None, "no error"

    def test_noecho(self):
        # Act
        _, _, _, _, echo, err = parse_open_args("COM3 noecho")

        # Assert
        assert echo is False, "noecho token -> False"
        assert err is None, "no error"

    def test_duplicate_echo(self):
        # Act
        _, _, _, _, _, err = parse_open_args("COM3 echo noecho")

        # Assert
        assert err is not None, "two echo tokens should error"
        assert "Duplicate echo" in err, (
            f"error names the duplicate; got {err!r}"
        )

    # -- Order-independence after port-first -------------------------------

    def test_all_fields_any_order_after_port(self):
        # Act -- the user's example from the feature ask.
        port, baud, mode, le, echo, err = parse_open_args(
            "COM3 echo crlf 9600 N81"
        )

        # Assert
        assert port == "COM3", "port stays first"
        assert baud == 9600, "baud found"
        assert mode == ("N", 8, 1.0), "mode found"
        assert le == "\r\n", "line ending found"
        assert echo is True, "echo found"
        assert err is None, "no error"

    def test_no_port_still_accepts_other_fields(self):
        # Act -- user just wants to change baud + echo in-session.
        port, baud, mode, le, echo, err = parse_open_args("9600 echo")

        # Assert
        assert port is None, "no port provided"
        assert baud == 9600, "baud parsed"
        assert echo is True, "echo parsed"
        assert err is None, "no error"

    # -- Port-first enforcement -------------------------------------------

    def test_port_must_be_first(self):
        # Act -- putting a port-name-looking token after a classifier
        # should be rejected.  Previously (position-independent) this
        # would have been accepted as "port=COM3 with echo first".
        _, _, _, _, _, err = parse_open_args("echo COM3")

        # Assert
        assert err is not None, (
            "port-name-looking token after other fields must error"
        )
        assert "Unexpected" in err, f"error phrasing; got {err!r}"


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


# -- Port spec resolution ---------------------------------------------------


import pytest  # noqa: E402

from termapy.port_control import (  # noqa: E402
    MATCH_LITERAL,
    MATCH_RESERVED,
    MATCH_SERIAL,
    MATCH_URL,
    AmbiguousSerialNumberError,
    ChipFacts,
    _build_demo_fleet,
    _gather_all_chip_facts,
    _windows_location_chain,
    chip_field,
    chip_info,
    gather_chip_facts,
    parse_location_paths,
    resolve_port,
    resolve_port_trace,
)


class TestResolvePort:
    """resolve_port() translates a port spec into a concrete device name.

    These tests hand in ``_build_demo_fleet`` -- the same deterministic
    three-port roster the ``TERMAPY_DEMO_FLEET`` variable installs (FTDI
    COM3 SN A1B2C3D4, Silicon Labs COM4 SN 0001, Microsoft COM7 SN
    020026702RYN040952) -- so the fleet is visible in each call rather
    than armed by an environment the test has to remember to set.
    """

    def test_literal_device_name_unchanged(self):
        # Act
        actual = resolve_port("COM3", source=_build_demo_fleet)

        # Assert
        assert actual == "COM3", "literal device match returns that device"

    def test_serial_number_resolves_to_device(self):
        # Act
        actual = resolve_port("A1B2C3D4", source=_build_demo_fleet)

        # Assert
        expected = "COM3"
        assert actual == expected, \
            f"SN A1B2C3D4 should resolve to {expected}, got {actual}"

    def test_serial_number_case_insensitive(self):
        # Act
        actual = resolve_port("a1b2c3d4", source=_build_demo_fleet)

        # Assert
        expected = "COM3"
        assert actual == expected, \
            f"SN match must be case-insensitive; got {actual}"

    def test_pipe_fallback_first_candidate_wins(self):
        # Act -- SN resolves, COM7 fallback never tried
        actual = resolve_port("A1B2C3D4|COM7", source=_build_demo_fleet)

        # Assert -- note fleet has COM7 so COM7 would also resolve;
        # test still proves order by checking we got the SN match, not COM7
        expected = "COM3"
        assert actual == expected, \
            f"first resolving candidate wins; got {actual}"

    def test_pipe_fallback_second_wins_when_first_missing(self):
        # Act
        actual = resolve_port("BOGUS_NO_MATCH|COM4", source=_build_demo_fleet)

        # Assert
        expected = "COM4"
        assert actual == expected, \
            f"literal fallback wins when SN not found; got {actual}"

    def test_reserved_demo_passes_through(self):
        # Act -- DEMO bypasses enumeration even when DEMO_FLEET is set
        actual = resolve_port("DEMO", source=_build_demo_fleet)

        # Assert
        assert actual == "DEMO", "DEMO is a reserved name"

    def test_reserved_demo_fail_passes_through(self):
        # Act
        actual = resolve_port("DEMO_FAIL", source=_build_demo_fleet)

        # Assert
        assert actual == "DEMO_FAIL", "DEMO_FAIL is a reserved name"

    def test_pyserial_url_passes_through(self):
        # Act
        actual = resolve_port("rfc2217://host:2217", source=_build_demo_fleet)

        # Assert
        assert actual == "rfc2217://host:2217", "URLs are passed through"

    def test_all_candidates_missing_returns_last(self):
        # Act
        actual = resolve_port("NOPE1|NOPE2|NOPE3", source=_build_demo_fleet)

        # Assert -- last candidate is what ``open_serial()`` will show
        # in its "Cannot open <X>" error, which is what the user most
        # explicitly asked for.
        expected = "NOPE3"
        assert actual == expected, \
            f"last candidate returned on total miss; got {actual}"

    def test_ambiguous_sn_raises(self):
        # Arrange -- a fleet with two devices sharing the same SN "0001"
        # (a common burn-in on cheap CP2102 / CH340 clones), handed in
        # through source= rather than patched over the gather function.
        def _ambiguous_fleet():
            return [
                ChipFacts(
                    device="COM3", manufacturer="CH340", serial="0001",
                    vid_pid="1A86:7523",
                ),
                ChipFacts(
                    device="COM7", manufacturer="CH340", serial="0001",
                    vid_pid="1A86:7523",
                ),
            ]

        # Act + Assert
        with pytest.raises(AmbiguousSerialNumberError) as exc:
            resolve_port("0001", source=_ambiguous_fleet)
        assert exc.value.matches == ["COM3", "COM7"], \
            "both colliding devices named in exception"
        assert "0001" in str(exc.value), "SN named in exception message"
        assert "COM3" in str(exc.value) and "COM7" in str(exc.value), \
            "both devices mentioned in exception message"


class TestChipInfoResolvesSnSpec:
    """R2607-03: /port.chip and /port.chip.<field> must resolve an SN /
    fallback spec (like /port.info does) before the literal-device lookup.

    gather_chip_facts matches literal device names only, so without
    resolution these failed with "No port matching" under an SN-based
    config -- even while connected.  Uses the demo fleet (COM3 SN
    A1B2C3D4, an FTDI FT232R), handed in rather than set in the
    environment.
    """

    def test_chip_info_resolves_serial_number(self):
        # Arrange -- SN-based config: current_port is the SN, nothing typed.
        # Act
        msgs, _ = chip_info("", "A1B2C3D4", connected_port="COM3", source=_build_demo_fleet)

        # Assert
        texts = [text for text, _ in msgs]
        assert not any("No port matching" in text for text in texts), (
            "chip_info must resolve the SN, not report no match"
        )
        assert any("COM3" in text for text in texts), (
            "resolves the SN to its device (COM3) and dumps its facts"
        )

    def test_chip_field_resolves_serial_number(self):
        # Act -- /port.chip.model under an SN config.
        msgs, _ = chip_field("model", "", "A1B2C3D4", connected_port="COM3", source=_build_demo_fleet)

        # Assert
        texts = [text for text, _ in msgs]
        assert not any("No port matching" in text for text in texts), (
            "chip_field must resolve the SN, not report no match"
        )
        assert any("FT232R" in text or "FTDI" in text for text in texts), (
            f"returns the resolved device's model field, got {texts}"
        )


class TestResolvePortTrace:
    """resolve_port_trace() builds a per-candidate diagnostic trace.

    Runs against the demo roster, handed in through ``source=``.
    """

    def test_single_candidate_literal_match(self):
        # Act
        actual = resolve_port_trace("COM3", source=_build_demo_fleet)

        # Assert
        expected = [("COM3", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_sn_match(self):
        # Act
        actual = resolve_port_trace("A1B2C3D4", source=_build_demo_fleet)

        # Assert
        expected = [("A1B2C3D4", MATCH_SERIAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_reserved(self):
        # Act
        actual = resolve_port_trace("DEMO", source=_build_demo_fleet)

        # Assert
        expected = [("DEMO", MATCH_RESERVED)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_url(self):
        # Act
        actual = resolve_port_trace("rfc2217://host:2217", source=_build_demo_fleet)

        # Assert
        expected = [("rfc2217://host:2217", MATCH_URL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_with_first_miss(self):
        # Act
        actual = resolve_port_trace("BOGUS|COM4", source=_build_demo_fleet)

        # Assert -- None marks the miss so the caller can say
        # "BOGUS: not found" in the error message.
        expected = [("BOGUS", None), ("COM4", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_all_miss(self):
        # Act
        actual = resolve_port_trace("NOPE1|NOPE2", source=_build_demo_fleet)

        # Assert
        expected = [("NOPE1", None), ("NOPE2", None)]
        assert actual == expected, f"got {actual}"

    def test_ambiguous_sn_reported_not_raised(self):
        # Arrange -- same duplicate-SN fleet as the raises test.
        # trace()'s contract is to NEVER raise, so the caller can build
        # one coherent error message even when one of multiple
        # candidates is ambiguous.
        def _ambiguous_fleet():
            return [
                ChipFacts(device="COM3", serial="0001"),
                ChipFacts(device="COM7", serial="0001"),
            ]

        # Act
        actual = resolve_port_trace("0001|COM7", source=_ambiguous_fleet)

        # Assert -- ambiguous first, then literal fallback.
        expected = [("0001", "ambiguous"), ("COM7", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"


class TestPortSourceLayers:
    """Where ports come from: injection, then the environment, then hardware.

    The environment layer is deliberate -- the party that wants fake ports
    is usually not the calling code (a CI job, a screenshot run, a docs
    build), and injection alone cannot fake ports underneath a program you
    do not control.  ``trust_env`` is what keeps it honest: an env var is
    process-global, so a caller that needs determinism opts out instead of
    manipulating global state.
    """

    def _fleet(self):
        """Two ports that could not be mistaken for the demo fleet."""
        return [
            ChipFacts(device="COM9", serial="INJECTED-9", manufacturer="Acme"),
            ChipFacts(device="COM8", serial="INJECTED-8", manufacturer="Acme"),
        ]

    def test_an_injected_source_is_used(self):
        # Act -- no env var, no hardware
        actual = _gather_all_chip_facts(source=self._fleet)

        # Assert
        devices = [f.device for f in actual]
        assert devices == ["COM8", "COM9"], (
            f"the injected fleet is returned, sorted by device; got {devices}"
        )

    def test_injection_wins_over_the_environment(self, monkeypatch):
        # Arrange -- both layers armed at once
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

        # Act
        actual = _gather_all_chip_facts(source=self._fleet)

        # Assert
        devices = [f.device for f in actual]
        assert devices == ["COM8", "COM9"], (
            "an explicit source outranks the environment, so a test never "
            f"has to unset the variable first; got {devices}"
        )

    def test_the_environment_is_honored_by_default(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

        # Act -- no source: the operator's variable decides
        actual = _gather_all_chip_facts()

        # Assert
        devices = [f.device for f in actual]
        assert devices == ["COM3", "COM4", "COM7"], (
            f"the demo fleet still works with no code change; got {devices}"
        )

    def test_trust_env_false_ignores_the_variable(self, monkeypatch):
        # Arrange -- the variable is set, but this caller wants determinism
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

        # Act -- real enumeration; what it finds depends on the machine, so
        # assert only that the SYNTHETIC record is not what came back.
        actual = gather_chip_facts("COM3", trust_env=False)

        # Assert
        serial = actual.serial if actual else None
        assert serial != "A1B2C3D4", (
            "trust_env=False must reach real hardware, not the fleet the "
            "environment is offering"
        )

    def test_a_named_port_missing_from_a_fleet_is_not_invented(self):
        # Act -- a substitute fleet is authoritative: no reserved-name
        # fallback behind its back.
        actual = gather_chip_facts("DEMO", source=self._fleet)

        # Assert
        assert actual is None, (
            "a fleet that does not list the port means the port is absent, "
            "not that a synthetic record should be conjured"
        )

    def test_resolution_runs_against_an_injected_fleet(self):
        """The payoff: resolve by serial number with no hardware, no env var."""
        # Act
        actual = resolve_port("INJECTED-9", source=self._fleet)

        # Assert
        assert actual == "COM9", (
            f"SN INJECTED-9 should resolve to COM9, got {actual}"
        )

    def test_the_trace_reports_how_an_injected_candidate_matched(self):
        # Act
        actual = resolve_port_trace("INJECTED-8|COM9", source=self._fleet)

        # Assert
        assert actual == [("INJECTED-8", MATCH_SERIAL), ("COM9", MATCH_LITERAL)], (
            f"both candidates resolve against the injected fleet; got {actual}"
        )

    def test_ambiguity_still_raises_against_an_injected_fleet(self):
        # Arrange -- two ports burned with the same serial number
        def twins():
            return [
                ChipFacts(device="COM8", serial="SAME"),
                ChipFacts(device="COM9", serial="SAME"),
            ]

        # Act / Assert -- the safety rule is a property of resolution, not
        # of where the ports came from.
        with pytest.raises(AmbiguousSerialNumberError):
            resolve_port("SAME", source=twins)


class TestParseLocationPaths:
    """The Windows LOCATION_PATHS -> bus-port chain rule.

    Pure string work, so it is tested directly and on every platform.
    The cfgmgr32 devnode walk that feeds it is thin glue over three OS
    calls and is exercised live rather than here; what is worth pinning
    is that termapy's spelling of a location matches pyserial's, since
    the whole point of deriving one for FTDI ports is that the LOCATION
    column reads as one notation.
    """

    def test_a_hub_port_becomes_a_dotted_chain(self):
        # Arrange -- a real path from an FTDI adapter's parent USB node.
        paths = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)"

        # Act
        actual = parse_location_paths(paths)

        # Assert
        assert actual == "1-8.3", (
            f"bus 1, hub port 8, device port 3; got {actual!r}"
        )

    def test_a_port_directly_on_the_root_hub_has_no_dot(self):
        # Act -- one hop: the separator is '-', and '.' never appears.
        actual = parse_location_paths("PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)")

        # Assert
        assert actual == "1-8", f"single hop keeps the dash form; got {actual!r}"

    def test_each_further_hop_adds_a_dot(self):
        # Act -- a hub behind a hub behind a hub.
        actual = parse_location_paths(
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(4)#USB(2)"
        )

        # Assert
        assert actual == "1-8.4.2", f"one dot per extra tier; got {actual!r}"

    def test_the_bus_is_numbered_from_one(self):
        # Act -- USBROOT is 0-based in the registry, 1-based on display.
        # This is pyserial's convention, and matching it is the point.
        actual = parse_location_paths("PCIROOT(0)#USBROOT(1)#USB(2)")

        # Assert
        assert actual == "2-2", f"USBROOT(1) is bus 2; got {actual!r}"

    def test_a_non_usb_path_has_no_chain(self):
        # Act -- a PCI serial card names no USB hop.
        actual = parse_location_paths("PCIROOT(0)#PCI(1C00)#PCI(0000)")

        # Assert
        assert actual is None, (
            f"None, not an empty string, so callers can fall back; got {actual!r}"
        )

    def test_an_empty_path_has_no_chain(self):
        # Act
        actual = parse_location_paths("")

        # Assert
        assert actual is None, f"nothing in, nothing out; got {actual!r}"


class TestWindowsLocationChain:
    """The devnode walk is best-effort and must never raise."""

    def test_an_unknown_device_yields_no_location(self):
        # Act -- no such devnode.  Off Windows there are no bindings at
        # all, and the answer is the same: None, not an exception.
        actual = _windows_location_chain("NOSUCHDEVICE")

        # Assert
        assert actual is None, (
            f"enrichment must degrade to blank, never fail a lookup; got {actual!r}"
        )

    def test_a_composite_interface_becomes_the_suffix(self):
        # Arrange -- the real path from a composite debugger+CDC device,
        # whose COM port is interface 1.
        paths = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(4)#USBMI(1)"

        # Act
        actual = parse_location_paths(paths)

        # Assert -- same spelling pyserial produces for the same device.
        assert actual == "1-8.4:x.1", (
            f"interface 1 of the device at 1-8.4; got {actual!r}"
        )

    def test_channels_of_one_chip_are_told_apart(self):
        # Arrange -- an FT2232H's two channels share a parent USB node,
        # so the hop chain alone is identical for both.  Only the
        # interface separates them, which is the whole reason USBMI is
        # read rather than ignored.
        base = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)"

        # Act
        first = parse_location_paths(base + "#USBMI(0)")
        second = parse_location_paths(base + "#USBMI(1)")

        # Assert
        assert (first, second) == ("1-8.3:x.0", "1-8.3:x.1"), (
            f"two ports on one chip must not collide; got {first!r} and {second!r}"
        )

    def test_a_single_function_device_gets_no_suffix(self):
        # Act -- no USBMI token means nothing to disambiguate, and
        # pyserial omits the suffix in that case on every platform.
        actual = parse_location_paths(
            "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)"
        )

        # Assert
        assert actual == "1-8.3", f"no interface, no suffix; got {actual!r}"
