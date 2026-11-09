"""Tests for port_control.py - pure serial port control functions."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.port_control import (
    PORT_PROPS,
    SERIAL_KEYS,
    baud_facts,
    get_set_flow,
    get_set_hw_line,
    get_set_prop,
    mode_facts,
    parse_mode,
    parse_open_args,
    port_info,
    problem,
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
        """The keys that identify the serial connection itself are all in SERIAL_KEYS."""
        assert "port" in SERIAL_KEYS, "SERIAL_KEYS should contain 'port'"
        assert "baud_rate" in SERIAL_KEYS, "SERIAL_KEYS should contain 'baud_rate'"
        assert "flow_control" in SERIAL_KEYS, "SERIAL_KEYS should contain 'flow_control'"

    def test_port_props_has_all_keys(self):
        """Every framing property the port commands can get or set is listed in PORT_PROPS."""
        assert "baud_rate" in PORT_PROPS, "PORT_PROPS should contain 'baud_rate'"
        assert "byte_size" in PORT_PROPS, "PORT_PROPS should contain 'byte_size'"
        assert "parity" in PORT_PROPS, "PORT_PROPS should contain 'parity'"
        assert "stop_bits" in PORT_PROPS, "PORT_PROPS should contain 'stop_bits'"


# ── port_info ────────────────────────────────────────────────────────────────


class TestPortInfo:
    def test_disconnected(self):
        """With no open port, the info page falls back to the config values, says so, and has no signals."""
        # Act
        report = port_info(_cfg(), None)

        # Assert - config values with disconnected state, no live lines
        rows = {item[0]: item[1] for item in report.items if isinstance(item, tuple)}
        assert "disconnected" in rows["Port"], "the port row says disconnected"
        assert (report.data["connected"], report.data["baud_rate"]) == (False, 115200), (
            "config values as data"
        )
        assert report.data["signals"] is None, "no live lines without an open port"

    def test_connected_shows_hw_lines(self):
        """An open port adds its live hardware lines to the page and, as booleans, to the record."""
        # Arrange
        ser = _mock_ser()

        # Act
        report = port_info(_cfg(), ser)

        # Assert - hardware line rows and their values
        labels = [item[0] for item in report.items if isinstance(item, tuple)]
        assert {"DTR", "RTS"} <= set(labels), "the DTR and RTS rows"
        assert report.data["connected"] is True, "connected as a boolean"
        assert set(report.data["signals"]) == {"dtr", "rts", "cts", "dsr", "ri", "cd"}, (
            "all six lines"
        )


# ── get_set_prop ─────────────────────────────────────────────────────────────


class TestGetSetProp:
    def test_get_disconnected_shows_config(self):
        """A bare query while disconnected reports the configured value, marked disconnected."""
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "")

        # Assert
        assert any("115200" in t for t, _ in msgs), "should show config baud rate"
        assert any("disconnected" in t for t, _ in msgs), "should indicate disconnected"

    def test_get_connected_shows_live_value(self):
        """A bare query while connected reports the port's live value, not the config."""
        # Arrange
        ser = _mock_ser(baudrate=9600)

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "")

        # Assert
        assert any("9600" in t for t, _ in msgs), "should show live baud rate value"

    def test_set_valid_value(self):
        """Setting a property updates the port and asks for a cfg write and title refresh."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "9600")

        # Assert - value changed, side effects requested
        assert ser.baudrate == 9600, "baudrate should be updated to 9600"
        assert effects.get("update_title") is True, "should request title update"
        assert effects["cfg_update"]["baud_rate"] == 9600, "cfg_update should contain new baud_rate"

    def test_set_invalid_parity(self):
        """A bad parity letter errors without touching the saved config."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "parity", "X")

        # Assert - error message, no side effects
        assert any("red" == c for _, c in msgs), "should show red error message"
        assert not effects.get("cfg_update"), "should not produce cfg_update on invalid parity"

    def test_set_when_disconnected(self):
        """Setting a property needs an open port; disconnected it warns instead."""
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "9600")

        # Assert - not connected warning
        assert any(STATE_MESSAGES["serial_connected"] in t for t, _ in msgs), (
            "should warn not connected"
        )


# ── get_set_flow ─────────────────────────────────────────────────────────────


class TestGetSetFlow:
    def test_get_flow(self):
        """A bare flow query reports the configured mode even with no port open."""
        # Act
        msgs, _ = get_set_flow(None, _cfg(), "")

        # Assert
        assert any("none" in t for t, _ in msgs), "should show current flow control as 'none'"

    def test_set_valid_flow(self):
        """rtscts enables hardware flow on the port, saves it, and resyncs the line display."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_flow(ser, _cfg(), "rtscts")

        # Assert
        assert ser.rtscts is True, "rtscts should be enabled on serial object"
        assert effects.get("sync_hw") is True, "should request hw sync"
        assert effects["cfg_update"]["flow_control"] == "rtscts", (
            "cfg_update should set flow_control to rtscts"
        )

    def test_set_invalid_flow(self):
        """An unknown flow-control name is reported as an error."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = get_set_flow(ser, _cfg(), "invalid")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid flow"


# ── get_set_hw_line ──────────────────────────────────────────────────────────


class TestGetSetHwLine:
    def test_get_dtr(self):
        """A bare line query prints the asserted state as 1."""
        # Arrange
        ser = _mock_ser(dtr=True)

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs), "DTR=True should display as '1'"

    def test_set_dtr(self):
        """Setting DTR to 0 drops the line and asks the UI to resync line indicators."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_hw_line(ser, "dtr", "0")

        # Assert
        assert ser.dtr is False, "DTR should be set to False"
        assert effects.get("sync_hw") is True, "should request hw sync after DTR change"

    def test_invalid_value(self):
        """A token that is not a line level errors with the shared vocabulary instead of being read as low."""
        # Arrange
        ser = _mock_ser(dtr=True)

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "maybe")

        # Assert
        expected = ("Invalid DTR value: maybe (use on/off/true/false/yes/no/1/0/y/n/t/f)", "red")
        assert msgs == [expected], "one red line naming the line, the text and the vocabulary"
        assert ser.dtr is True, "a refused token leaves the line alone"

    def test_the_old_hardware_words_are_gone(self):
        """high and low were a second vocabulary for the same question; they are refused like any other word."""
        ser = _mock_ser(dtr=True)
        msgs, _ = get_set_hw_line(ser, "dtr", "low")
        assert msgs[0][1] == "red" and ser.dtr is True, "low is no longer a level"

    def test_disconnected(self):
        """Driving a hardware line needs an open port; disconnected it warns instead."""
        # Act
        msgs, _ = get_set_hw_line(None, "dtr", "1")

        # Assert
        assert any(STATE_MESSAGES["serial_connected"] in t for t, _ in msgs), (
            "should warn not connected"
        )


# ── read_signal ──────────────────────────────────────────────────────────────


class TestReadSignal:
    def test_read_cts(self):
        """An asserted input signal reads back as 1."""
        # Arrange
        ser = _mock_ser(cts=True)

        # Act
        msgs, _ = read_signal(ser, "cts", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs), "CTS=True should display as '1'"

    def test_read_only_rejects_value(self):
        """Input signals like CTS cannot be written; a value is refused as read-only."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = read_signal(ser, "cts", "1")

        # Assert
        assert any("read-only" in t for t, _ in msgs), "should reject write to read-only signal"

    def test_disconnected(self):
        """Reading an input signal needs an open port; disconnected it warns instead."""
        # Act
        msgs, _ = read_signal(None, "cts", "")

        # Assert
        assert any(STATE_MESSAGES["serial_connected"] in t for t, _ in msgs), (
            "should warn not connected"
        )


# ── send_break ───────────────────────────────────────────────────────────────


class TestSendBreak:
    def test_default_duration(self):
        """A bare break holds the line for 250 ms and reports that duration."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "")

        # Assert
        ser.send_break.assert_called_once_with(duration=0.25)
        assert any("250ms" in t for t, _ in msgs), "should report 250ms default duration"

    def test_custom_duration(self):
        """A break duration is given in milliseconds and passed to pyserial in seconds."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "100")

        # Assert
        ser.send_break.assert_called_once_with(duration=0.1)

    def test_invalid_duration(self):
        """A non-numeric duration errors and never sends a break."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "abc")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid duration"
        ser.send_break.assert_not_called()

    def test_zero_duration_invalid(self):
        """A zero-length break is rejected as an invalid duration."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "0")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for zero duration"

    def test_disconnected(self):
        """A break needs an open port; disconnected it warns instead."""
        # Act
        msgs, _ = send_break(None, "")

        # Assert
        assert any(STATE_MESSAGES["serial_connected"] in t for t, _ in msgs), (
            "should warn not connected"
        )


# ── parse_mode ──────────────────────────────────────────────────────────────


class TestParseMode:
    def test_n81(self):
        """The parity-first spelling yields (parity, data bits, stop bits) with float stops."""
        # Act
        actual = parse_mode("N81")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, "N81 should parse to (N, 8, 1.0)"

    def test_e71(self):
        """Even parity with 7 data bits parses in parity-first form."""
        # Act
        actual = parse_mode("E71")

        # Assert
        expected = ("E", 7, 1.0)
        assert actual == expected, "E71 should parse to (E, 7, 1.0)"

    def test_o81_5(self):
        """A fractional 1.5 stop-bit suffix is accepted in parity-first form."""
        # Act
        actual = parse_mode("O81.5")

        # Assert
        expected = ("O", 8, 1.5)
        assert actual == expected, "O81.5 should parse to (O, 8, 1.5)"

    def test_s52(self):
        """Space parity, 5 data bits and 2 stop bits cover the edge values of each field."""
        # Act
        actual = parse_mode("S52")

        # Assert
        expected = ("S", 5, 2.0)
        assert actual == expected, "S52 should parse to (S, 5, 2.0)"

    def test_lowercase(self):
        """Parity letters are case-insensitive and normalized to uppercase."""
        # Act
        actual = parse_mode("n81")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, "lowercase n81 should parse to (N, 8, 1.0)"

    def test_invalid_parity(self):
        """A letter outside the pyserial parity set makes the token not a mode."""
        assert parse_mode("X81") is None, "X is not a valid parity letter"

    def test_invalid_byte_size(self):
        """Data bits above 8 make the token not a mode."""
        assert parse_mode("N91") is None, "9 is not a valid byte size"

    def test_invalid_stop_bits(self):
        """Only 1, 1.5 and 2 stop bits are valid; 3 makes the token not a mode."""
        assert parse_mode("N83") is None, "3 is not a valid stop bits value"

    def test_empty(self):
        """An empty string is not a mode."""
        assert parse_mode("") is None, "empty string should return None"

    def test_garbage(self):
        """A port name is not mistaken for a mode, so arg classification stays unambiguous."""
        assert parse_mode("COM3") is None, "port name should not parse as mode"

    def test_just_digits(self):
        """An all-digit token is left for baud, never read as a mode."""
        assert parse_mode("9600") is None, "baud rate should not parse as mode"

    def test_databits_first_8n1(self):
        """The conventional data-bits-first spelling parses the same as parity-first."""
        # Act -- the conventional order (data bits first)
        actual = parse_mode("8N1")

        # Assert
        expected = ("N", 8, 1.0)
        assert actual == expected, f"8N1 (data-bits-first) must parse like N81, got {actual}"

    def test_databits_first_fractional_stop(self):
        """Data-bits-first form also accepts a fractional 1.5 stop-bit suffix."""
        # Act
        actual = parse_mode("7E1.5")

        # Assert
        expected = ("E", 7, 1.5)
        assert actual == expected, f"7E1.5 parses to {expected}, got {actual}"


# ── parse_open_args ─────────────────────────────────────────────────────────


class TestParseOpenArgs:
    def test_port_only(self):
        """A lone port name sets only the port; every other field stays None."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("COM3")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert le is None, "line_ending should be None"
        assert echo is None, "echo should be None"
        assert err is None, "no error expected"

    def test_8n1_first_token_is_mode_not_port(self):
        """A leading 8N1 is classified as a mode, not opened as a port named 8N1."""
        # 8N1 now parses as a mode; before, the unrecognized "8N1" was
        # treated as a port name -> "Cannot open 8N1".
        port, baud, mode, le, echo, _, err = parse_open_args("8N1")

        assert err is None, "8N1 is a valid mode, not an error"
        assert mode == ("N", 8, 1.0), f"8N1 -> mode, got {mode}"
        assert port is None, "8N1 must not be treated as a port name"

    def test_port_equals_forces_numeric_serial(self):
        """port= is the escape hatch for a numeric serial number that would read as baud."""
        # port= is the explicit escape hatch for a numeric SN that a bare
        # token would otherwise read as a baud rate.
        port, baud, mode, le, echo, _, err = parse_open_args("port=0001 9600")

        assert err is None, "port=0001 9600 parses cleanly"
        assert port == "0001", f"port= forces the value as the port, got {port!r}"
        assert baud == 9600, f"the bare 9600 is still baud, got {baud}"

    def test_bare_numeric_still_baud(self):
        """A bare numeric first token is read as a baud rate, so an SN typed without port= is refused as one."""
        # Act
        *_, err = parse_open_args("0001")

        # Assert
        assert err == "Invalid baud rate: 1 (not a standard rate: add custom=true to allow it)", (
            "a rate of 1 is refused rather than opened; port=0001 names the SN"
        )


@pytest.mark.rule("baud-standard-unless-custom")
class TestBaudStandardUnlessCustom:
    """A typed rate off the standard list needs custom=true."""

    def test_a_mistyped_rate_is_refused(self):
        """11520 (a 115200 missing a digit) is refused, and the error says how to ask for it."""
        *_, err = parse_open_args("COM3 11520")
        assert err == "Invalid baud rate: 11520 (not a standard rate: add custom=true to allow it)"

    def test_custom_true_allows_any_rate_from_300(self):
        """custom=true takes a rate off the list, anywhere on the line, and says it was asked for."""
        # Act
        port, baud, _, _, _, custom, err = parse_open_args("COM3 custom=true 250000")

        # Assert
        assert err is None, err
        assert (port, baud, custom) == ("COM3", 250000, True)

    def test_custom_reads_the_boolean_vocabulary(self):
        """custom= takes any parse_bool token and refuses anything else, never reading it as false."""
        # Act
        *_, custom_yes, err_yes = parse_open_args("COM3 250000 custom=yes")
        *_, err_bad = parse_open_args("COM3 250000 custom=maybe")

        # Assert
        assert (custom_yes, err_yes) == (True, None), "yes is true"
        assert err_bad is not None and err_bad.startswith("Invalid custom: maybe"), err_bad

    def test_custom_still_requires_300(self):
        """A custom rate under 300 is refused: no adapter runs that slow on purpose."""
        *_, err = parse_open_args("COM3 150 custom=true")
        assert err == "Invalid baud rate: 150 (custom baud requires >= 300)"

    def test_a_standard_rate_needs_nothing(self):
        """A standard rate parses as before, with custom false."""
        _, baud, _, _, _, custom, err = parse_open_args("COM3 115200")
        assert (baud, custom, err) == (115200, False, None)

    def test_custom_twice_is_refused(self):
        """Two custom= tokens are one too many, whatever they say."""
        *_, err = parse_open_args("COM3 250000 custom=true custom=false")
        assert err is not None and err.startswith("Duplicate custom"), err

    def test_mode_refuses_a_mistyped_rate_and_takes_a_custom_one(self):
        """/port.mode: 11520 is refused before the port is touched; custom=true sets the rate and custom_baud."""
        # Arrange
        port = _mock_ser()

        # Act
        refused = set_mode(port, _cfg(), "11520 8N1")
        accepted = set_mode(port, _cfg(), "250000 8N1 custom=true")

        # Assert
        assert problem(refused) == (
            "Invalid baud rate: 11520 (not a standard rate: add custom=true to allow it)"
        )
        assert problem(accepted) is None, accepted
        assert port.baudrate == 250000, "the custom rate reached the port"
        assert accepted[1]["cfg_update"]["custom_baud"] is True, "the session records it as custom"

    def test_baud_rate_refuses_a_mistyped_rate_and_takes_a_custom_one(self):
        """/port.baud_rate: 9460 is refused; 9460 custom=true sets it and custom_baud."""
        # Arrange
        port = _mock_ser()

        # Act
        refused = get_set_prop(port, _cfg(), "baud_rate", "9460")
        accepted = get_set_prop(port, _cfg(), "baud_rate", "9460 custom=true")

        # Assert
        assert problem(refused) == (
            "Invalid baud rate: 9460 (not a standard rate: add custom=true to allow it)"
        )
        assert port.baudrate == 9460, "the custom rate reached the port"
        assert accepted[1]["cfg_update"] == {"baud_rate": 9460, "custom_baud": True}

    def test_baud_rate_standard_leaves_custom_baud_alone(self):
        """A standard rate sets only the rate: custom_baud is not touched."""
        port = _mock_ser()
        result = get_set_prop(port, _cfg(), "baud_rate", "9600")
        assert result[1]["cfg_update"] == {"baud_rate": 9600}

    def test_only_the_baud_rate_takes_custom(self):
        """custom= belongs to the rate: on another property it is an invalid value, never ignored."""
        port = _mock_ser()
        result = get_set_prop(port, _cfg(), "byte_size", "8 custom=true")
        assert problem(result) is not None, "custom= on the data bits is refused"

    def test_port_equals_requires_value(self):
        """An empty port= is an error rather than clearing the port."""
        *_, err = parse_open_args("port=")
        assert err and "value" in err, f"empty port= is an error, got {err!r}"

    def test_duplicate_port_rejected(self):
        """A positional port plus port= is two ports and is refused."""
        *_, err = parse_open_args("COM3 port=0001")
        assert err and "Duplicate port" in err, f"two ports is an error, got {err!r}"

    def test_port_and_baud(self):
        """A port followed by a number yields port and baud with no mode."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("COM3 9600")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode is None, "mode should be None"
        assert err is None, "no error expected"

    def test_port_baud_mode(self):
        """Port, baud and mode in order all classify correctly."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("COM3 9600 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud == 9600, "should extract baud rate"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_port_and_mode_no_baud(self):
        """Baud may be omitted between port and mode."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("COM3 N81")

        # Assert
        assert port == "COM3", "should extract port name"
        assert baud is None, "baud should be None when omitted"
        assert mode == ("N", 8, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_empty(self):
        """No args means no fields and no error, so a bare open reuses the config."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("")

        # Assert
        assert port is None, "port should be None"
        assert baud is None, "baud should be None"
        assert mode is None, "mode should be None"
        assert le is None, "line_ending should be None"
        assert echo is None, "echo should be None"
        assert err is None, "no error for empty args"

    def test_linux_port(self):
        """A /dev path is accepted as a port name alongside baud and mode."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("/dev/ttyUSB0 115200 E71")

        # Assert
        assert port == "/dev/ttyUSB0", "should handle Linux device paths"
        assert baud == 115200, "should extract baud rate"
        assert mode == ("E", 7, 1.0), "should extract mode tuple"
        assert err is None, "no error expected"

    def test_duplicate_baud_error(self):
        """Two baud rates are an error rather than last-one-wins."""
        # Act
        _, _, _, _, _, _, err = parse_open_args("COM3 9600 115200")

        # Assert
        assert err is not None, "should error on duplicate baud rate"
        assert "Duplicate baud" in err, "error should mention duplicate baud"

    def test_duplicate_mode_error(self):
        """Two mode tokens are an error rather than last-one-wins."""
        # Act
        _, _, _, _, _, _, err = parse_open_args("COM3 N81 E71")

        # Assert
        assert err is not None, "should error on duplicate mode"
        assert "Duplicate mode" in err, "error should mention duplicate mode"

    def test_duplicate_port_error(self):
        """A second port name is rejected as unexpected because only the first slot is a port."""
        # Act -- COM4 is not a line-ending / echo / mode / baud token,
        # and it isn't first, so the port-first enforcement rejects it
        # as "unexpected".
        _, _, _, _, _, _, err = parse_open_args("COM3 COM4")

        # Assert
        assert err is not None, "should error on duplicate port name"
        assert "Unexpected" in err, "error should mention unexpected argument"

    # -- Line ending tokens ------------------------------------------------

    def test_line_ending_cr(self):
        """The cr token sets a carriage-return line ending."""
        # Act
        port, baud, mode, le, echo, _, err = parse_open_args("COM3 cr")

        # Assert
        assert le == "\r", "cr -> carriage return"
        assert err is None, "no error"

    def test_line_ending_lf(self):
        """The lf token sets a newline line ending."""
        # Act
        _, _, _, le, _, _, err = parse_open_args("COM3 lf")

        # Assert
        assert le == "\n", "lf -> newline"
        assert err is None, "no error"

    def test_line_ending_crlf(self):
        """The crlf token sets a CR+LF line ending."""
        # Act
        _, _, _, le, _, _, err = parse_open_args("COM3 crlf")

        # Assert
        assert le == "\r\n", "crlf -> carriage return + newline"
        assert err is None, "no error"

    def test_line_ending_case_insensitive(self):
        """Line-ending tokens match regardless of case."""
        # Act -- uppercase/mixed should also match.
        _, _, _, le, _, _, err = parse_open_args("COM3 CRLF")

        # Assert
        assert le == "\r\n", "line ending match is case-insensitive"
        assert err is None, "no error"

    def test_duplicate_line_ending(self):
        """Two line-ending tokens are an error naming the duplicate."""
        # Act
        _, _, _, _, _, _, err = parse_open_args("COM3 cr lf")

        # Assert
        assert err is not None, "two line endings should error"
        assert "Duplicate line ending" in err, f"error names the duplicate; got {err!r}"

    # -- Echo tokens -------------------------------------------------------

    def test_echo_on(self):
        """The echo token turns local echo on."""
        # Act
        _, _, _, _, echo, _, err = parse_open_args("COM3 echo")

        # Assert
        assert echo is True, "echo token -> True"
        assert err is None, "no error"

    def test_noecho(self):
        """The noecho token turns local echo off explicitly, distinct from unset."""
        # Act
        _, _, _, _, echo, _, err = parse_open_args("COM3 noecho")

        # Assert
        assert echo is False, "noecho token -> False"
        assert err is None, "no error"

    def test_duplicate_echo(self):
        """Conflicting echo and noecho tokens are an error naming the duplicate."""
        # Act
        _, _, _, _, _, _, err = parse_open_args("COM3 echo noecho")

        # Assert
        assert err is not None, "two echo tokens should error"
        assert "Duplicate echo" in err, f"error names the duplicate; got {err!r}"

    # -- Order-independence after port-first -------------------------------

    def test_all_fields_any_order_after_port(self):
        """After the port, baud, mode, line ending and echo may come in any order."""
        # Act -- the user's example from the feature ask.
        port, baud, mode, le, echo, _, err = parse_open_args("COM3 echo crlf 9600 N81")

        # Assert
        assert port == "COM3", "port stays first"
        assert baud == 9600, "baud found"
        assert mode == ("N", 8, 1.0), "mode found"
        assert le == "\r\n", "line ending found"
        assert echo is True, "echo found"
        assert err is None, "no error"

    def test_no_port_still_accepts_other_fields(self):
        """Without a port the other fields still parse, for in-session changes."""
        # Act -- user just wants to change baud + echo in-session.
        port, baud, mode, le, echo, _, err = parse_open_args("9600 echo")

        # Assert
        assert port is None, "no port provided"
        assert baud == 9600, "baud parsed"
        assert echo is True, "echo parsed"
        assert err is None, "no error"

    # -- Port-first enforcement -------------------------------------------

    def test_port_must_be_first(self):
        """A port-like token after another field is refused; the port slot is first only."""
        # Act -- putting a port-name-looking token after a classifier
        # should be rejected.  Previously (position-independent) this
        # would have been accepted as "port=COM3 with echo first".
        _, _, _, _, _, _, err = parse_open_args("echo COM3")

        # Assert
        assert err is not None, "port-name-looking token after other fields must error"
        assert "Unexpected" in err, f"error phrasing; got {err!r}"


# ── set_mode ────────────────────────────────────────────────────────────────


class TestBaudFacts:
    """baud_facts is the one producer of the {"baud", "custom"} pair /port.baud_rate and /port.mode answer."""

    def test_no_rate_reads_the_config(self):
        """With no rate given, the baud is the config's, as /port.mode prints it."""
        actual = baud_facts(_cfg(serial={"baud_rate": 9600, "custom_baud": True}))
        assert actual == {"baud": 9600, "custom": True}, "the config's rate and custom flag"

    def test_a_read_rate_is_a_number(self):
        """A rate read off the open port as text answers as a number, the config's custom flag beside it."""
        actual = baud_facts(_cfg(), "250000")
        assert actual == {"baud": 250000, "custom": False}, "digits become an int"

    def test_a_rate_that_is_not_digits_stays_text(self):
        """A rate the port reports as something other than digits is kept as written, never guessed."""
        actual = baud_facts(_cfg(), "?")
        assert actual["baud"] == "?", "no number to make"

    def test_mode_facts_carry_the_same_pair(self):
        """mode_facts spreads baud_facts, so the two answers spell the rate one way."""
        cfg = _cfg(serial={"baud_rate": 9600})
        actual = {key: mode_facts(cfg)[key] for key in ("baud", "custom")}
        assert actual == baud_facts(cfg), "one producer, one spelling"


class TestSetMode:
    def test_show_current_disconnected(self):
        """A bare query while disconnected shows the configured baud and frame."""
        # Act
        msgs, _ = set_mode(None, _cfg(), "")

        # Assert
        texts = " ".join(t for t, _ in msgs)
        assert "115200" in texts, "should show current baud rate"
        assert "8N1" in texts, "should show current mode"
        assert "disconnected" in texts, "should show disconnected"

    def test_show_current_connected(self):
        """A bare query while connected shows live settings without a disconnected tag."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "")

        # Assert
        texts = " ".join(t for t, _ in msgs)
        assert "115200" in texts, "should show current baud rate"
        assert "disconnected" not in texts, "should not show disconnected"

    def test_set_mode_only(self):
        """A frame token alone updates parity, data and stop bits on the port and in cfg."""
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
        """A baud alone changes only the baud rate and leaves the framing untouched."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = set_mode(ser, _cfg(), "9600")

        # Assert
        assert ser.baudrate == 9600, "baudrate should be set to 9600"
        assert effects["cfg_update"]["baud_rate"] == 9600, "cfg_update baud_rate should be 9600"
        assert "parity" not in effects["cfg_update"], "should not touch parity when only baud set"

    def test_set_baud_and_mode(self):
        """Baud and frame together apply both and the summary line names both."""
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
        """Changing the mode needs an open port; disconnected it warns instead."""
        # Act
        msgs, _ = set_mode(None, _cfg(), "9600 N81")

        # Assert
        assert any(STATE_MESSAGES["serial_connected"] in t for t, _ in msgs), (
            "should warn not connected"
        )

    def test_invalid_token(self):
        """A token that is neither baud nor frame is reported as an error."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "garbage")

        # Assert
        assert any("red" == c for _, c in msgs), "should show red error for invalid token"

    def test_summary_format(self):
        """The summary prints the frame data-bits-first, e.g. 8O1.5, after the baud."""
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = set_mode(ser, _cfg(), "9600 O81.5")

        # Assert
        text = msgs[0][0]
        assert "Mode -> 9600 8O1.5" in text, "summary should show baud and frame"


# -- Port spec resolution ---------------------------------------------------


import os  # noqa: E402

from termapy.port_control import (  # noqa: E402
    _BUS_REPORTED_DEVICE_DESC,
    _DEVICE_ID_SEP,
    _RESERVED_PORTS,
    MATCH_AMBIGUOUS,
    MATCH_LITERAL,
    MATCH_RESERVED,
    MATCH_SERIAL,
    MATCH_URL,
    AmbiguousSerialNumberError,
    ChipFacts,
    _build_demo_fleet,
    _check_in_use,
    _devpropkey,
    _gather_all_chip_facts,
    _is_usb_device_node,
    _windows_location_chain,
    _windows_usb_product,
    chip_field,
    chip_info,
    gather_chip_facts,
    listing_devices,
    parse_location_paths,
    port_display,
    port_present,
    reserved_serial,
    resolve_port,
    resolve_port_trace,
    same_port,
    split_location_interface,
    synthetic_facts_for_reserved,
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
        """A spec naming an existing device resolves to itself."""
        # Act
        actual = resolve_port("COM3", source=_build_demo_fleet)

        # Assert
        assert actual == "COM3", "literal device match returns that device"

    def test_serial_number_resolves_to_device(self):
        """A USB serial number resolves to the device that carries it."""
        # Act
        actual = resolve_port("A1B2C3D4", source=_build_demo_fleet)

        # Assert
        expected = "COM3"
        assert actual == expected, f"SN A1B2C3D4 should resolve to {expected}, got {actual}"

    def test_serial_number_case_insensitive(self):
        """Serial-number lookup ignores case, since vendors and users disagree on it."""
        # Act
        actual = resolve_port("a1b2c3d4", source=_build_demo_fleet)

        # Assert
        expected = "COM3"
        assert actual == expected, f"SN match must be case-insensitive; got {actual}"

    def test_pipe_fallback_first_candidate_wins(self):
        """In a pipe-separated spec the first candidate that resolves wins."""
        # Act -- SN resolves, COM7 fallback never tried
        actual = resolve_port("A1B2C3D4|COM7", source=_build_demo_fleet)

        # Assert -- note fleet has COM7 so COM7 would also resolve;
        # test still proves order by checking we got the SN match, not COM7
        expected = "COM3"
        assert actual == expected, f"first resolving candidate wins; got {actual}"

    def test_pipe_fallback_second_wins_when_first_missing(self):
        """A missing first candidate falls through to the next one in the chain."""
        # Act
        actual = resolve_port("BOGUS_NO_MATCH|COM4", source=_build_demo_fleet)

        # Assert
        expected = "COM4"
        assert actual == expected, f"literal fallback wins when SN not found; got {actual}"

    def test_reserved_demo_passes_through(self):
        """DEMO is reserved and returned as-is without consulting the fleet."""
        # Act -- DEMO bypasses enumeration even when DEMO_FLEET is set
        actual = resolve_port("DEMO", source=_build_demo_fleet)

        # Assert
        assert actual == "DEMO", "DEMO is a reserved name"

    def test_reserved_demo_fail_passes_through(self):
        """DEMO_FAIL is reserved and returned as-is without consulting the fleet."""
        # Act
        actual = resolve_port("DEMO_FAIL", source=_build_demo_fleet)

        # Assert
        assert actual == "DEMO_FAIL", "DEMO_FAIL is a reserved name"

    def test_pyserial_url_passes_through(self):
        """A pyserial URL bypasses enumeration and is returned unchanged."""
        # Act
        actual = resolve_port("rfc2217://host:2217", source=_build_demo_fleet)

        # Assert
        assert actual == "rfc2217://host:2217", "URLs are passed through"

    def test_all_candidates_missing_returns_last(self):
        """On a total miss the last candidate is returned so the open error names it."""
        # Act
        actual = resolve_port("NOPE1|NOPE2|NOPE3", source=_build_demo_fleet)

        # Assert -- last candidate is what ``open_serial()`` will show
        # in its "Cannot open <X>" error, which is what the user most
        # explicitly asked for.
        expected = "NOPE3"
        assert actual == expected, f"last candidate returned on total miss; got {actual}"

    def test_ambiguous_sn_raises(self):
        """A serial number shared by two ports raises, naming both, instead of guessing."""

        # Arrange -- a fleet with two devices sharing the same SN "0001"
        # (a common burn-in on cheap CP2102 / CH340 clones), handed in
        # through source= rather than patched over the gather function.
        def _ambiguous_fleet():
            return [
                ChipFacts(
                    device="COM3",
                    manufacturer="CH340",
                    serial="0001",
                    vid_pid="1A86:7523",
                ),
                ChipFacts(
                    device="COM7",
                    manufacturer="CH340",
                    serial="0001",
                    vid_pid="1A86:7523",
                ),
            ]

        # Act + Assert
        with pytest.raises(AmbiguousSerialNumberError) as exc:
            resolve_port("0001", source=_ambiguous_fleet)
        assert exc.value.matches == ["COM3", "COM7"], "both colliding devices named in exception"
        assert "0001" in str(exc.value), "SN named in exception message"
        assert "COM3" in str(exc.value) and "COM7" in str(exc.value), (
            "both devices mentioned in exception message"
        )


# Each platform's rule runs where it holds: the Windows cases here and on any
# Windows run, the POSIX cases on Linux CI and in WSL.
_WINDOWS = os.name == "nt"
windows_only = pytest.mark.skipif(not _WINDOWS, reason="Windows port-name rules")
posix_only = pytest.mark.skipif(_WINDOWS, reason="Linux and macOS port-name rules")


def _linked_device(tmp_path):
    """A stand-in device file and a by-id style symlink to it."""
    device = tmp_path / "ttyUSB0"
    device.touch()
    link = tmp_path / "usb-FTDI_FT232R_USB_UART_A1B2C3D4-if00-port0"
    link.symlink_to(device)
    return str(device), str(link)


@pytest.mark.rule("port-name-one-compare")
class TestSamePort:
    """Two names are the same port when the OS would open the same device."""

    @windows_only
    @pytest.mark.parametrize("typed", ["COM3", "com3", "Com3", r"\\.\COM3", r"\\.\com3"])
    def test_windows_ignores_case_and_the_device_prefix(self, typed):
        """On Windows a name in any case, with or without the device prefix, is the listed COM3."""
        assert same_port(typed, "COM3") is True, f"{typed!r} opens COM3"

    @windows_only
    def test_windows_still_tells_ports_apart(self):
        """Ignoring case never makes COM3 match COM30 or COM4."""
        assert same_port("com3", "COM30") is False, "a longer number is another port"
        assert same_port("COM3", "COM4") is False, "another number is another port"

    @posix_only
    def test_posix_names_are_case_sensitive(self):
        """On Linux and macOS a name differing only in case is a different device."""
        assert same_port("/dev/ttyUSB0", "/dev/TTYUSB0") is False

    @posix_only
    def test_posix_follows_a_symlink(self, tmp_path):
        """A by-id style link is the device it points to."""
        device, link = _linked_device(tmp_path)
        assert same_port(link, device) is True, "the link opens the device"


@pytest.mark.rule("port-name-one-compare")
class TestResolvePortNameForms:
    """A name that opens a listed port resolves to the listed name and is present."""

    @windows_only
    @pytest.mark.parametrize("typed", ["com3", r"\\.\COM3"])
    def test_windows_forms_resolve_to_the_listed_name(self, typed):
        """com3 and the prefixed form resolve to COM3, count as present, and /port.chip finds the port."""
        # Act
        actual = resolve_port(typed, source=_build_demo_fleet)
        report = chip_info(typed, current_port="", source=_build_demo_fleet)
        # Assert
        assert actual == "COM3", "the listed spelling, so the app shows and opens COM3"
        assert port_present(typed, source=_build_demo_fleet) is True, (
            "a config naming the port this way is not read as unplugged"
        )
        assert report.problem is None, f"/port.chip {typed} finds the port"

    @posix_only
    def test_a_symlink_resolves_to_the_device_it_names(self, tmp_path):
        """A by-id link to a listed device resolves to that device and counts as present."""
        device, link = _linked_device(tmp_path)

        def fleet():
            return [ChipFacts(device=device, description="USB Serial Port")]

        assert resolve_port(link, source=fleet) == device, "the listed device, not the link"
        assert port_present(link, source=fleet) is True, "a by-id config is not read as unplugged"

    @windows_only
    def test_the_session_knows_its_own_port_by_any_name_on_windows(self):
        """The port this session holds is in use by it however the name is cased, so nothing is probed."""
        # COM250 exists nowhere: a regression would probe it, harmlessly, and fail here.
        assert _check_in_use("COM250", "com250") == "yes (this session)"

    @posix_only
    def test_the_session_knows_its_own_port_through_a_link(self, tmp_path):
        """The port this session holds by its by-id link is in use by it, so nothing is probed."""
        device, link = _linked_device(tmp_path)
        assert _check_in_use(device, link) == "yes (this session)"


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
        """The chip page resolves an SN-based port spec before its device lookup."""
        # Arrange -- SN-based config: current_port is the SN, nothing typed.
        # Act
        report = chip_info("", "A1B2C3D4", connected_port="COM3", source=_build_demo_fleet)

        # Assert
        assert report.problem is None, "chip_info must resolve the SN, not report no match"
        assert (report.data["device"], report.value) == ("COM3", "COM3"), (
            "resolves the SN to its device (COM3) and reports its facts"
        )

    def test_chip_field_resolves_serial_number(self):
        """A single chip field also resolves an SN-based spec to its device first."""
        # Act -- /port.chip.model under an SN config.
        msgs, _ = chip_field(
            "model", "", "A1B2C3D4", connected_port="COM3", source=_build_demo_fleet
        )

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
        """A device name is traced as a literal match."""
        # Act
        actual = resolve_port_trace("COM3", source=_build_demo_fleet)

        # Assert
        expected = [("COM3", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_sn_match(self):
        """A serial number is traced as a serial-number match."""
        # Act
        actual = resolve_port_trace("A1B2C3D4", source=_build_demo_fleet)

        # Assert
        expected = [("A1B2C3D4", MATCH_SERIAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_reserved(self):
        """A reserved name like DEMO is traced as reserved, not looked up."""
        # Act
        actual = resolve_port_trace("DEMO", source=_build_demo_fleet)

        # Assert
        expected = [("DEMO", MATCH_RESERVED)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_url(self):
        """A pyserial URL is traced as a URL match."""
        # Act
        actual = resolve_port_trace("rfc2217://host:2217", source=_build_demo_fleet)

        # Assert
        expected = [("rfc2217://host:2217", MATCH_URL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_with_first_miss(self):
        """A missed candidate is traced as None so the error can say which one failed."""
        # Act
        actual = resolve_port_trace("BOGUS|COM4", source=_build_demo_fleet)

        # Assert -- None marks the miss so the caller can say
        # "BOGUS: not found" in the error message.
        expected = [("BOGUS", None), ("COM4", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_all_miss(self):
        """Every candidate gets a None entry when nothing in the chain resolves."""
        # Act
        actual = resolve_port_trace("NOPE1|NOPE2", source=_build_demo_fleet)

        # Assert
        expected = [("NOPE1", None), ("NOPE2", None)]
        assert actual == expected, f"got {actual}"

    def test_ambiguous_sn_reported_not_raised(self):
        """The trace marks a shared SN as ambiguous and keeps going, never raising."""

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
        expected = [("0001", MATCH_AMBIGUOUS), ("COM7", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"


def _shared_serial_fleet() -> list[ChipFacts]:
    """Two ports burned with the same serial number, as cheap clones ship."""
    return [ChipFacts(device="COM3", serial="0001"), ChipFacts(device="COM7", serial="0001")]


@pytest.mark.rule("port-present-one-decider")
class TestPortPresent:
    """Whether the configured port is here, decided as connecting resolves it."""

    @pytest.mark.parametrize(
        "spec",
        [
            "COM3",
            "A1B2C3D4",
            "a1b2c3d4",
            "BOGUS|A1B2C3D4",
            "DEMO",
            "demo",
            "DEMO_VT100",
            "rfc2217://host:2217",
            "TERMAPY-DEMO",
        ],
    )
    def test_every_form_that_connects_is_present(self, spec):
        """A device name, a serial number in any case, a chain, a simulated port, its serial number and a URL are all here."""
        assert port_present(spec, source=_build_demo_fleet) is True, f"{spec} resolves"

    @pytest.mark.parametrize("spec", ["", "BOGUS", "NOPE1|NOPE2", "COM99"])
    def test_a_spec_that_matches_nothing_is_absent(self, spec):
        """Nothing in the chain matches an attached port: absent, so loading asks for one."""
        assert port_present(spec, source=_build_demo_fleet) is False, f"{spec} matches nothing"

    def test_a_shared_serial_number_is_absent_as_the_connect_refuses_it(self):
        """A serial number two ports share stops the chain, exactly where resolve_port raises."""
        with pytest.raises(AmbiguousSerialNumberError):
            resolve_port("0001|COM7", source=_shared_serial_fleet)
        assert port_present("0001|COM7", source=_shared_serial_fleet) is False, (
            "the connect would raise, so the port is not here"
        )
        assert port_present("COM7|0001", source=_shared_serial_fleet) is True, (
            "a literal name ahead of the shared serial number resolves first"
        )

    def test_each_simulated_port_answers_to_its_serial_number(self):
        """reserved_serial(name) resolves to that simulator, and its facts carry the same number."""
        for name in _RESERVED_PORTS:
            # Arrange
            serial = reserved_serial(name)

            # Act
            actual = resolve_port(serial, source=_build_demo_fleet)

            # Assert
            assert actual == name, f"{serial} opens {name}"
            facts = synthetic_facts_for_reserved(name)
            assert facts is not None, f"{name} has facts"
            assert facts.serial == serial, "the number /port.info shows is the one that resolves"

    @pytest.mark.rule("port-display")
    def test_display_shows_the_device_and_its_serial_number(self):
        """A config naming its port by serial number displays as the device, the number as its serial."""
        # Act
        device, facts = port_display("A1B2C3D4", source=_build_demo_fleet)

        # Assert
        assert device == "COM3", "the device the serial number resolves to"
        assert facts is not None and facts.serial == "A1B2C3D4", "the number stays the serial"

    def test_display_of_the_open_device_is_what_is_open(self):
        """Connected, the display is the device actually open, not what the chain lists first."""
        device, facts = port_display("A1B2C3D4|COM4", "COM3", source=_build_demo_fleet)
        assert device == "COM3", "the open device wins over the spec"
        assert facts is not None and facts.serial == "A1B2C3D4", "its own facts"

    def test_display_of_a_port_not_here_is_the_spec(self):
        """Nothing matches, or a shared serial number: the spec as written, with no facts."""
        assert port_display("NOPE", source=_build_demo_fleet) == ("NOPE", None)
        assert port_display("0001", source=_shared_serial_fleet) == ("0001", None)

    def test_a_real_serial_number_wins_over_a_simulated_one(self):
        """An attached device burned with a simulator's serial number is the one found."""

        def fleet():
            return [ChipFacts(device="COM9", serial=reserved_serial("DEMO"))]

        actual = resolve_port(reserved_serial("DEMO"), source=fleet)
        assert actual == "COM9", "real hardware is matched before the simulators"

    @pytest.mark.rule("port-display")
    def test_a_listing_resolves_every_row_against_one_snapshot(self):
        """A config list reads the ports once: a serial number shows its device, the rest as written."""
        # Arrange -- a fleet that counts how often it is read
        reads: list[int] = []

        def fleet():
            reads.append(1)
            return _build_demo_fleet()

        specs = {"a": "A1B2C3D4", "b": "COM4", "c": "NOPE", "d": ""}

        # Act
        actual = listing_devices(specs, source=fleet)

        # Assert
        expected = {"a": "COM3", "b": "COM4", "c": "NOPE", "d": ""}
        assert actual == expected, (
            "the serial number shows its device; a name and a miss as written"
        )
        assert len(reads) == 1, "one enumeration for the whole list, not one per row"

    @pytest.mark.rule("port-display")
    def test_a_listing_shows_the_open_device_on_the_active_config(self):
        """The active config's cell is the open device whatever its file says; a shared number shows as written."""
        # Act
        actual = listing_devices(
            {"live": "A1B2C3D4|COM4", "shared": "0001"},
            active="live",
            open_device="COM9",
            source=_shared_serial_fleet,
        )

        # Assert
        assert actual["live"] == "COM9", "the open device wins over the spec"
        assert actual["shared"] == "0001", "an ambiguous serial number shows as written"


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
        """An injected source replaces enumeration, and its ports come back sorted."""
        # Act -- no env var, no hardware
        actual = _gather_all_chip_facts(source=self._fleet)

        # Assert
        devices = [f.device for f in actual]
        assert devices == ["COM8", "COM9"], (
            f"the injected fleet is returned, sorted by device; got {devices}"
        )

    def test_injection_wins_over_the_environment(self, monkeypatch):
        """An explicit source outranks TERMAPY_DEMO_FLEET, so tests never unset it first."""
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
        """With no source, TERMAPY_DEMO_FLEET installs the demo fleet with no code change."""
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
        """trust_env=False bypasses the demo fleet and reaches real enumeration."""
        # Arrange -- the variable is set, but this caller wants determinism
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

        # Act -- real enumeration; what it finds depends on the machine, so
        # assert only that the SYNTHETIC record is not what came back.
        actual = gather_chip_facts("COM3", trust_env=False)

        # Assert
        serial = actual.serial if actual else None
        assert serial != "A1B2C3D4", (
            "trust_env=False must reach real hardware, not the fleet the environment is offering"
        )

    def test_a_named_port_missing_from_a_fleet_is_not_invented(self):
        """An injected fleet is authoritative: an unlisted DEMO gets no synthetic record."""
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
        assert actual == "COM9", f"SN INJECTED-9 should resolve to COM9, got {actual}"

    def test_the_trace_reports_how_an_injected_candidate_matched(self):
        """The trace classifies SN and literal matches against an injected fleet too."""
        # Act
        actual = resolve_port_trace("INJECTED-8|COM9", source=self._fleet)

        # Assert
        assert actual == [("INJECTED-8", MATCH_SERIAL), ("COM9", MATCH_LITERAL)], (
            f"both candidates resolve against the injected fleet; got {actual}"
        )

    def test_ambiguity_still_raises_against_an_injected_fleet(self):
        """The shared-SN refusal holds whatever the port source, injected fleets included."""

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
        """A device behind a hub reads bus-port.port, matching pyserial's notation."""
        # Arrange -- a real path from an FTDI adapter's parent USB node.
        paths = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)"

        # Act
        actual = parse_location_paths(paths)

        # Assert
        assert actual == "1-8.3", f"bus 1, hub port 8, device port 3; got {actual!r}"

    def test_a_port_directly_on_the_root_hub_has_no_dot(self):
        """A device on the root hub gets the bare bus-port form with no dot."""
        # Act -- one hop: the separator is '-', and '.' never appears.
        actual = parse_location_paths("PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)")

        # Assert
        assert actual == "1-8", f"single hop keeps the dash form; got {actual!r}"

    def test_each_further_hop_adds_a_dot(self):
        """Each extra hub tier appends one dotted port number to the chain."""
        # Act -- a hub behind a hub behind a hub.
        actual = parse_location_paths("PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(4)#USB(2)")

        # Assert
        assert actual == "1-8.4.2", f"one dot per extra tier; got {actual!r}"

    def test_the_bus_is_numbered_from_one(self):
        """The 0-based USBROOT index is shown 1-based, as pyserial does."""
        # Act -- USBROOT is 0-based in the registry, 1-based on display.
        # This is pyserial's convention, and matching it is the point.
        actual = parse_location_paths("PCIROOT(0)#USBROOT(1)#USB(2)")

        # Assert
        assert actual == "2-2", f"USBROOT(1) is bus 2; got {actual!r}"

    def test_a_non_usb_path_has_no_chain(self):
        """A path with no USB hop yields None so callers can fall back."""
        # Act -- a PCI serial card names no USB hop.
        actual = parse_location_paths("PCIROOT(0)#PCI(1C00)#PCI(0000)")

        # Assert
        assert actual is None, (
            f"None, not an empty string, so callers can fall back; got {actual!r}"
        )

    def test_an_empty_path_has_no_chain(self):
        """An empty LOCATION_PATHS string yields None."""
        # Act
        actual = parse_location_paths("")

        # Assert
        assert actual is None, f"nothing in, nothing out; got {actual!r}"


class TestWindowsLocationChain:
    """The devnode walk is best-effort and must never raise."""

    def test_an_unknown_device_yields_no_location(self):
        """An unknown device, or no Windows bindings, yields None rather than raising."""
        # Act -- no such devnode.  Off Windows there are no bindings at
        # all, and the answer is the same: None, not an exception.
        actual = _windows_location_chain("NOSUCHDEVICE")

        # Assert
        assert actual is None, (
            f"enrichment must degrade to blank, never fail a lookup; got {actual!r}"
        )

    def test_a_composite_interface_becomes_the_suffix(self):
        """A USBMI hop becomes a :x.<n> interface suffix, as pyserial spells it."""
        # Arrange -- the real path from a composite debugger+CDC device,
        # whose COM port is interface 1.
        paths = "PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(4)#USBMI(1)"

        # Act
        actual = parse_location_paths(paths)

        # Assert -- same spelling pyserial produces for the same device.
        assert actual == "1-8.4:x.1", f"interface 1 of the device at 1-8.4; got {actual!r}"

    def test_channels_of_one_chip_are_told_apart(self):
        """Two channels of one chip share a hop chain and differ only by interface."""
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
        """Without a USBMI hop the location carries no interface suffix."""
        # Act -- no USBMI token means nothing to disambiguate, and
        # pyserial omits the suffix in that case on every platform.
        actual = parse_location_paths("PCIROOT(0)#PCI(1400)#USBROOT(0)#USB(8)#USB(3)")

        # Assert
        assert actual == "1-8.3", f"no interface, no suffix; got {actual!r}"


class TestWindowsUsbProduct:
    """The device's own product name comes from the USB device node, never from an interface's.

    Found 2026-10-31: a PICkit 5's serial interface reports the placeholder
    USB_DEVICE_CDC_NAME, while the device itself reports MPLAB PICkit 5.
    """

    @pytest.mark.parametrize(
        ("parts", "vid_pid", "expected"),
        [
            (["USB", "VID_04D8&PID_9036", "020026702RYN040952"], "04D8:9036", True),
            (["USB", "VID_04D8&PID_9036&MI_01", "6&1C949859&0&0001"], "04D8:9036", False),
            (["FTDIBUS", "VID_0403+PID_6001+BG03U7VTA", "0000"], "0403:6001", False),
            (["USB", "VID_0403&PID_6001", "BG03U7VT"], "0403:6001", True),
            (["USB", "VID_0BDA&PID_5487", "5&2D5D2A&0&2"], "04D8:9036", False),
            (["usb", "vid_04d8&pid_9036", "020026702RYN040952"], "04d8:9036", True),
            (["USB", "VID_04D8&PID_9036"], "04D8:9036", False),
        ],
        ids=[
            "the device",
            "an interface of it",
            "the FTDI bus node",
            "the FTDI device above it",
            "a hub above it",
            "any case",
            "no instance part",
        ],
    )
    def test_only_the_device_node_answers(self, parts, vid_pid, expected):
        """The climb stops at USB plus the port's VID and PID with no interface, nowhere else."""
        # Act
        actual = _is_usb_device_node(_DEVICE_ID_SEP.join(parts), vid_pid)

        # Assert
        assert actual == expected

    def test_the_property_key_is_laid_out_as_windows_reads_it(self):
        """The DEVPROPKEY is 20 bytes: the GUID with its first fields little-endian, then the id."""
        # Act
        actual = _devpropkey(*_BUS_REPORTED_DEVICE_DESC)

        # Assert -- devpkey.h's {540b947e-...}: Data1 0x540B947E lands
        # byte-reversed, the last eight GUID bytes as written, id 4 last
        assert len(actual) == 20, "a 16-byte GUID and a 4-byte property id"
        assert actual[:4] == bytes([0x7E, 0x94, 0x0B, 0x54]), "Data1 little-endian"
        assert actual[8:16] == bytes([0xA8, 0xA2, 0x6A, 0x0B, 0x89, 0x4C, 0xBD, 0xA2])
        assert actual[16:] == bytes([4, 0, 0, 0]), "property id 4, little-endian"

    def test_an_unknown_device_yields_no_product(self):
        """An unknown device, or no Windows bindings, yields None rather than raising."""
        # Act
        actual = _windows_usb_product("NOSUCHDEVICE", "04D8:9036")

        # Assert
        assert actual is None, f"enrichment degrades to blank; got {actual!r}"


class TestSplitLocationInterface:
    """A location carries two facts; they belong in two fields.

    Only a multi-function device has the ``:<config>.<interface>`` tail,
    so leaving it inline makes the raw string change shape from row to
    row and stops ports being compared down a column.
    """

    def test_a_windows_composite_location_splits(self):
        """The Windows :x.<n> tail splits off, keeping the interface number."""
        # Act -- the "x" is pyserial's placeholder for a configuration
        # value Windows won't yield; the interface after it is real.
        actual = split_location_interface("1-8.4:x.1")

        # Assert
        assert actual == ("1-8.4", "1"), f"path and interface; got {actual}"

    def test_a_linux_location_splits_the_same_way(self):
        """A Linux :<config>.<n> tail splits the same way despite the real config value."""
        # Act -- Linux names the real configuration value, so the tail
        # reads ":1.1" rather than ":x.1".  Same shape, same split.
        actual = split_location_interface("1-8.4:1.1")

        # Assert
        assert actual == ("1-8.4", "1"), (
            f"the config value differs by platform, the split doesn't; got {actual}"
        )

    def test_a_single_function_location_has_no_interface(self):
        """A location with no tail keeps its path and reports interface None."""
        # Act
        actual = split_location_interface("1-8.3")

        # Assert
        assert actual == ("1-8.3", None), f"nothing to split, and None marks that; got {actual}"

    def test_the_registry_fallback_is_left_alone(self):
        """The Windows Hub_#/Port_# fallback is not mistaken for an interface tail."""
        # Act -- the Windows hub/port fallback has dots but no tail, and
        # must not be mistaken for one.
        actual = split_location_interface("Hub_#0011.Port_#0003")

        # Assert
        assert actual == ("Hub_#0011.Port_#0003", None), (
            f"a non-matching notation passes through whole; got {actual}"
        )

    def test_no_location_splits_into_no_facts(self):
        """A missing location splits into (None, None)."""
        # Act
        actual = split_location_interface(None)

        # Assert
        assert actual == (None, None), f"None in, None out; got {actual}"


# ── list_port_records ────────────────────────────────────────────────────────


class TestListPortRecords:
    """The structured twin of list_ports (serves /port.list's data)."""

    def test_records_from_injected_fleet(self):
        """One record per fleet port, in the fleet's order."""
        # Arrange / Act
        from termapy.port_control import _build_demo_fleet, list_port_records

        actual = list_port_records(source=_build_demo_fleet)

        # Assert
        actual_devices = [record["device"] for record in actual]
        expected_devices = [facts.device for facts in _build_demo_fleet()]
        assert actual_devices == expected_devices, "one record per fleet port, order preserved"

    def test_record_schema_is_fixed(self):
        """Every record carries every key, null when unknown, so consumers need not probe."""
        # Arrange / Act -- every record has every field (null for unknown),
        # so consumers can rely on the shape without probing.
        from termapy.port_control import _build_demo_fleet, list_port_records

        records = list_port_records(source=_build_demo_fleet)

        # Assert
        expected_keys = {
            "device",
            "manufacturer",
            "manufacturer_raw",
            "vendor",
            "description",
            "product",
            "chip",
            "speed",
            "vid",
            "pid",
            "vid_pid",
            "serial_number",
            "in_use",
            "driver",
            "location",
            "interface_number",
        }
        for record in records:
            assert set(record.keys()) == expected_keys, (
                f"fixed schema violated for {record.get('device')!r}"
            )

    def test_empty_fleet_gives_empty_list(self):
        """A fleet with no ports gives an empty list, not an error."""
        # Arrange / Act -- injection expresses "no ports", which the env
        # layer cannot.
        from termapy.port_control import list_port_records

        actual = list_port_records(source=lambda: [])

        # Assert
        assert actual == [], "no ports -> empty list, not an error"
