"""Demo visualizer: decode a status register packet with bit fields.

Demonstrates the format spec bit field syntax:
- ``B1.0`` — single bit from byte 1, bit 0
- ``B1-2.8-11`` — 4-bit field spanning bytes 1-2, bits 8-11
- ``_`` padding for reserved bytes

The demo packet layout (8 bytes):
  Byte 1:   Device ID (H)
  Byte 2:   Status register — individual bit flags
  Byte 3-4: Control register — contains bit fields
  Byte 5-6: Sensor value (unsigned 16-bit)
  Byte 7:   Reserved (padding)
  Byte 8:   Checksum (sum8)

Copy this file as a starting point for your own visualizers.
"""

from __future__ import annotations

from termapy.protocol import apply_format, parse_format_spec
from termapy.protocol import diff_columns as proto_diff_columns

NAME = "Bitfield"
DESCRIPTION = "Decode status/control register packets with bit fields"
SORT_ORDER = 40

# Status register (byte 2) — individual bits
# Control register (bytes 3-4, big-endian) — bit field ranges
_SPEC = (
    "ID:H1 "
    "Run:B2.0 Err:B2.1 Rdy:B2.7 "
    "Mode:B3-4.0-2 Chan:B3-4.3-5 Gain:B3-4.8-11 "
    "Sensor:U5-6 "
    "_:_7 "
    "Sum:sum8"
)


def format_spec(data: bytes) -> str:
    """Return the format spec string for the given data."""
    return _SPEC


def format_columns(data: bytes) -> tuple[list[str], list[str]]:
    """Format packet bytes as structured bit field columns.

    Args:
        data: Raw packet bytes (8 bytes expected).

    Returns:
        Tuple of (headers, values).
    """
    if not data:
        return ["Bitfield"], [""]
    return apply_format(data, parse_format_spec(_SPEC))


def diff_columns(
    actual: bytes, expected: bytes, mask: bytes,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare actual vs expected with per-column status.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0xFF=must match, 0x00=any).

    Returns:
        Tuple of (headers, expected_values, actual_values, statuses).
    """
    if not expected and not actual:
        return ["Bitfield"], [""], [""], ["match"]
    cols = parse_format_spec(_SPEC)
    return proto_diff_columns(actual, expected, mask, cols)
