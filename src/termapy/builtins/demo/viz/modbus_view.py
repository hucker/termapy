"""Demo visualizer: decode Modbus RTU frames into structured columns.

This visualizer uses the format spec language to map Modbus RTU frame
bytes into named columns (Slave, Func, Addr, Count, CRC, etc.).
Python logic selects the right spec for each message type and
generates dynamic columns for variable-length responses.

Copy this file as a starting point for your own visualizers.
"""

from __future__ import annotations

from termapy.protocol import apply_format, parse_format_spec
from termapy.protocol import diff_columns as proto_diff_columns

NAME = "Modbus"
DESCRIPTION = "Decode Modbus RTU frames into structured columns"
SORT_ORDER = 30

# Modbus function code names for display
_FUNC_NAMES: dict[int, str] = {
    0x01: "RdCoils",
    0x02: "RdInputs",
    0x03: "RdRegs",
    0x04: "RdInRegs",
    0x05: "WrCoil",
    0x06: "WrReg",
    0x0F: "WrCoils",
    0x10: "WrRegs",
}

# Format specs for fixed-length Modbus frames
_READ_REQ = "Slave:H1 Func:H2 Addr:D3-4 Count:D5-6 CRC:crc16-modbus_le"
_WRITE_SINGLE_REG = "Slave:H1 Func:H2 Reg:D3-4 Value:D5-6 CRC:crc16-modbus_le"
_WRITE_COIL = "Slave:H1 Func:H2 Addr:D3-4 Value:H5-6 CRC:crc16-modbus_le"
_WRITE_MULTI = "Slave:H1 Func:H2 Start:D3-4 Count:D5-6 CRC:crc16-modbus_le"
_EXCEPTION = "Slave:H1 ErrFunc:H2 Code:D3 CRC:crc16-modbus_le"


def _read_resp_spec(data: bytes) -> str:
    """Generate dynamic columns for a read response — one per register.

    Args:
        data: Modbus response frame bytes.

    Returns:
        Format spec string with dynamic register columns.
    """
    func = data[1] if len(data) > 1 else 0
    byte_count = data[2] if len(data) > 2 else 0

    if func in (0x03, 0x04) and byte_count >= 2:
        # 16-bit register values
        n_regs = byte_count // 2
        spec = "Slave:H1 Func:H2 Bytes:D3"
        for i in range(n_regs):
            s = 4 + i * 2
            spec += f" R{i}:D{s}-{s + 1}"
        spec += " CRC:crc16-modbus_le"
        return spec

    # Coil/input bit responses or short responses
    spec = "Slave:H1 Func:H2 Bytes:D3 Data:H4-* CRC:crc16-modbus_le"
    return spec


def _pick_spec(data: bytes) -> str:
    """Select the appropriate format spec for a Modbus frame.

    Inspects function code and frame length to choose the right spec.

    Args:
        data: Raw Modbus RTU frame bytes.

    Returns:
        Format spec string.
    """
    if len(data) < 4:
        return "Raw:H1-*"

    func = data[1]

    # Exception response (function code has bit 7 set)
    if func & 0x80:
        return _EXCEPTION

    # Read request: 8 bytes with func 01-04
    if func in (0x01, 0x02, 0x03, 0x04):
        if len(data) == 8:
            return _READ_REQ
        return _read_resp_spec(data)

    # Write single register: func 06, 8 bytes
    if func == 0x06:
        return _WRITE_SINGLE_REG

    # Write single coil: func 05, 8 bytes
    if func == 0x05:
        return _WRITE_COIL

    # Write multiple: func 0F/10
    if func in (0x0F, 0x10):
        if len(data) == 8:
            # Echo response (start + count + CRC)
            return _WRITE_MULTI
        # Request with data payload
        return "Slave:H1 Func:H2 Start:D3-4 Count:D5-6 Bytes:D7 Data:H8-* CRC:crc16-modbus_le"

    # Fallback
    return "Slave:H1 Func:H2 Data:H3-* CRC:crc16-modbus_le"


def format_spec(data: bytes) -> str:
    """Return the format spec string for the given data.

    Args:
        data: Raw Modbus RTU frame bytes.

    Returns:
        Format spec string.
    """
    if not data:
        return ""
    return _pick_spec(data)


def format_columns(data: bytes) -> tuple[list[str], list[str]]:
    """Format Modbus RTU frame bytes as structured columns.

    Args:
        data: Raw frame bytes from the device.

    Returns:
        Tuple of (headers, values).
    """
    if not data:
        return ["Modbus"], [""]
    spec_str = _pick_spec(data)
    return apply_format(data, parse_format_spec(spec_str))


def diff_columns(
    actual: bytes, expected: bytes, mask: bytes,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare actual vs expected Modbus frames with per-column status.

    Uses the expected frame to determine the column layout, then
    compares actual values at each column's byte positions.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0xFF=must match, 0x00=any).

    Returns:
        Tuple of (headers, expected_values, actual_values, statuses).
    """
    if not expected and not actual:
        return ["Modbus"], [""], [""], ["match"]
    # Use expected to determine layout (consistent column count)
    spec_str = _pick_spec(expected)
    cols = parse_format_spec(spec_str)
    return proto_diff_columns(actual, expected, mask, cols)
