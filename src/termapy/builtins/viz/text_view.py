"""Built-in visualizer: ASCII text with escape sequences."""

from __future__ import annotations

from termapy.protocol import diff_bytes

NAME = "Text"
DESCRIPTION = "ASCII text with escape sequences"
SORT_ORDER = 20

# Escape sequence display map
_ESCAPES = {ord("\r"): "\\r", ord("\n"): "\\n", ord("\t"): "\\t", 0: "\\0"}

# Rich markup styles matching proto_debug _DIFF_STYLE
_STYLES = {
    "match": "bold bright_green",
    "wildcard": "dim",
    "mismatch": "bold red",
    "extra": "bold red",
    "missing": "bold red",
}


def _token(b: int) -> str:
    """Format a single byte as a text token.

    Args:
        b: Byte value.

    Returns:
        Fixed-width token: escape (2 chars), printable char + space
        (2 chars), or ``. `` for unprintable (2 chars).
    """
    if b in _ESCAPES:
        return _ESCAPES[b]
    if 32 <= b < 127:
        return f"{chr(b)} "
    return ". "


def format_bytes(data: bytes) -> str:
    """Format bytes as spaced ASCII text with escapes.

    Each byte produces a 2-character token: printable char + space,
    escape sequence (``\\r``, ``\\n``, etc.), or ``. `` for unprintable.

    Args:
        data: Raw bytes to format.

    Returns:
        Spaced text string, e.g. ``"H e l l o \\r\\n"``.
    """
    tokens = [_token(b) for b in data]
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
            token = ". "
        else:
            token = _token(actual[i])
        parts.append(f"[{style}]{token}[/]")
    result = "".join(parts)
    # Strip trailing space inside the last markup tag
    if result.endswith(" [/]"):
        result = result[:-4] + "[/]"
    return result
