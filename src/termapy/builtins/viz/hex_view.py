"""Built-in visualizer: raw hexadecimal byte values."""

from __future__ import annotations

from termapy.protocol import diff_bytes

NAME = "Hex"
DESCRIPTION = "Raw hexadecimal byte values"
SORT_ORDER = 10

# Rich markup styles matching proto_debug _DIFF_STYLE
_STYLES = {
    "match": "bold bright_green",
    "wildcard": "dim",
    "mismatch": "bold red",
    "extra": "bold red",
    "missing": "bold red",
}


def format_bytes(data: bytes) -> str:
    """Format bytes as spaced hex values.

    Each byte produces a 3-character token (``XX ``), with the
    trailing space stripped from the last byte.

    Args:
        data: Raw bytes to format.

    Returns:
        Spaced hex string, e.g. ``"01 FF 0A"``.
    """
    tokens = [f"{b:02X} " for b in data]
    return "".join(tokens).rstrip()


def format_diff(actual: bytes, expected: bytes, mask: bytes) -> str:
    """Format actual bytes with diff coloring as Rich markup.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0x00 = wildcard position).

    Returns:
        Rich-markup string with per-byte colors.
    """
    statuses = diff_bytes(expected, actual, mask)
    parts: list[str] = []
    for i, status in enumerate(statuses):
        style = _STYLES.get(status, "")
        if status == "missing":
            token = "-- "
        else:
            token = f"{actual[i]:02X} "
        parts.append(f"[{style}]{token}[/]")
    result = "".join(parts)
    # Strip trailing space inside the last markup tag
    if result.endswith(" [/]"):
        result = result[:-4] + "[/]"
    return result
