"""Demo visualizer: decode AT command responses into readable fields.

This visualizer is a working example showing how to build a termapy
packet visualizer.  It parses AT-style responses (``+KEY:VALUE``,
``OK``, ``ERROR:``) and displays them as labeled fields instead of
raw bytes.

Copy this file as a starting point for your own visualizers.
"""

from __future__ import annotations

from termapy.protocol import diff_bytes, DIFF_STYLES

NAME = "AT"
DESCRIPTION = "Decode AT command responses into labeled fields"
SORT_ORDER = 30


def _decode(data: bytes) -> str:
    """Decode raw bytes into an AT response summary.

    Recognizes three patterns:
    - ``+KEY:VALUE`` — shows ``KEY = VALUE``
    - ``OK`` / ``ERROR:...`` — shows as-is
    - Everything else — falls back to printable ASCII with dots

    Args:
        data: Raw response bytes.

    Returns:
        Human-readable single-line summary.
    """
    text = data.decode("ascii", errors="replace").strip()
    lines = text.splitlines()
    if not lines:
        return "(empty)"

    parts = []
    for line in lines:
        line = line.strip()
        if line.startswith("+") and ":" in line:
            key, _, value = line[1:].partition(":")
            parts.append(f"{key.strip()}={value.strip()}")
        elif line in ("OK",):
            parts.append("OK")
        elif line.startswith("ERROR"):
            parts.append(line)
        else:
            parts.append(line)
    return " | ".join(parts)


def format_bytes(data: bytes) -> str:
    """Format AT response bytes as decoded field summary.

    Args:
        data: Raw response bytes from the device.

    Returns:
        Decoded summary, e.g. ``"PROD-ID=BASSOMATIC-77"`` or
        ``"LED=OFF | Uptime=0h 0m 6s | Connections=1"``.
    """
    return _decode(data)


def format_diff(actual: bytes, expected: bytes, mask: bytes) -> str:
    """Format actual bytes with diff coloring as Rich markup.

    Compares at the byte level for match/mismatch status, then
    displays the decoded AT summary with overall pass/fail color.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0xFF=must match, 0x00=any).

    Returns:
        Rich-markup string with pass/fail coloring.
    """
    statuses = diff_bytes(expected, actual, mask)
    has_mismatch = any(s in ("mismatch", "missing", "extra") for s in statuses)
    decoded = _decode(actual)
    if has_mismatch:
        return f"[{DIFF_STYLES['mismatch']}]{decoded}[/]"
    return f"[{DIFF_STYLES['match']}]{decoded}[/]"
