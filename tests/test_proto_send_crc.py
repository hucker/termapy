"""Tests for proto.send CRC-append feature."""

import pytest

from termapy.plugins import EngineAPI, PluginContext
from termapy.builtins.plugins.proto import _cmd_send, _parse_send_algo
from termapy.protocol import get_crc_registry


@pytest.fixture
def send_env():
    """Create a PluginContext that captures writes and serial output."""
    output = []
    tx_bytes = []

    ctx = PluginContext(
        write=lambda text, color=None: output.append((text, color)),
        is_connected=lambda: True,
        serial_write=lambda data: tx_bytes.append(data),
        serial_read_raw=lambda timeout_ms=1000, frame_gap_ms=0: b"",
        engine=EngineAPI(),
    )
    return ctx, output, tx_bytes


# ── _parse_send_algo ────────────────────────────────────────────────────────


class TestParseSendAlgo:
    """Tests for algorithm name + suffix parsing."""

    def test_bare_algo(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus", registry,
        )

        # Assert — exact match, default LE, no ascii
        assert actual_name == "crc16-modbus"
        assert actual_be is False
        assert actual_ascii is False

    def test_algo_be(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_be", registry,
        )

        # Assert — BE suffix stripped
        assert actual_name == "crc16-modbus"
        assert actual_be is True
        assert actual_ascii is False

    def test_algo_le(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_le", registry,
        )

        # Assert — explicit LE, same as default
        assert actual_name == "crc16-modbus"
        assert actual_be is False
        assert actual_ascii is False

    def test_algo_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_ascii", registry,
        )

        # Assert — ascii suffix stripped
        assert actual_name == "crc16-modbus"
        assert actual_be is False
        assert actual_ascii is True

    def test_algo_be_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_be_ascii", registry,
        )

        # Assert — both suffixes stripped
        assert actual_name == "crc16-modbus"
        assert actual_be is True
        assert actual_ascii is True

    def test_algo_le_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_le_ascii", registry,
        )

        # Assert — LE + ascii
        assert actual_name == "crc16-modbus"
        assert actual_be is False
        assert actual_ascii is True

    def test_unknown_returns_none(self):
        # Act
        registry = get_crc_registry()
        actual_name, _, _ = _parse_send_algo("not-an-algo", registry)

        # Assert — no match
        assert actual_name is None

    def test_case_insensitive(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, _ = _parse_send_algo(
            "CRC16-MODBUS_BE", registry,
        )

        # Assert — case folded
        assert actual_name == "crc16-modbus"
        assert actual_be is True


# ── Send with CRC ──────────────────────────────────────────────────────────


class TestSendCrcAppend:
    """CRC algorithm detection and byte append."""

    def test_crc16_modbus_le_default(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        # Known: CRC16-Modbus of 01 03 00 00 00 01 = 0x0A84, LE = 84 0A
        args = "crc16-modbus 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected = b"\x01\x03\x00\x00\x00\x01\x84\x0A"
        assert actual == expected  # data with LE CRC appended

    def test_crc16_modbus_be(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_be 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected = b"\x01\x03\x00\x00\x00\x01\x0A\x84"
        assert actual == expected  # data with BE CRC appended

    def test_crc16_modbus_ascii_le(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_ascii 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected_data = b"\x01\x03\x00\x00\x00\x01"
        expected_crc_text = b"840A"  # LE ascii: bytes reversed
        expected = expected_data + expected_crc_text
        assert actual == expected  # data with LE ASCII CRC appended

    def test_crc16_modbus_ascii_be(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_be_ascii 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected_data = b"\x01\x03\x00\x00\x00\x01"
        expected_crc_text = b"0A84"  # BE ascii: natural order
        expected = expected_data + expected_crc_text
        assert actual == expected  # data with BE ASCII CRC appended

    def test_crc_info_displayed(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert len(actual) == 1  # CRC info line shown
        assert "0x0A84" in actual[0]  # CRC value displayed
        assert "LE" in actual[0]  # endian label shown
        assert "bin" in actual[0]  # mode label shown

    def test_crc_info_be_ascii(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_be_ascii 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert "BE" in actual[0]  # BE label
        assert "ascii" in actual[0]  # ascii mode label


class TestSendCrcEdgeCases:
    """Error handling and edge cases for CRC-append."""

    def test_no_data_after_algo(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus"

        # Act
        _cmd_send(ctx, args)

        # Assert
        assert len(tx_bytes) == 0  # nothing sent
        actual = [t for t, c in output if c == "red"]
        assert any("No data" in t for t in actual)  # error shown

    def test_no_data_after_algo_with_suffix(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_ascii"

        # Act
        _cmd_send(ctx, args)

        # Assert
        assert len(tx_bytes) == 0  # nothing sent
        actual = [t for t, c in output if c == "red"]
        assert any("No data" in t for t in actual)  # error shown

    def test_no_algo_sends_raw(self, send_env):
        # Arrange — first word is NOT a CRC algo, so plain send
        ctx, output, tx_bytes = send_env
        args = "01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected = b"\x01\x03\x00\x00\x00\x01"
        assert actual == expected  # raw bytes, no CRC appended

    def test_no_crc_info_without_algo(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert len(actual) == 0  # no CRC info line

    def test_not_connected(self):
        # Arrange
        output = []
        ctx = PluginContext(
            write=lambda text, color=None: output.append((text, color)),
            is_connected=lambda: False,
        )

        # Act
        _cmd_send(ctx, "crc16-modbus 01 03")

        # Assert
        actual = [t for t, c in output if c == "red"]
        assert any("Not connected" in t for t in actual)  # error shown

    def test_empty_args(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env

        # Act
        _cmd_send(ctx, "")

        # Assert
        assert len(tx_bytes) == 0  # nothing sent
        actual = [t for t, c in output if c == "red"]
        assert any("Usage" in t for t in actual)  # usage shown


class TestSendCrcAlgorithms:
    """Verify CRC-append works with different algorithm widths."""

    def test_crc8(self, send_env):
        # Arrange — use a CRC-8 algorithm
        ctx, output, tx_bytes = send_env
        registry = get_crc_registry()
        # Find a crc8 algo
        crc8_name = next(
            (n for n in registry if registry[n].width == 1), None
        )
        if crc8_name is None:
            pytest.skip("No CRC-8 algorithm available")
        algo = registry[crc8_name]
        data = b"\x01\x02\x03"
        expected_crc = algo.compute(data)

        # Act
        _cmd_send(ctx, f"{crc8_name} 01 02 03")

        # Assert
        actual = tx_bytes[0]
        assert actual == data + bytes([expected_crc])  # 1-byte CRC appended

    def test_crc32(self, send_env):
        # Arrange — use a CRC-32 algorithm
        ctx, output, tx_bytes = send_env
        registry = get_crc_registry()
        crc32_name = next(
            (n for n in registry if registry[n].width == 4), None
        )
        if crc32_name is None:
            pytest.skip("No CRC-32 algorithm available")
        algo = registry[crc32_name]
        data = b"\x01\x02\x03"
        expected_crc = algo.compute(data)
        expected_bytes = expected_crc.to_bytes(4, "big")[::-1]  # LE default

        # Act
        _cmd_send(ctx, f"{crc32_name} 01 02 03")

        # Assert
        actual = tx_bytes[0]
        assert actual == data + expected_bytes  # 4-byte LE CRC appended
