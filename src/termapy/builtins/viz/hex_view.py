"""Built-in visualizer: raw hexadecimal byte values.

Single-column layout using ``h1-*`` - all bytes displayed as
spaced hex in one column with per-byte diff coloring.
"""

from __future__ import annotations

from termapy.protocol import apply_format, parse_format_spec
from termapy.protocol import diff_columns as proto_diff_columns

NAME = "Hex"
DESCRIPTION = "Raw hexadecimal byte values"
SORT_ORDER = 10

_SPEC = "Hex:h1-*"


def format_spec(data: bytes) -> str:
    """Return the format spec string for the given data.

    Args:
        data: Raw bytes (unused - spec is fixed).

    Returns:
        Format spec string.
    """
    return _SPEC


def format_columns(data: bytes) -> tuple[list[str], list[str]]:
    """Format bytes as a single hex column.

    Args:
        data: Raw bytes to format.

    Returns:
        Tuple of (headers, values).
    """
    if not data:
        return ["Hex"], [""]
    return apply_format(data, parse_format_spec(_SPEC))


def diff_columns(
    actual: bytes, expected: bytes, mask: bytes,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare actual vs expected as hex columns.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0xFF=must match, 0x00=any).

    Returns:
        Tuple of (headers, expected_values, actual_values, statuses).
    """
    if not expected and not actual:
        return ["Hex"], [""], [""], ["match"]
    cols = parse_format_spec(_SPEC)
    return proto_diff_columns(actual, expected, mask, cols)
