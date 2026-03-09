"""Built-in visualizer: raw hexadecimal byte values."""

from __future__ import annotations

from termapy.protocol import format_diff_markup

NAME = "Hex"
DESCRIPTION = "Raw hexadecimal byte values"
SORT_ORDER = 10


def _hex_token(b: int) -> str:
    """Format a single byte as a 3-char hex token (``XX ``).

    Args:
        b: Byte value.

    Returns:
        Uppercase hex pair followed by a space.
    """
    return f"{b:02X} "


def format_bytes(data: bytes) -> str:
    """Format bytes as spaced hex values.

    Each byte produces a 3-character token (``XX ``), with the
    trailing space stripped from the last byte.

    Args:
        data: Raw bytes to format.

    Returns:
        Spaced hex string, e.g. ``"01 FF 0A"``.
    """
    return "".join(_hex_token(b) for b in data).rstrip()


def format_diff(actual: bytes, expected: bytes, mask: bytes) -> str:
    """Format actual bytes with diff coloring as Rich markup.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0x00 = wildcard position).

    Returns:
        Rich-markup string with per-byte colors.
    """
    return format_diff_markup(actual, expected, mask, _hex_token, "-- ")
