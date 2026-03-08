"""Binary protocol utilities — hex parsing, framing, pattern matching, script parsing.

Pure functions and classes with no Textual or pyserial dependencies.
Used by the ``!!proto`` builtin plugin for binary serial protocol testing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Hex utilities
# ---------------------------------------------------------------------------

_ANSI_ESCAPE = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
_HEX_TOKEN = re.compile(r"(?:0x)?([0-9a-fA-F]{2})")
_QUOTED_STR = re.compile(r'"([^"]*)"')

_ESCAPE_MAP = {
    "\\r": "\r",
    "\\n": "\n",
    "\\t": "\t",
    "\\\\": "\\",
    "\\0": "\0",
}


def strip_ansi(data: bytes) -> bytes:
    """Remove ANSI escape sequences from raw bytes.

    Strips CSI sequences (ESC [ ... letter) commonly used for terminal
    colors and cursor control.

    Args:
        data: Raw bytes potentially containing ANSI escapes.

    Returns:
        Bytes with all ANSI escape sequences removed.
    """
    return _ANSI_ESCAPE.sub(b"", data)


def _unescape(s: str) -> str:
    """Process backslash escapes in a quoted string."""
    for esc, char in _ESCAPE_MAP.items():
        s = s.replace(esc, char)
    return s


def parse_hex(text: str) -> bytes:
    """Parse a hex string into bytes.

    Accepts space-separated hex pairs, optional ``0x`` prefixes, and commas.
    Example: ``'01 03 00 0A'`` or ``'0x01, 0x03, 0x00, 0x0A'``.

    Args:
        text: Hex string to parse.

    Returns:
        Parsed bytes.

    Raises:
        ValueError: If the string contains no valid hex bytes.
    """
    text = text.replace(",", " ")
    tokens = _HEX_TOKEN.findall(text)
    if not tokens:
        raise ValueError(f"No valid hex bytes in: {text!r}")
    return bytes(int(t, 16) for t in tokens)


def parse_data(text: str) -> bytes:
    """Parse mixed hex and quoted text into bytes.

    Quoted strings are UTF-8 encoded with escape support
    (``\\r``, ``\\n``, ``\\t``, ``\\\\``, ``\\0``).
    Hex bytes and quoted strings can be freely mixed.

    Example: ``'02 "HELLO\\r" 03'`` → ``b'\\x02HELLO\\r\\x03'``.

    Args:
        text: Mixed hex/text string.

    Returns:
        Combined bytes.

    Raises:
        ValueError: If no valid hex bytes or quoted strings found.
    """
    result = bytearray()
    pos = 0
    remaining = text.strip()

    while pos < len(remaining):
        # Skip whitespace and commas
        if remaining[pos] in " ,\t":
            pos += 1
            continue

        # Quoted string
        if remaining[pos] == '"':
            m = _QUOTED_STR.match(remaining, pos)
            if not m:
                raise ValueError(f"Unterminated string at position {pos}")
            result.extend(_unescape(m.group(1)).encode("utf-8"))
            pos = m.end()
            continue

        # Hex byte (with optional 0x prefix)
        m = _HEX_TOKEN.match(remaining, pos)
        if m:
            result.append(int(m.group(1), 16))
            pos = m.end()
            continue

        raise ValueError(f"Unexpected character at position {pos}: {remaining[pos]!r}")

    if not result:
        raise ValueError(f"No valid data in: {text!r}")
    return bytes(result)


def format_hex(data: bytes) -> str:
    """Format bytes as a single-line hex string.

    Example: ``b'\\x01\\x03\\x00\\x0a'`` → ``'01 03 00 0A'``.
    """
    return " ".join(f"{b:02X}" for b in data)


_DISPLAY_ESCAPES = {ord("\r"): "\\r", ord("\n"): "\\n", ord("\t"): "\\t", 0: "\\0"}


def format_smart(data: bytes) -> str:
    """Format bytes as a mix of quoted text and hex for readable display.

    Runs of printable ASCII (plus common escapes) are shown as quoted
    strings. Non-printable bytes are shown as hex. Examples::

        b"fw\\r"           → ``"fw\\r"``
        b"\\x02HELLO\\x03" → ``02 "HELLO" 03``
        b"\\x01\\x02\\x03" → ``01 02 03``

    Args:
        data: Raw bytes to format.

    Returns:
        Human-readable mixed hex/text representation.
    """
    if not data:
        return ""
    parts: list[str] = []
    text_buf: list[str] = []

    def _flush_text() -> None:
        if text_buf:
            parts.append('"' + "".join(text_buf) + '"')
            text_buf.clear()

    for b in data:
        if b in _DISPLAY_ESCAPES:
            text_buf.append(_DISPLAY_ESCAPES[b])
        elif 32 <= b < 127:
            text_buf.append(chr(b))
        else:
            _flush_text()
            parts.append(f"{b:02X}")

    _flush_text()
    return " ".join(parts)


def format_spaced(data: bytes, binary: bool = False) -> str:
    """Format bytes with fixed 3-char width per byte for column alignment.

    Each byte occupies exactly 3 characters so that TX, Expected, and
    Actual lines align column-by-column:

    - Hex byte: ``4F `` (2 hex + 1 space)
    - Printable char: ``A  `` (1 char + 2 spaces)
    - Escape sequence: ``\\r `` (2 chars + 1 space)

    Args:
        data: Raw bytes to format.
        binary: If True, always use hex format.

    Returns:
        Fixed-width spaced representation for visual comparison.
    """
    tokens: list[str] = []
    for b in data:
        if binary:
            tokens.append(f"{b:02X} ")
        elif b in _DISPLAY_ESCAPES:
            tokens.append(f"{_DISPLAY_ESCAPES[b]} ")
        elif 32 <= b < 127:
            tokens.append(f"{chr(b)}  ")
        else:
            tokens.append(f"{b:02X} ")
    return "".join(tokens).rstrip()


def format_hex_dump(data: bytes, width: int = 16) -> list[str]:
    """Format bytes as a multi-line hex dump with offset and ASCII sidebar.

    Args:
        data: Bytes to format.
        width: Number of bytes per line (default 16).

    Returns:
        List of formatted lines like:
        ``'0000 | 01 03 00 0A 48 45 4C 4C  4F 00 00 00 00 00 00 00 | ....HELL O.......'``
    """
    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        # Hex part with gap in middle
        hex_parts = []
        for i, b in enumerate(chunk):
            if i == width // 2:
                hex_parts.append(" ")
            hex_parts.append(f"{b:02X}")
        hex_str = " ".join(hex_parts)
        # Pad to fixed width
        full_hex_len = width * 3 + 1  # extra space in middle
        hex_str = hex_str.ljust(full_hex_len)
        # ASCII part
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{offset:04X} | {hex_str}| {ascii_str}")
    return lines


# ---------------------------------------------------------------------------
# Pattern matching
# ---------------------------------------------------------------------------


def parse_pattern(text: str) -> tuple[bytes, bytes]:
    """Parse a pattern with wildcards into (data, mask).

    ``**`` in the pattern becomes a wildcard (mask byte ``0x00``).
    All other bytes have mask ``0xFF`` (must match exactly).
    Quoted text is converted to bytes with mask ``0xFF`` per byte.

    Args:
        text: Pattern string, e.g. ``'01 03 ** ** "OK\\r"'``.

    Returns:
        Tuple of (expected_data, mask). Wildcard positions have
        data=0x00 and mask=0x00.
    """
    data = bytearray()
    mask = bytearray()
    pos = 0
    text = text.strip()

    while pos < len(text):
        # Skip whitespace and commas
        if text[pos] in " ,\t":
            pos += 1
            continue

        # Wildcard
        if text[pos : pos + 2] == "**":
            data.append(0x00)
            mask.append(0x00)
            pos += 2
            continue

        # Quoted string — every byte must match
        if text[pos] == '"':
            m = _QUOTED_STR.match(text, pos)
            if not m:
                raise ValueError(f"Unterminated string at position {pos}")
            for b in _unescape(m.group(1)).encode("utf-8"):
                data.append(b)
                mask.append(0xFF)
            pos = m.end()
            continue

        # Hex byte — must match
        m = _HEX_TOKEN.match(text, pos)
        if m:
            data.append(int(m.group(1), 16))
            mask.append(0xFF)
            pos = m.end()
            continue

        raise ValueError(f"Unexpected character at position {pos}: {text[pos]!r}")

    return bytes(data), bytes(mask)


def match_response(expected: bytes, actual: bytes, mask: bytes) -> bool:
    """Compare actual response against expected, respecting wildcard mask.

    Mask byte ``0xFF`` = must match, ``0x00`` = any value accepted.
    Length must match exactly (no partial matches).

    Args:
        expected: Expected byte pattern.
        actual: Actual received bytes.
        mask: Bitmask (same length as expected).

    Returns:
        True if actual matches expected at all non-wildcard positions.
    """
    if len(actual) != len(expected):
        return False
    return all(
        (a & m) == (e & m) for a, e, m in zip(actual, expected, mask)
    )


# ---------------------------------------------------------------------------
# Frame collector
# ---------------------------------------------------------------------------


class FrameCollector:
    """Accumulate raw bytes and emit complete frames based on timeout gap.

    A silence gap (default 50ms) after receiving data marks the end of a
    frame. This is the framing method used by Modbus RTU and many simple
    embedded protocols.

    Args:
        timeout_ms: Silence gap in milliseconds to consider a frame complete.
    """

    def __init__(self, timeout_ms: int = 50) -> None:
        self._timeout_s = timeout_ms / 1000.0
        self._buf = bytearray()
        self._last_feed: float | None = None

    def feed(self, data: bytes, now: float | None = None) -> bytes | None:
        """Feed received bytes into the collector.

        Args:
            data: Raw bytes received from the serial port.
            now: Current timestamp (seconds). Defaults to ``time.monotonic()``.

        Returns:
            Complete frame bytes if a timeout gap was detected before this
            new data (meaning the previous data was a complete frame),
            or None if still accumulating.
        """
        if now is None:
            now = time.monotonic()

        completed = None
        # If we had data buffered and enough silence elapsed, emit it
        if self._buf and self._last_feed is not None:
            gap = now - self._last_feed
            if gap >= self._timeout_s:
                completed = bytes(self._buf)
                self._buf.clear()

        self._buf.extend(data)
        self._last_feed = now
        return completed

    def flush(self, now: float | None = None) -> bytes | None:
        """Flush any buffered data as a complete frame if timeout has elapsed.

        Call this periodically (or after waiting) to emit frames that ended
        with silence.

        Args:
            now: Current timestamp (seconds). Defaults to ``time.monotonic()``.

        Returns:
            Complete frame bytes, or None if no data or timeout not yet reached.
        """
        if now is None:
            now = time.monotonic()

        if self._buf and self._last_feed is not None:
            gap = now - self._last_feed
            if gap >= self._timeout_s:
                completed = bytes(self._buf)
                self._buf.clear()
                self._last_feed = None
                return completed
        return None

    def reset(self) -> None:
        """Discard any buffered data and reset state."""
        self._buf.clear()
        self._last_feed = None

    @property
    def pending(self) -> int:
        """Number of bytes currently buffered."""
        return len(self._buf)


# ---------------------------------------------------------------------------
# Proto script parsing
# ---------------------------------------------------------------------------


@dataclass
class Step:
    """A single step in a protocol test script.

    Attributes:
        action: Step type — ``"send"``, ``"expect"``, ``"delay"``.
        data: Byte payload for send/expect steps.
        mask: Wildcard mask for expect steps (0xFF=match, 0x00=any).
        timeout_ms: Timeout for expect steps.
        label: Human-readable step label for display.
    """

    action: str
    data: bytes = b""
    mask: bytes = b""
    timeout_ms: int = 1000
    label: str = ""
    binary: bool = False


def _parse_duration_ms(text: str) -> int:
    """Parse a duration string like '500ms' or '1.5s' to milliseconds."""
    text = text.strip().lower()
    if text.endswith("ms"):
        return int(float(text[:-2]))
    if text.endswith("s"):
        return int(float(text[:-1]) * 1000)
    return int(text)


def parse_proto_script(text: str) -> tuple[dict, list[Step]]:
    """Parse a ``.pro`` script into settings and steps.

    Script format supports:
    - ``# comments``
    - ``@timeout 1000ms`` / ``@frame_gap 50ms`` / ``@strip_ansi`` directives
    - ``label: <text>`` — name for the next step
    - ``send: <hex or "text">`` — bytes to transmit
    - ``expect: <pattern>`` — expected response (with ``**`` wildcards)
    - ``timeout: <duration>`` — per-step timeout override
    - ``delay: <duration>`` — pause between steps

    Args:
        text: Full script file content.

    Returns:
        Tuple of (settings_dict, step_list). Settings include ``timeout_ms``
        and ``frame_gap_ms`` from ``@`` directives.

    Raises:
        ValueError: If the script contains syntax errors.
    """
    settings: dict = {
        "timeout_ms": 1000,
        "frame_gap_ms": 50,
        "strip_ansi": False,
        "name": "",
    }
    steps: list[Step] = []
    current_label = ""
    current_timeout: int | None = None

    for lineno, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()

        # Skip blank lines and comments
        if not line or line.startswith("#"):
            continue

        # @ directives
        if line.startswith("@"):
            parts = line[1:].split(None, 1)
            if not parts:
                raise ValueError(f"Line {lineno}: invalid directive: {line}")
            key = parts[0]
            val = parts[1] if len(parts) > 1 else ""
            if key == "timeout":
                if not val:
                    raise ValueError(f"Line {lineno}: invalid directive: {line}")
                settings["timeout_ms"] = _parse_duration_ms(val)
            elif key == "frame_gap":
                if not val:
                    raise ValueError(f"Line {lineno}: invalid directive: {line}")
                settings["frame_gap_ms"] = _parse_duration_ms(val)
            elif key == "strip_ansi":
                settings["strip_ansi"] = True
            elif key == "quiet":
                steps.append(Step(action="quiet"))
            elif key == "loud":
                steps.append(Step(action="loud"))
            elif key == "name":
                if not val:
                    raise ValueError(f"Line {lineno}: @name requires a value")
                settings["name"] = val
            else:
                raise ValueError(f"Line {lineno}: unknown directive: @{key}")
            continue

        # Colon-separated directives
        colon = line.find(":")
        if colon < 0:
            raise ValueError(f"Line {lineno}: expected 'key: value', got: {line}")

        key = line[:colon].strip().lower()
        val = line[colon + 1 :].strip()

        if key == "label":
            current_label = val
        elif key == "timeout":
            current_timeout = _parse_duration_ms(val)
        elif key == "send":
            data = parse_data(val)
            is_binary = not val.strip().startswith('"')
            steps.append(Step(
                action="send",
                data=data,
                label=current_label,
                binary=is_binary,
            ))
            current_label = ""
        elif key == "expect":
            exp_data, exp_mask = parse_pattern(val)
            is_binary = not val.strip().startswith('"')
            timeout = current_timeout if current_timeout is not None else settings["timeout_ms"]
            steps.append(Step(
                action="expect",
                data=exp_data,
                mask=exp_mask,
                timeout_ms=timeout,
                label=current_label,
                binary=is_binary,
            ))
            current_label = ""
            current_timeout = None
        elif key == "delay":
            steps.append(Step(
                action="delay",
                timeout_ms=_parse_duration_ms(val),
                label=current_label,
            ))
            current_label = ""
        elif key == "cmd":
            steps.append(Step(
                action="cmd",
                data=val.encode("utf-8"),
                label=current_label,
            ))
            current_label = ""
        elif key == "flush":
            steps.append(Step(
                action="flush",
                timeout_ms=_parse_duration_ms(val),
                label=current_label,
            ))
            current_label = ""
        else:
            raise ValueError(f"Line {lineno}: unknown key: {key}")

    return settings, steps
