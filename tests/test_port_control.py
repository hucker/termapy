"""Tests for port_control.py — pure serial port control functions."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from termapy.port_control import (
    PORT_PROPS,
    SERIAL_KEYS,
    get_set_flow,
    get_set_hw_line,
    get_set_prop,
    list_ports,
    parse_bool_value,
    port_info,
    read_signal,
    send_break,
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
        assert "port" in SERIAL_KEYS
        assert "baud_rate" in SERIAL_KEYS
        assert "flow_control" in SERIAL_KEYS

    def test_port_props_has_all_keys(self):
        assert "baud_rate" in PORT_PROPS
        assert "byte_size" in PORT_PROPS
        assert "parity" in PORT_PROPS
        assert "stop_bits" in PORT_PROPS


# ── parse_bool_value ─────────────────────────────────────────────────────────


class TestParseBoolValue:

    def test_true_values(self):
        for val in ("1", "on", "true", "high"):
            assert parse_bool_value(val) is True

    def test_false_values(self):
        for val in ("0", "off", "false", "low"):
            assert parse_bool_value(val) is False

    def test_invalid_returns_none(self):
        assert parse_bool_value("maybe") is None
        assert parse_bool_value("") is None


# ── port_info ────────────────────────────────────────────────────────────────


class TestPortInfo:

    def test_disconnected(self):
        # Act
        msgs, effects = port_info(_cfg(), None)

        # Assert — shows config values with disconnected state
        texts = [t for t, _ in msgs]
        assert any("disconnected" in t for t in texts)
        assert any("115200" in t for t in texts)

    def test_connected_shows_hw_lines(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = port_info(_cfg(), ser)

        # Assert — shows hardware line values
        texts = [t for t, _ in msgs]
        assert any("connected" in t for t in texts)
        assert any("DTR" in t for t in texts)
        assert any("RTS" in t for t in texts)


# ── get_set_prop ─────────────────────────────────────────────────────────────


class TestGetSetProp:

    def test_get_disconnected_shows_config(self):
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "")

        # Assert
        assert any("115200" in t for t, _ in msgs)
        assert any("disconnected" in t for t, _ in msgs)

    def test_get_connected_shows_live_value(self):
        # Arrange
        ser = _mock_ser(baudrate=9600)

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "")

        # Assert
        assert any("9600" in t for t, _ in msgs)

    def test_set_valid_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "baud_rate", "9600")

        # Assert — value changed, side effects requested
        assert ser.baudrate == 9600
        assert effects.get("update_title") is True
        assert effects["cfg_update"]["baud_rate"] == 9600

    def test_set_invalid_parity(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_prop(ser, _cfg(), "parity", "X")

        # Assert — error message, no side effects
        assert any("red" == c for _, c in msgs)
        assert not effects.get("cfg_update")

    def test_set_when_disconnected(self):
        # Act
        msgs, effects = get_set_prop(None, _cfg(), "baud_rate", "9600")

        # Assert — not connected warning
        assert any("Not connected" in t for t, _ in msgs)


# ── get_set_flow ─────────────────────────────────────────────────────────────


class TestGetSetFlow:

    def test_get_flow(self):
        # Act
        msgs, _ = get_set_flow(None, _cfg(), "")

        # Assert
        assert any("none" in t for t, _ in msgs)

    def test_set_valid_flow(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_flow(ser, _cfg(), "rtscts")

        # Assert
        assert ser.rtscts is True
        assert effects.get("sync_hw") is True
        assert effects["cfg_update"]["flow_control"] == "rtscts"

    def test_set_invalid_flow(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = get_set_flow(ser, _cfg(), "invalid")

        # Assert
        assert any("red" == c for _, c in msgs)


# ── get_set_hw_line ──────────────────────────────────────────────────────────


class TestGetSetHwLine:

    def test_get_dtr(self):
        # Arrange
        ser = _mock_ser(dtr=True)

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs)

    def test_set_dtr(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, effects = get_set_hw_line(ser, "dtr", "0")

        # Assert
        assert ser.dtr is False
        assert effects.get("sync_hw") is True

    def test_invalid_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = get_set_hw_line(ser, "dtr", "maybe")

        # Assert
        assert any("red" == c for _, c in msgs)

    def test_disconnected(self):
        # Act
        msgs, _ = get_set_hw_line(None, "dtr", "1")

        # Assert
        assert any("Not connected" in t for t, _ in msgs)


# ── read_signal ──────────────────────────────────────────────────────────────


class TestReadSignal:

    def test_read_cts(self):
        # Arrange
        ser = _mock_ser(cts=True)

        # Act
        msgs, _ = read_signal(ser, "cts", "")

        # Assert
        assert any(t.strip() == "1" for t, _ in msgs)

    def test_read_only_rejects_value(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = read_signal(ser, "cts", "1")

        # Assert
        assert any("read-only" in t for t, _ in msgs)

    def test_disconnected(self):
        # Act
        msgs, _ = read_signal(None, "cts", "")

        # Assert
        assert any("Not connected" in t for t, _ in msgs)


# ── send_break ───────────────────────────────────────────────────────────────


class TestSendBreak:

    def test_default_duration(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "")

        # Assert
        ser.send_break.assert_called_once_with(duration=0.25)
        assert any("250ms" in t for t, _ in msgs)

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
        assert any("red" == c for _, c in msgs)
        ser.send_break.assert_not_called()

    def test_zero_duration_invalid(self):
        # Arrange
        ser = _mock_ser()

        # Act
        msgs, _ = send_break(ser, "0")

        # Assert
        assert any("red" == c for _, c in msgs)

    def test_disconnected(self):
        # Act
        msgs, _ = send_break(None, "")

        # Assert
        assert any("Not connected" in t for t, _ in msgs)
