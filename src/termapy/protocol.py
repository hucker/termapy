"""Binary protocol utilities - hex parsing, framing, pattern matching, script parsing.

Pure functions and classes with no Textual or pyserial dependencies.
Used by the ``/proto`` builtin plugin for binary serial protocol testing.
"""

from __future__ import annotations

import re
import struct
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from termapy.protocol_crc import get_crc_registry

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

    Example: ``'02 "HELLO\\r" 03'`` -> ``b'\\x02HELLO\\r\\x03'``.

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

    Example: ``b'\\x01\\x03\\x00\\x0a'`` -> ``'01 03 00 0A'``.
    """
    return " ".join(f"{b:02X}" for b in data)


_DISPLAY_ESCAPES = {ord("\r"): "\\r", ord("\n"): "\\n", ord("\t"): "\\t", 0: "\\0"}


def format_smart(data: bytes) -> str:
    """Format bytes as a mix of quoted text and hex for readable display.

    Runs of printable ASCII (plus common escapes) are shown as quoted
    strings. Non-printable bytes are shown as hex. Examples::

        b"fw\\r"           -> ``"fw\\r"``
        b"\\x02HELLO\\x03" -> ``02 "HELLO" 03``
        b"\\x01\\x02\\x03" -> ``01 02 03``

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
    """Format bytes with fixed width per byte for column alignment.

    Two modes with consistent per-byte width:

    - **Hex mode** (``binary=True``): ``XX `` - 3 chars per byte.
    - **Text mode** (``binary=False``): ``c `` - 2 chars per byte.
      Escapes render as ``\\r``, ``\\n``, ``\\t``, ``\\0`` (2 chars).
      Other unprintable bytes render as ``.`` + space (2 chars).

    Args:
        data: Raw bytes to format.
        binary: If True, use hex format (3 chars/byte).
            If False, use text format (2 chars/byte).

    Returns:
        Fixed-width spaced representation for visual comparison.
    """
    tokens: list[str] = []
    for b in data:
        if binary:
            tokens.append(f"{b:02X} ")
        elif b in _DISPLAY_ESCAPES:
            tokens.append(f"{_DISPLAY_ESCAPES[b]}")
        elif 32 <= b < 127:
            tokens.append(f"{chr(b)} ")
        else:
            tokens.append(". ")
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

        # Quoted string - every byte must match
        if text[pos] == '"':
            m = _QUOTED_STR.match(text, pos)
            if not m:
                raise ValueError(f"Unterminated string at position {pos}")
            for b in _unescape(m.group(1)).encode("utf-8"):
                data.append(b)
                mask.append(0xFF)
            pos = m.end()
            continue

        # Hex byte - must match
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
    Length must match exactly - overflow (extra bytes) is a failure.

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
        action: Step type - ``"send"``, ``"expect"``, ``"delay"``.
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
    - ``label: <text>`` - name for the next step
    - ``send: <hex or "text">`` - bytes to transmit
    - ``expect: <pattern>`` - expected response (with ``**`` wildcards)
    - ``timeout: <duration>`` - per-step timeout override
    - ``delay: <duration>`` - pause between steps

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


# ---------------------------------------------------------------------------
# Structured TOML-based proto scripts
# ---------------------------------------------------------------------------


@dataclass
class TestCase:
    """A single send/expect test from a structured proto script.

    Attributes:
        index: 1-based test number.
        name: Human-readable test name.
        setup: Commands to run before send (e.g. ``["echo off"]``).
        teardown: Commands to run after send/expect.
        send_data: Parsed bytes to transmit.
        send_raw: Original send string from script (for display).
        expect_data: Expected response bytes.
        expect_mask: Wildcard mask (0xFF=match, 0x00=any).
        expect_raw: Original expect string from script (for display).
        binary: True if send value is hex (not quoted text).
        timeout_ms: Per-test timeout override.
        viz: Visualizer name that must be displayed for this test.
        send_fmt: Inline format spec for TX data (e.g. ``"Slave:H1 Func:H2"``).
        expect_fmt: Inline format spec for RX data.
    """

    index: int
    name: str
    setup: list[str] = field(default_factory=list)
    teardown: list[str] = field(default_factory=list)
    send_data: bytes = b""
    send_raw: str = ""
    expect_data: bytes = b""
    expect_mask: bytes = b""
    expect_raw: str = ""
    binary: bool = False
    timeout_ms: int = 1000
    viz: str = ""
    send_fmt: str = ""
    expect_fmt: str = ""


@dataclass
class ProtoScript:
    """Parsed structured proto script.

    Attributes:
        name: Script display name.
        frame_gap_ms: Silence gap for frame detection.
        timeout_ms: Default expect timeout.
        strip_ansi: Strip ANSI escapes from responses.
        quiet: Suppress setup/teardown output.
        setup: List of command strings to run before tests.
        teardown: List of command strings to run after tests.
        tests: Ordered list of test cases.
        viz: Allowed visualizer names (empty = all available).
        send_fmt: Default inline format spec for TX data.
        expect_fmt: Default inline format spec for RX data.
    """

    name: str = ""
    frame_gap_ms: int = 50
    timeout_ms: int = 1000
    strip_ansi: bool = False
    quiet: bool = False
    setup: list[str] = field(default_factory=list)
    teardown: list[str] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    viz: list[str] = field(default_factory=list)
    send_fmt: str = ""
    expect_fmt: str = ""
    json_file: str = ""


def parse_toml_script(text: str) -> ProtoScript:
    """Parse a TOML-format ``.pro`` script into a ``ProtoScript``.

    Args:
        text: TOML file content.

    Returns:
        Parsed ProtoScript with settings and test cases.

    Raises:
        ValueError: If the script is missing required fields or has
            invalid data.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"TOML parse error: {e}") from e

    if "test" not in doc:
        raise ValueError("TOML script must contain at least one [[test]] section")

    # Parse settings
    default_timeout = 1000
    frame_gap_ms = 50
    if "timeout" in doc:
        default_timeout = _parse_duration_ms(str(doc["timeout"]))
    if "frame_gap" in doc:
        frame_gap_ms = _parse_duration_ms(str(doc["frame_gap"]))

    script = ProtoScript(
        name=doc.get("name", ""),
        frame_gap_ms=frame_gap_ms,
        timeout_ms=default_timeout,
        strip_ansi=doc.get("strip_ansi", False),
        quiet=doc.get("quiet", False),
        setup=doc.get("setup", []),
        teardown=doc.get("teardown", []),
        viz=doc.get("viz", []),
        send_fmt=doc.get("send_fmt", ""),
        expect_fmt=doc.get("expect_fmt", doc.get("recv_fmt", "")),  # recv_fmt compat - remove after v7
        json_file=doc.get("json_file", ""),
    )

    # Parse test cases
    for i, entry in enumerate(doc["test"], 1):
        if "send" not in entry:
            raise ValueError(f"Test {i}: missing 'send' field")
        if "expect" not in entry:
            raise ValueError(f"Test {i}: missing 'expect' field")

        send_raw = entry["send"]
        expect_raw = entry["expect"]
        send_data = parse_data(send_raw)
        expect_data, expect_mask = parse_pattern(expect_raw)
        is_binary = not send_raw.strip().startswith('"')

        timeout = default_timeout
        if "timeout" in entry:
            timeout = _parse_duration_ms(str(entry["timeout"]))

        # Per-test setup/teardown - support both list and legacy "cmd" string
        test_setup: list[str] = entry.get("setup", [])
        if not test_setup and "cmd" in entry:
            test_setup = [entry["cmd"]]
        test_teardown: list[str] = entry.get("teardown", [])

        script.tests.append(TestCase(
            index=i,
            name=entry.get("name", f"Test {i}"),
            setup=test_setup,
            teardown=test_teardown,
            send_data=send_data,
            send_raw=send_raw,
            expect_data=expect_data,
            expect_mask=expect_mask,
            expect_raw=expect_raw,
            binary=is_binary,
            timeout_ms=timeout,
            viz=entry.get("viz", ""),
            send_fmt=entry.get("send_fmt", script.send_fmt),
            expect_fmt=entry.get("expect_fmt", entry.get("recv_fmt", script.expect_fmt)),  # recv_fmt compat - remove after v7
        ))

    return script


def load_proto_script(text: str) -> tuple[str, ProtoScript | tuple[dict, list[Step]]]:
    """Auto-detect script format and parse accordingly.

    Tries TOML first (looks for ``[[test]]`` sections). Falls back
    to the flat ``.pro`` format.

    Args:
        text: File content.

    Returns:
        Tuple of ``("toml", ProtoScript)`` or ``("flat", (settings, steps))``.
    """
    try:
        doc = tomllib.loads(text)
        if "test" in doc:
            return ("toml", parse_toml_script(text))
    except (tomllib.TOMLDecodeError, ValueError):
        pass
    return ("flat", parse_proto_script(text))


def diff_bytes(expected: bytes, actual: bytes, mask: bytes) -> list[str]:
    """Compare expected and actual bytes with mask, returning per-byte status.

    Only compares up to ``len(expected)`` bytes. Extra bytes in actual
    are not included in the diff list - use ``overflow_count()`` to
    detect overflow.

    Args:
        expected: Expected byte pattern.
        actual: Actual received bytes.
        mask: Wildcard mask (0xFF=must match, 0x00=any).

    Returns:
        List of status strings, one per byte position up to
        ``len(expected)``: ``"match"``, ``"wildcard"``, ``"mismatch"``,
        ``"missing"`` (actual shorter than expected).
    """
    result: list[str] = []
    for i in range(len(expected)):
        if i >= len(actual):
            result.append("missing")
        elif i < len(mask) and mask[i] == 0x00:
            result.append("wildcard")
        elif expected[i] == actual[i]:
            result.append("match")
        else:
            result.append("mismatch")
    return result


def overflow_count(expected: bytes, actual: bytes) -> int:
    """Return the number of extra bytes in actual beyond expected length.

    Args:
        expected: Expected byte pattern.
        actual: Actual received bytes.

    Returns:
        Number of overflow bytes (0 if actual <= expected length).
    """
    return max(0, len(actual) - len(expected))


# Rich markup styles for diff coloring in visualizers
DIFF_STYLES: dict[str, str] = {
    "match": "bold bright_green",
    "wildcard": "dim",
    "mismatch": "bold red",
    "extra": "bold red",
    "missing": "bold red",
    "mixed": "",  # per-byte markup embedded in value string
}


def format_diff_markup(
    actual: bytes,
    expected: bytes,
    mask: bytes,
    token_fn: Callable[[int], str],
    missing_token: str,
) -> str:
    """Build Rich-markup diff string for a visualizer.

    Shared helper used by built-in hex and text visualizers.
    Each byte is styled according to its diff status. If actual
    has more bytes than expected, appends ``OVR+N`` in yellow.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0x00 = wildcard position).
        token_fn: Callable that formats a single byte value to a display token.
        missing_token: Token string to show for missing bytes.

    Returns:
        Rich-markup string with per-byte diff colors.
    """
    statuses = diff_bytes(expected, actual, mask)
    parts: list[str] = []
    for i, status in enumerate(statuses):
        style = DIFF_STYLES.get(status, "")
        token = missing_token if status == "missing" else token_fn(actual[i])
        parts.append(f"[{style}]{token}[/]")
    result = "".join(parts)
    # Strip trailing space inside the last markup tag
    if result.endswith(" [/]"):
        result = result[:-4] + "[/]"
    # Append overflow indicator if actual has extra bytes
    ovr = overflow_count(expected, actual)
    if ovr > 0:
        result += f" [bold yellow]OVR+{ovr}[/]"
    return result


# ---------------------------------------------------------------------------
# Format spec language - column-based packet visualization
# ---------------------------------------------------------------------------


@dataclass
class ColumnSpec:
    """Parsed column specification from a format spec string.

    Attributes:
        name: Column header label.
        type_code: Type code (``"H"``, ``"U"``, ``"I"``, ``"S"``, ``"F"``,
            ``"B"``, ``"_"``, or a CRC algorithm name like ``"crc16m"``).
        byte_indices: 0-based byte indices in the specified order.
            For ascending ranges the first index is MSB (big-endian).
        bit: Bit index or range for ``B`` type, or None.
            Single bit: ``int``. Multi-byte range: ``(start, end)`` tuple.
        wildcard: True if the spec ends with ``-*`` (variable length).
        crc_algo: CRC algorithm name (without ``_le``/``_be`` suffix), or None.
        crc_little_endian: True if CRC uses ``_le`` suffix.
        crc_data_range: Explicit ``(start, end)`` 0-based inclusive range
            for CRC computation, or None for auto-range.
    """

    name: str
    type_code: str
    byte_indices: list[int] = field(default_factory=list)
    bit: int | tuple[int, int] | None = None
    wildcard: bool = False
    crc_algo: str | None = None
    crc_little_endian: bool = True
    crc_data_range: tuple[int, int] | None = None


# Regex for parsing a single byte reference like "1", "12"
_BYTE_NUM = re.compile(r"\d+")

# Regex for CRC spec: algorithm_endian(data_range) or algorithm_endian
# Algo names may contain hyphens (e.g. "crc16-modbus", "crc16-ccitt-false").
# The _le/_be suffix uses underscore, so hyphens are unambiguous.
_CRC_SPEC = re.compile(
    r"(?P<algo>[\w-]+?)(?:_(?P<endian>le|be))?"
    r"(?:\((?P<range>\d+-\d+)\))?"
    r"$"
)


def _parse_byte_refs(text: str) -> tuple[list[int], bool]:
    """Parse byte index references from a type spec body.

    Handles single bytes (``"1"``), ascending ranges (``"3-4"``),
    descending ranges (``"4-1"``), explicit sequences (``"1234"`` parsed
    as individual digits when each is a single digit, or ``"12"`` as
    byte 12), and wildcards (``"7-*"``).

    All indices are converted from 1-based (user-facing) to 0-based.

    Args:
        text: The byte reference portion of a column spec.

    Returns:
        Tuple of (0-based byte indices, is_wildcard).
    """
    wildcard = False

    # Wildcard: "7-*" or "1-*"
    if text.endswith("-*"):
        prefix = text[:-2]
        start = int(prefix) - 1
        return [start], True

    # Range: "3-4" or "4-1"
    if "-" in text:
        parts = text.split("-", 1)
        start = int(parts[0])
        end = int(parts[1])
        if start <= end:
            indices = list(range(start - 1, end))
        else:
            indices = list(range(start - 1, end - 2, -1))
        return indices, False

    # Explicit multi-byte sequence: check for concatenated numbers
    # "1234" with all single digits -> bytes 1,2,3,4
    # But "12" could be byte 12. We use a heuristic:
    # If all characters are digits and length > 1, try to parse as
    # concatenated single-digit refs first (each char is a byte index 1-9)
    if text.isdigit():
        if len(text) == 1:
            return [int(text) - 1], False
        # Could be a single number like "12" or concatenated like "1234"
        # If any digit is 0, it's not valid as a 1-based index, so treat
        # the whole thing as a single number
        if "0" not in text and len(text) > 2:
            # Concatenated single-digit refs: "1234" -> [0,1,2,3]
            return [int(ch) - 1 for ch in text], False
        # Single number: "12" -> [11]
        return [int(text) - 1], False

    return [], False


def _parse_crc_spec(type_body: str) -> ColumnSpec:
    """Parse a CRC type specification.

    Formats:
    - ``crc16m_le`` - algorithm + endianness
    - ``crc16m_le(1-6)`` - with explicit data range
    - ``crc8`` - no endianness needed for 1-byte CRC

    Args:
        type_body: Everything after ``Name:`` in the column spec.

    Returns:
        ColumnSpec configured for CRC verification.
    """
    m = _CRC_SPEC.match(type_body)
    if not m:
        return ColumnSpec(name="", type_code="crc", crc_algo=type_body)

    algo = m.group("algo")
    endian = m.group("endian")
    data_range_str = m.group("range")

    little_endian = endian != "be"  # default to LE

    data_range: tuple[int, int] | None = None
    if data_range_str:
        parts = data_range_str.split("-")
        data_range = (int(parts[0]) - 1, int(parts[1]) - 1)

    return ColumnSpec(
        name="",
        type_code="crc",
        crc_algo=algo,
        crc_little_endian=little_endian,
        crc_data_range=data_range,
    )


def extract_fmt_title(spec: str) -> tuple[str, str]:
    """Extract a ``Title:Name`` prefix from a format spec string.

    If the first token is ``Title:SomeName``, returns the title (with
    underscores replaced by spaces) and the remaining spec. Otherwise
    returns an empty title and the original spec.

    Args:
        spec: Format spec string, possibly starting with ``Title:Name``.

    Returns:
        Tuple of (title, remaining_spec).
    """
    tokens = spec.split()
    if tokens and tokens[0].startswith("Title:"):
        title = tokens[0][6:].replace("_", " ")
        return title, " ".join(tokens[1:])
    return "", spec


def parse_format_spec(spec: str) -> list[ColumnSpec]:
    """Parse a format spec string into column definitions.

    Format: space-separated ``Name:TypeBytesRefs`` tokens.
    See plan for full syntax documentation.

    Args:
        spec: Format spec string, e.g.
            ``"Slave:H1 Func:H2 Addr:U3-4 CRC:crc16m_le"``.

    Returns:
        List of ColumnSpec, one per column.
    """
    columns: list[ColumnSpec] = []
    for token in spec.split():
        colon = token.index(":")
        name = token[:colon]
        type_body = token[colon + 1:]

        # Bit field: B/b prefix with dot separator
        #   B1.3 - single bit, display as integer
        #   B1-2.7-9 - multi-byte bit range, display as integer
        #   b1-2.0-7 - same but display as binary string (0-7 = MSB first)
        #   b1-2.7-0 - binary string (7-0 = LSB first)
        if type_body[0] in "Bb" and "." in type_body:
            type_code = type_body[0]
            byte_part, bit_part = type_body[1:].split(".", 1)
            byte_indices, _ = _parse_byte_refs(byte_part)
            if "-" in bit_part:
                bit_parts = bit_part.split("-", 1)
                bit = (int(bit_parts[0]), int(bit_parts[1]))
            else:
                bit = int(bit_part)
            columns.append(ColumnSpec(
                name=name, type_code=type_code,
                byte_indices=byte_indices, bit=bit,
            ))
            continue

        # Standard types: H, h, U, I, S, F, _ (padding)
        if type_body[0] in "HhUISF_":
            type_code = type_body[0]
            refs_str = type_body[1:]
            byte_indices, wildcard = _parse_byte_refs(refs_str)
            columns.append(ColumnSpec(
                name=name, type_code=type_code,
                byte_indices=byte_indices, wildcard=wildcard,
            ))
            continue

        # CRC: starts with known CRC prefix or contains _le/_be
        if "_le" in type_body or "_be" in type_body or (
            not type_body[0].isupper() and type_body[0].isalpha()
        ):
            col = _parse_crc_spec(type_body)
            col.name = name
            columns.append(col)
            continue

        # Unknown - treat as hex
        byte_indices, wildcard = _parse_byte_refs(type_body)
        columns.append(ColumnSpec(
            name=name, type_code="H",
            byte_indices=byte_indices, wildcard=wildcard,
        ))

    return columns


def _resolve_wildcards(
    columns: list[ColumnSpec], data_len: int,
) -> list[ColumnSpec]:
    """Resolve wildcard columns and CRC byte positions for a given data length.

    Wildcards expand to cover all remaining bytes (minus any trailing CRC).
    CRC columns get their byte_indices set based on the plugin WIDTH.

    Args:
        columns: Parsed column specs (may contain wildcards and CRCs).
        data_len: Length of the actual data bytes.

    Returns:
        New list of ColumnSpec with wildcards and CRCs resolved.
    """
    registry = get_crc_registry()

    # Calculate total CRC bytes at end of packet
    crc_width = 0
    for col in columns:
        if col.crc_algo and col.crc_algo in registry:
            crc_width = registry[col.crc_algo].width
            break

    resolved: list[ColumnSpec] = []
    for col in columns:
        if col.wildcard:
            # Expand wildcard: start_index to (data_len - crc_width - 1)
            start = col.byte_indices[0] if col.byte_indices else 0
            end = data_len - crc_width
            new_col = ColumnSpec(
                name=col.name, type_code=col.type_code,
                byte_indices=list(range(start, end)),
                wildcard=False,
            )
            resolved.append(new_col)
        elif col.crc_algo:
            # CRC: bytes are at the end of the packet
            algo = registry.get(col.crc_algo)
            if algo:
                width = algo.width
                crc_start = data_len - width
                indices = list(range(crc_start, data_len))
                # Reverse for little-endian
                if col.crc_little_endian:
                    indices = list(reversed(indices))
                new_col = ColumnSpec(
                    name=col.name, type_code="crc",
                    byte_indices=indices,
                    crc_algo=col.crc_algo,
                    crc_little_endian=col.crc_little_endian,
                    crc_data_range=col.crc_data_range,
                )
                resolved.append(new_col)
            else:
                resolved.append(col)
        else:
            resolved.append(col)
    return resolved


def _format_column_value(
    data: bytes, col: ColumnSpec,
) -> str:
    """Format a single column's value from raw data bytes.

    Args:
        data: Full packet bytes.
        col: Resolved column specification.

    Returns:
        Formatted string value for this column.
    """
    indices = col.byte_indices

    # Bit field - B (integer) or b (binary string)
    if col.type_code in ("B", "b") and col.bit is not None:
        if not indices or indices[0] >= len(data):
            return "?"
        if isinstance(col.bit, tuple):
            # Multi-byte bit range: assemble value from byte indices,
            # extract bit range. Byte order follows index order.
            raw_bits = bytearray()
            for idx in indices:
                raw_bits.append(data[idx] if idx < len(data) else 0)
            combined = int.from_bytes(raw_bits, "big")
            start_bit, end_bit = col.bit
            if start_bit <= end_bit:
                low = start_bit
                width = end_bit - start_bit + 1
            else:
                low = end_bit
                width = start_bit - end_bit + 1
            value = (combined >> low) & ((1 << width) - 1)
            if col.type_code == "b":
                bits = format(value, f"0{width}b")
                # Ascending range (0-7) = MSB first (conventional),
                # descending range (7-0) = reversed
                if start_bit > end_bit:
                    bits = bits[::-1]
                return bits
            return str(value)
        byte_val = data[indices[0]]
        bit_val = (byte_val >> col.bit) & 1
        return str(bit_val)

    # Gather bytes in specified order
    raw = bytearray()
    for idx in indices:
        if idx < len(data):
            raw.append(data[idx])
        else:
            raw.append(0)

    if not raw:
        return ""

    # CRC - show hex value
    if col.type_code == "crc":
        registry = get_crc_registry()
        algo = registry.get(col.crc_algo or "")
        if algo:
            # Format the CRC value from packet bytes
            val = int.from_bytes(raw, "big")
            width = algo.width
            return f"{val:0{width * 2}X}"
        return raw.hex().upper()

    # Hex: H = combined (0A2B), h = spaced per-byte (0A 2B)
    if col.type_code == "H":
        return "".join(f"{b:02X}" for b in raw) if len(raw) > 1 else (
            f"{raw[0]:02X}" if raw else "")
    if col.type_code == "h":
        return " ".join(f"{b:02X}" for b in raw)

    # Unsigned decimal
    if col.type_code == "U":
        val = int.from_bytes(raw, "big")
        return str(val)

    # Signed decimal (always show +/- sign)
    if col.type_code == "I":
        val = int.from_bytes(raw, "big", signed=True)
        return f"{val:+d}"

    # ASCII string
    if col.type_code == "S":
        return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)

    # Float
    if col.type_code == "F":
        if len(raw) == 4:
            val = struct.unpack(">f", bytes(raw))[0]
            return f"{val:.4g}"
        elif len(raw) == 8:
            val = struct.unpack(">d", bytes(raw))[0]
            return f"{val:.6g}"
        return raw.hex().upper()

    return raw.hex().upper()


def apply_format(
    data: bytes, columns: list[ColumnSpec],
) -> tuple[list[str], list[str]]:
    """Apply column specs to data bytes, returning headers and values.

    Resolves wildcards and CRC positions based on actual data length.

    Args:
        data: Raw packet bytes.
        columns: Parsed column specs (from ``parse_format_spec``).

    Returns:
        Tuple of (headers, values) - parallel lists of strings.
    """
    resolved = _resolve_wildcards(columns, len(data))
    headers: list[str] = []
    values: list[str] = []
    for col in resolved:
        if col.type_code == "_":
            continue  # padding - accounted for but not displayed
        headers.append(col.name)
        values.append(_format_column_value(data, col))
    return headers, values


def diff_columns(
    actual: bytes, expected: bytes, mask: bytes,
    columns: list[ColumnSpec],
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare actual vs expected using column specs.

    Each column's status is determined by comparing the underlying
    bytes at the column's byte indices. If any byte in the range
    mismatches (respecting the wildcard mask), the column is marked
    as ``"mismatch"``.

    For CRC columns, the status is determined by computing the CRC
    over the data range and comparing against the packet's CRC bytes.

    Args:
        actual: Actual received bytes.
        expected: Expected bytes for comparison.
        mask: Wildcard mask (0xFF=must match, 0x00=any).
        columns: Parsed column specs (from ``parse_format_spec``).

    Returns:
        Tuple of (headers, expected_values, actual_values, statuses).
        Status per column: ``"match"``, ``"mismatch"``, ``"wildcard"``,
        ``"missing"``.
    """
    # Resolve wildcards using expected length (for consistent column count)
    resolved = _resolve_wildcards(columns, len(expected))

    headers: list[str] = []
    exp_values: list[str] = []
    act_values: list[str] = []
    statuses: list[str] = []

    registry = get_crc_registry()

    for col in resolved:
        if col.type_code == "_":
            continue  # padding - accounted for but not displayed
        headers.append(col.name)
        exp_values.append(_format_column_value(expected, col))
        act_values.append(_format_column_value(actual, col))

        # Determine per-column status
        if col.type_code == "crc" and col.crc_algo:
            # CRC column: compare actual vs expected bytes directly.
            act_crc_bytes = bytearray()
            exp_crc_bytes = bytearray()
            for idx in col.byte_indices:
                if idx < len(actual):
                    act_crc_bytes.append(actual[idx])
                if idx < len(expected):
                    exp_crc_bytes.append(expected[idx])

            if act_crc_bytes == exp_crc_bytes and exp_crc_bytes:
                statuses.append("match")
            else:
                statuses.append("mismatch")
        elif col.type_code == "h" and len(col.byte_indices) > 1:
            # Per-byte hex: build Rich markup with per-byte coloring
            parts: list[str] = []
            has_mismatch = False
            for idx in col.byte_indices:
                if idx >= len(actual):
                    parts.append(f"[{DIFF_STYLES['missing']}]--[/]")
                    has_mismatch = True
                elif idx >= len(expected):
                    parts.append(
                        f"[{DIFF_STYLES['extra']}]{actual[idx]:02X}[/]")
                    has_mismatch = True
                elif idx < len(mask) and mask[idx] == 0x00:
                    parts.append(
                        f"[{DIFF_STYLES['wildcard']}]{actual[idx]:02X}[/]")
                elif actual[idx] != expected[idx]:
                    parts.append(
                        f"[{DIFF_STYLES['mismatch']}]{actual[idx]:02X}[/]")
                    has_mismatch = True
                else:
                    parts.append(
                        f"[{DIFF_STYLES['match']}]{actual[idx]:02X}[/]")
            if has_mismatch:
                act_values[-1] = " ".join(parts)
                statuses.append("mixed")
            else:
                statuses.append("match")
        else:
            # Compare decoded values so that multi-byte columns
            # (H, U, bit fields sharing bytes) only show mismatch
            # when their formatted output actually differs.
            max_idx = max(col.byte_indices) if col.byte_indices else -1
            if max_idx >= len(actual):
                col_status = "missing"
            elif max_idx >= len(expected):
                col_status = "extra"
            elif all(
                idx >= len(mask) or mask[idx] == 0x00
                for idx in col.byte_indices
            ):
                col_status = "wildcard"
            elif exp_values[-1] != act_values[-1]:
                col_status = "mismatch"
            else:
                col_status = "match"
            statuses.append(col_status)

    return headers, exp_values, act_values, statuses


