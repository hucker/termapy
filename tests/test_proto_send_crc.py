"""Tests for proto.send CRC-append feature."""

import pytest

from termapy.plugins import InternalHandle, PluginContext, UsageError
from termapy.builtins.commands.proto import _cmd_send, _parse_send_algo
from termapy.protocol import get_crc_registry


@pytest.fixture
def send_env():
    """Create a PluginContext that captures writes and serial output."""
    output = []
    tx_bytes = []

    from termapy.plugins import IOHandle, SerialHandle
    ctx = PluginContext(
        internal=InternalHandle(),
        io=IOHandle(_write=lambda text, color=None: output.append((text, color))),
        serial=SerialHandle(
            is_connected=lambda: True,
            write=lambda data: tx_bytes.append(data),
            read_raw=lambda timeout_ms=1000, frame_gap_ms=0: b"",
        ),
    )
    # Seed the `flags` namespace (would be done by app.py._build_context).
    flags = ctx.ns("flags")
    flags["echo"] = True
    flags["output_level"] = "verbose"
    flags["hex"] = False
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

        # Assert
        assert actual_name == "crc16-modbus", "exact match, default LE, no ascii"
        assert actual_be is False, "bare algo defaults to LE"
        assert actual_ascii is False, "bare algo defaults to binary"

    def test_algo_be(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_be", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "BE suffix stripped from name"
        assert actual_be is True, "_be suffix sets big-endian"
        assert actual_ascii is False, "_be does not set ascii mode"

    def test_algo_le(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_le", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "LE suffix stripped from name"
        assert actual_be is False, "explicit _le keeps little-endian"
        assert actual_ascii is False, "_le does not set ascii mode"

    def test_algo_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_ascii", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "ascii suffix stripped from name"
        assert actual_be is False, "_ascii defaults to LE"
        assert actual_ascii is True, "_ascii sets ascii mode"

    def test_algo_be_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_be_ascii", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "both suffixes stripped from name"
        assert actual_be is True, "_be_ascii sets big-endian"
        assert actual_ascii is True, "_be_ascii sets ascii mode"

    def test_algo_le_ascii(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, actual_ascii = _parse_send_algo(
            "crc16-modbus_le_ascii", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "le_ascii suffixes stripped from name"
        assert actual_be is False, "_le_ascii keeps little-endian"
        assert actual_ascii is True, "_le_ascii sets ascii mode"

    def test_unknown_returns_none(self):
        # Act
        registry = get_crc_registry()
        actual_name, _, _ = _parse_send_algo("not-an-algo", registry)

        # Assert
        assert actual_name is None, "unknown algo returns None"

    def test_case_insensitive(self):
        # Act
        registry = get_crc_registry()
        actual_name, actual_be, _ = _parse_send_algo(
            "CRC16-MODBUS_BE", registry,
        )

        # Assert
        assert actual_name == "crc16-modbus", "case folded to lowercase"
        assert actual_be is True, "BE suffix recognized case-insensitively"


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
        assert actual == expected, "data with LE CRC appended"

    def test_crc16_modbus_be(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_be 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected = b"\x01\x03\x00\x00\x00\x01\x0A\x84"
        assert actual == expected, "data with BE CRC appended"

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
        assert actual == expected, "data with LE ASCII CRC appended"

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
        assert actual == expected, "data with BE ASCII CRC appended"

    def test_crc_info_displayed(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert len(actual) == 1, "CRC info line shown"
        assert "0x0A84" in actual[0], "CRC value displayed"
        assert "LE" in actual[0], "endian label shown"
        assert "bin" in actual[0], "mode label shown"

    def test_crc_info_be_ascii(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_be_ascii 01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert "BE" in actual[0], "BE label"
        assert "ascii" in actual[0], "ascii mode label"


class TestSendCrcEdgeCases:
    """Error handling and edge cases for CRC-append."""

    def test_no_data_after_algo(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus"

        # Act
        result = _cmd_send(ctx, args)

        # Assert
        assert len(tx_bytes) == 0, "nothing sent"
        assert not result.success, "handler reports failure"
        assert "No data" in result.error, "error mentions no data"

    def test_no_data_after_algo_with_suffix(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "crc16-modbus_ascii"

        # Act
        result = _cmd_send(ctx, args)

        # Assert
        assert len(tx_bytes) == 0, "nothing sent"
        assert not result.success, "handler reports failure"
        assert "No data" in result.error, "error mentions no data"

    def test_no_algo_sends_raw(self, send_env):
        # Arrange - first word is NOT a CRC algo, so plain send
        ctx, output, tx_bytes = send_env
        args = "01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = tx_bytes[0]
        expected = b"\x01\x03\x00\x00\x00\x01"
        assert actual == expected, "raw bytes, no CRC appended"

    def test_no_crc_info_without_algo(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        args = "01 03 00 00 00 01"

        # Act
        _cmd_send(ctx, args)

        # Assert
        actual = [t for t, _ in output if "CRC:" in t]
        assert len(actual) == 0, "no CRC info line"

    # Note: the old test_not_connected unit test was removed when the
    # "not connected" guard was promoted to a first-class capability
    # (Command.needs.serial_connected).  Dispatch-level coverage for the
    # capability gate lives in test_engine.TestDispatchCapabilities.

    def test_empty_args(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env

        # Act / Assert -- bad arity raises; dispatcher renders the usage
        # line from the declaration (see test_usage_error.py).
        with pytest.raises(UsageError):
            _cmd_send(ctx, "")
        assert len(tx_bytes) == 0, "nothing sent"


class TestSendCrcAlgorithms:
    """Verify CRC-append works with different algorithm widths."""

    def test_crc8(self, send_env):
        # Arrange - use a CRC-8 algorithm
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
        assert actual == data + bytes([expected_crc]), "1-byte CRC appended"

    def test_crc32(self, send_env):
        # Arrange - use a refout=True 4-byte CRC so the natural-wire
        # default lands on LE.  Iterate the registry rather than naming
        # a specific algorithm so the test survives reordering, but
        # filter on refout=True (matches the v0.71 byte-order default:
        # refout=True -> LE, refout=False -> BE).
        ctx, output, tx_bytes = send_env
        registry = get_crc_registry()
        crc32_name = next(
            (n for n in registry
             if registry[n].width == 4 and registry[n].refout),
            None,
        )
        if crc32_name is None:
            pytest.skip("No refout=True 4-byte CRC available")
        algo = registry[crc32_name]
        data = b"\x01\x02\x03"
        expected_crc = algo.compute(data)
        expected_bytes = expected_crc.to_bytes(4, "big")[::-1]  # refout=True -> LE

        # Act
        _cmd_send(ctx, f"{crc32_name} 01 02 03")

        # Assert
        actual = tx_bytes[0]
        assert actual == data + expected_bytes, "4-byte LE CRC appended"


class TestSendDryRun:
    """``--dry-run`` skips serial I/O and prints the bytes that would be sent."""

    def test_dry_run_skips_write(self, send_env):
        # Arrange
        ctx, output, tx_bytes = send_env
        ctx.active_flags = {"--dry-run"}

        # Act
        _cmd_send(ctx, "01 02 03")

        # Assert
        assert tx_bytes == [], "no bytes written to the port"
        actual_lines = [t for t, _ in output if "TX (dry-run)" in t]
        assert actual_lines, "TX (dry-run) line shown"

    def test_dry_run_default_uses_dot_sidebar(self, send_env):
        # Regression: dry-run prints hex bytes + an ASCII sidebar
        # (``|......|`` for non-printable), NOT the dual
        # hex + "\0\0\0\n" smart-text rendering the old default used.
        ctx, output, _ = send_env
        ctx.active_flags = {"--dry-run"}

        # Act -- a frame entirely of non-printable bytes.
        _cmd_send(ctx, "crc16-modbus 01 03 00 00 00 0A")

        # Assert
        actual_lines = [t for t, _ in output if "TX (dry-run)" in t]
        assert actual_lines, "TX (dry-run) line printed"
        line = actual_lines[0]
        assert "|" in line and "......" in line, (
            f"dot-sidebar present; got: {line!r}"
        )
        assert '"' not in line, (
            f"no quoted escape-text in default mode; got: {line!r}"
        )
        assert "\\0" not in line, "no escape rendering of null bytes"

    def test_dry_run_with_ascii_flag_uses_quoted_text(self, send_env):
        # ``--ascii`` switches to the quoted escape-text rendering.
        ctx, output, _ = send_env
        ctx.active_flags = {"--dry-run", "--ascii"}

        # Act -- mixed printable ("AT") + control bytes (\r\n).
        _cmd_send(ctx, '"AT" 0D 0A')

        # Assert
        actual_lines = [t for t, _ in output if "TX (dry-run)" in t]
        assert actual_lines, "TX (dry-run) line printed"
        line = actual_lines[0]
        assert '"AT\\r\\n"' in line, (
            f"--ascii renders as quoted escape-text; got: {line!r}"
        )

    def test_dry_run_value_is_frame_hex(self, send_env):
        # Arrange -- crc16-modbus is refout=True so natural wire order is LE.
        ctx, output, tx_bytes = send_env
        ctx.active_flags = {"--dry-run"}
        registry = get_crc_registry()
        data = b"\x01\x03\x00\x00\x00\x0a"
        crc_int = registry["crc16-modbus"].compute(data)
        crc_le = crc_int.to_bytes(2, "big")[::-1]

        # Act
        result = _cmd_send(ctx, "crc16-modbus 01 03 00 00 00 0A")

        # Assert
        actual = result.value
        expected = (data + crc_le).hex()
        assert tx_bytes == [], "no bytes written"
        assert actual == expected, "result.value carries the assembled frame as hex"

    def test_dry_run_works_when_disconnected(self):
        # Arrange -- is_connected returns False; --dry-run must still succeed.
        from termapy.plugins import IOHandle, SerialHandle
        output: list = []
        tx_bytes: list = []
        ctx = PluginContext(
            internal=InternalHandle(),
            io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: False,
                write=lambda data: tx_bytes.append(data),
                read_raw=lambda timeout_ms=1000, frame_gap_ms=0: b"",
            ),
        )
        ctx.ns("flags")["output_level"] = "normal"
        ctx.active_flags = {"--dry-run"}

        # Act
        result = _cmd_send(ctx, "01 02 03")

        # Assert
        assert tx_bytes == [], "no bytes written when disconnected"
        assert result.success, "dry-run succeeds without a connection"

    def test_no_dry_run_when_disconnected_fails(self):
        # Arrange -- no --dry-run, no connection: should refuse cleanly.
        from termapy.plugins import IOHandle, SerialHandle
        output: list = []
        tx_bytes: list = []
        ctx = PluginContext(
            internal=InternalHandle(),
            io=IOHandle(_write=lambda t, c=None: output.append((t, c))),
            serial=SerialHandle(
                is_connected=lambda: False,
                write=lambda data: tx_bytes.append(data),
            ),
        )
        ctx.ns("flags")["output_level"] = "normal"

        # Act
        result = _cmd_send(ctx, "01 02 03")

        # Assert
        actual = result.error
        expected = "Not connected."
        assert tx_bytes == [], "no bytes written"
        assert not result.success, "handler reports failure"
        assert actual == expected, "error matches the standard 'Not connected.' message"
