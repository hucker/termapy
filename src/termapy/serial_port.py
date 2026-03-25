"""Serial port I/O wrapper and reader data processor.

``SerialPort`` wraps a serial port with logging, frame reading, idle
detection, and queue draining. ``SerialReader`` processes raw bytes into
display lines, handling encoding, line splitting, EOL markers, ANSI
partial-escape buffering, and clear-screen detection.

No Textual dependency - fully testable.
"""

from __future__ import annotations

import queue
import re
import time
from dataclasses import dataclass, field
from typing import Callable


class SerialPort:
    """Serial port I/O wrapper.

    Owns a port object (real or fake) and a raw RX queue fed by the
    reader thread. Provides write, read, drain, and idle-wait operations
    with logging.

    Args:
        port: A ``serial.Serial`` or duck-typed equivalent (e.g. ``FakeSerial``).
        rx_queue: Queue fed by the background serial reader thread.
        log: Logging callback - log(direction, text). Direction is ">" for TX.
        encoding: Character encoding for decoding TX bytes for logging.
    """

    def __init__(
        self,
        port: object,
        rx_queue: "queue.Queue[bytes]",
        log: Callable[[str, str], None] | None = None,
        encoding: str = "utf-8",
    ) -> None:
        self._port = port
        self._rx_queue = rx_queue
        self._log = log or (lambda _d, _t: None)
        self._encoding = encoding

    @property
    def port(self) -> object:
        """The underlying serial port object."""
        return self._port

    @property
    def is_open(self) -> bool:
        """True if the port is open."""
        return getattr(self._port, "is_open", False)

    def write(self, data: bytes) -> None:
        """Write bytes to the serial port and log TX.

        Args:
            data: Bytes to send.
        """
        try:
            text = data.decode(self._encoding).rstrip("\r\n")
        except (UnicodeDecodeError, LookupError):
            text = data.hex(" ")
        self._log(">", text)
        if self._port:
            self._port.write(data)

    def read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 50) -> bytes:
        """Collect raw bytes using timeout-based framing.

        Drains the raw RX queue, accumulating bytes until a silence gap
        indicates a complete frame, or the overall timeout expires.

        Args:
            timeout_ms: Maximum time to wait for a response in milliseconds.
            frame_gap_ms: Silence gap to detect frame end (milliseconds).

        Returns:
            Complete frame bytes, or empty bytes on timeout.
        """
        from termapy.protocol import FrameCollector

        collector = FrameCollector(timeout_ms=frame_gap_ms)
        deadline = time.monotonic() + timeout_ms / 1000.0

        while time.monotonic() < deadline:
            try:
                chunk = self._rx_queue.get(timeout=0.01)
                now = time.monotonic()
                frame = collector.feed(chunk, now)
                if frame is not None:
                    return frame
            except queue.Empty:
                now = time.monotonic()
                frame = collector.flush(now)
                if frame is not None:
                    return frame

        return collector.flush(time.monotonic()) or b""

    def drain(self) -> int:
        """Discard all pending bytes in the raw RX queue.

        Returns:
            Number of bytes discarded.
        """
        count = 0
        while not self._rx_queue.empty():
            try:
                count += len(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        return count

    def wait_for_idle(self, timeout_ms: int = 100, max_wait_s: float = 3.0) -> None:
        """Wait until no serial data arrives for timeout_ms, or max_wait_s elapses.

        Args:
            timeout_ms: Silence period to consider idle (milliseconds).
            max_wait_s: Maximum total wait time (seconds).
        """
        deadline = time.monotonic() + max_wait_s
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            if not self.is_open:
                return
            try:
                waiting = self._port.in_waiting
            except (OSError, Exception):
                return
            if waiting > 0:
                last_data = time.monotonic()
            elif (time.monotonic() - last_data) >= timeout_ms / 1000.0:
                return
            time.sleep(0.01)


# Regexes for serial data processing (shared with app.py)
CLEAR_SCREEN_RE = re.compile(r"(\x1b\[H)?\x1b\[2J")
PARTIAL_ANSI_RE = re.compile(r"\x1b(\[[0-9;]*)?$")

# Visible EOL markers (dim ANSI text)
_EOL_CR = "\x1b[2m\\r\x1b[0m"
_EOL_LF = "\x1b[2m\\n\x1b[0m"


def eol_label(line_ending: str) -> str:
    """Format a line ending string with visible markers."""
    return line_ending.replace("\r", _EOL_CR).replace("\n", _EOL_LF)


@dataclass
class ReaderResult:
    """Result of processing a chunk of serial data.

    Attributes:
        lines: Complete text lines ready for display.
        clear_screen: True if a clear-screen escape was detected.
        capture_target_reached: True if binary capture hit its target.
    """

    lines: list[str] = field(default_factory=list)
    clear_screen: bool = False
    capture_target_reached: bool = False


class SerialReader:
    """Processes raw serial bytes into display lines.

    Handles encoding, line splitting, EOL markers, partial ANSI escape
    buffering, and clear-screen detection. Feeds binary data to the
    CaptureEngine when active.

    This class holds the text buffer state between ``process()`` calls
    but has no threading or I/O - the caller drives it.

    Args:
        encoding: Character encoding for decoding bytes.
        show_line_endings: Insert visible EOL markers.
        capture: Optional CaptureEngine for binary capture tap.
        proto_active: Callable returning True when display should be suppressed.
    """

    def __init__(
        self,
        encoding: str = "utf-8",
        show_line_endings: bool = False,
        capture: object | None = None,
        proto_active: Callable[[], bool] | None = None,
    ) -> None:
        self._encoding = encoding
        self._show_line_endings = show_line_endings
        self._capture = capture
        self._proto_active = proto_active or (lambda: False)
        self._buf: str = ""
        self._last_rx: float = time.monotonic()

    @property
    def encoding(self) -> str:
        return self._encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        self._encoding = value

    @property
    def show_line_endings(self) -> bool:
        return self._show_line_endings

    @show_line_endings.setter
    def show_line_endings(self, value: bool) -> None:
        self._show_line_endings = value

    def process(self, data: bytes) -> ReaderResult:
        """Process a chunk of raw serial bytes.

        Call this each time bytes arrive from the serial port. Returns
        a ``ReaderResult`` with any complete lines and status flags.

        Args:
            data: Raw bytes from the serial port (may be empty for idle check).

        Returns:
            ReaderResult with lines, clear_screen flag, and capture status.
        """
        result = ReaderResult()

        if data:
            # Feed binary capture if active - consume data, skip display
            cap = self._capture
            if cap and getattr(cap, "active", False) and getattr(cap, "mode", "") == "bin":
                target_reached = cap.feed_bytes(data)
                if target_reached:
                    result.capture_target_reached = True
                return result

            # Suppress display during protocol operations
            if self._proto_active():
                self._last_rx = time.monotonic()
                self._buf = ""
                return result

            self._last_rx = time.monotonic()
            text = data.decode(self._encoding, errors="replace")

            # Insert visible EOL markers before line splitting
            if self._show_line_endings:
                text = text.replace("\r", _EOL_CR + "\r")
                text = text.replace("\n", _EOL_LF + "\n")

            self._buf += text

            # Check for clear screen escape
            m = CLEAR_SCREEN_RE.search(self._buf)
            if m:
                self._buf = self._buf[m.end():]
                result.clear_screen = True

            # Collect complete lines
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                line = line.strip("\r")
                if line:
                    result.lines.append(line)

        else:
            # No data - flush partial line after 200ms of silence
            if self._buf and (time.monotonic() - self._last_rx) >= 0.2:
                if not PARTIAL_ANSI_RE.search(self._buf):
                    result.lines.append(self._buf)
                    self._buf = ""

        return result

    def reset(self) -> None:
        """Clear the internal buffer."""
        self._buf = ""
        self._last_rx = time.monotonic()
