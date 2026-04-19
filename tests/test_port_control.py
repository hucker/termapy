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


# -- Port spec resolution ---------------------------------------------------


import pytest  # noqa: E402

from termapy.port_control import (  # noqa: E402
    AmbiguousSerialNumberError,
    ChipFacts,
    MATCH_LITERAL,
    MATCH_RESERVED,
    MATCH_SERIAL,
    MATCH_URL,
    resolve_port,
    resolve_port_trace,
)


class TestResolvePort:
    """resolve_port() translates a port spec into a concrete device name.

    These tests use the DEMO_FLEET env-var hook to install a
    deterministic three-port fleet (FTDI COM3 SN A1B2C3D4, Silicon
    Labs COM4 SN 0001, Microsoft COM7 SN 020026702RYN040952).
    """

    @pytest.fixture(autouse=True)
    def _use_demo_fleet(self, monkeypatch):
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

    def test_literal_device_name_unchanged(self):
        # Act
        actual = resolve_port("COM3")

        # Assert
        assert actual == "COM3", "literal device match returns that device"

    def test_serial_number_resolves_to_device(self):
        # Act
        actual = resolve_port("A1B2C3D4")

        # Assert
        expected = "COM3"
        assert actual == expected, \
            f"SN A1B2C3D4 should resolve to {expected}, got {actual}"

    def test_serial_number_case_insensitive(self):
        # Act
        actual = resolve_port("a1b2c3d4")

        # Assert
        expected = "COM3"
        assert actual == expected, \
            f"SN match must be case-insensitive; got {actual}"

    def test_pipe_fallback_first_candidate_wins(self):
        # Act -- SN resolves, COM7 fallback never tried
        actual = resolve_port("A1B2C3D4|COM7")

        # Assert -- note fleet has COM7 so COM7 would also resolve;
        # test still proves order by checking we got the SN match, not COM7
        expected = "COM3"
        assert actual == expected, \
            f"first resolving candidate wins; got {actual}"

    def test_pipe_fallback_second_wins_when_first_missing(self):
        # Act
        actual = resolve_port("BOGUS_NO_MATCH|COM4")

        # Assert
        expected = "COM4"
        assert actual == expected, \
            f"literal fallback wins when SN not found; got {actual}"

    def test_reserved_demo_passes_through(self):
        # Act -- DEMO bypasses enumeration even when DEMO_FLEET is set
        actual = resolve_port("DEMO")

        # Assert
        assert actual == "DEMO", "DEMO is a reserved name"

    def test_reserved_demo_fail_passes_through(self):
        # Act
        actual = resolve_port("DEMO_FAIL")

        # Assert
        assert actual == "DEMO_FAIL", "DEMO_FAIL is a reserved name"

    def test_pyserial_url_passes_through(self):
        # Act
        actual = resolve_port("rfc2217://host:2217")

        # Assert
        assert actual == "rfc2217://host:2217", "URLs are passed through"

    def test_all_candidates_missing_returns_last(self):
        # Act
        actual = resolve_port("NOPE1|NOPE2|NOPE3")

        # Assert -- last candidate is what ``open_serial()`` will show
        # in its "Cannot open <X>" error, which is what the user most
        # explicitly asked for.
        expected = "NOPE3"
        assert actual == expected, \
            f"last candidate returned on total miss; got {actual}"

    def test_ambiguous_sn_raises(self, monkeypatch):
        # Arrange -- monkeypatch _gather_all_chip_facts with a fleet
        # containing two devices sharing the same SN "0001" (a common
        # burn-in on cheap CP2102 / CH340 clones).
        import termapy.port_control as pc

        def _ambiguous_fleet(connected_port: str = ""):
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

        monkeypatch.setattr(pc, "_gather_all_chip_facts", _ambiguous_fleet)

        # Act + Assert
        with pytest.raises(AmbiguousSerialNumberError) as exc:
            resolve_port("0001")
        assert exc.value.matches == ["COM3", "COM7"], \
            "both colliding devices named in exception"
        assert "0001" in str(exc.value), "SN named in exception message"
        assert "COM3" in str(exc.value) and "COM7" in str(exc.value), \
            "both devices mentioned in exception message"


class TestResolvePortTrace:
    """resolve_port_trace() builds a per-candidate diagnostic trace."""

    @pytest.fixture(autouse=True)
    def _use_demo_fleet(self, monkeypatch):
        monkeypatch.setenv("TERMAPY_DEMO_FLEET", "1")

    def test_single_candidate_literal_match(self):
        # Act
        actual = resolve_port_trace("COM3")

        # Assert
        expected = [("COM3", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_sn_match(self):
        # Act
        actual = resolve_port_trace("A1B2C3D4")

        # Assert
        expected = [("A1B2C3D4", MATCH_SERIAL)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_reserved(self):
        # Act
        actual = resolve_port_trace("DEMO")

        # Assert
        expected = [("DEMO", MATCH_RESERVED)]
        assert actual == expected, f"got {actual}"

    def test_single_candidate_url(self):
        # Act
        actual = resolve_port_trace("rfc2217://host:2217")

        # Assert
        expected = [("rfc2217://host:2217", MATCH_URL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_with_first_miss(self):
        # Act
        actual = resolve_port_trace("BOGUS|COM4")

        # Assert -- None marks the miss so the caller can say
        # "BOGUS: not found" in the error message.
        expected = [("BOGUS", None), ("COM4", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"

    def test_fallback_chain_all_miss(self):
        # Act
        actual = resolve_port_trace("NOPE1|NOPE2")

        # Assert
        expected = [("NOPE1", None), ("NOPE2", None)]
        assert actual == expected, f"got {actual}"

    def test_ambiguous_sn_reported_not_raised(self, monkeypatch):
        # Arrange -- same duplicate-SN fleet as the raises test.
        # trace()'s contract is to NEVER raise, so the caller can build
        # one coherent error message even when one of multiple
        # candidates is ambiguous.
        import termapy.port_control as pc

        def _ambiguous_fleet(connected_port: str = ""):
            return [
                ChipFacts(device="COM3", serial="0001"),
                ChipFacts(device="COM7", serial="0001"),
            ]

        monkeypatch.setattr(pc, "_gather_all_chip_facts", _ambiguous_fleet)

        # Act
        actual = resolve_port_trace("0001|COM7")

        # Assert -- ambiguous first, then literal fallback.
        expected = [("0001", "ambiguous"), ("COM7", MATCH_LITERAL)]
        assert actual == expected, f"got {actual}"


