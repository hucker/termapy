"""SerialPort — thin wrapper around a serial port for I/O operations.

Wraps a ``serial.Serial`` or ``FakeSerial`` instance with logging,
timeout-based frame reading, idle detection, and queue draining.
No Textual dependency — fully testable.
"""

from __future__ import annotations

import queue
import time
from typing import Callable


class SerialPort:
    """Serial port I/O wrapper.

    Owns a port object (real or fake) and a raw RX queue fed by the
    reader thread. Provides write, read, drain, and idle-wait operations
    with logging.

    Args:
        port: A ``serial.Serial`` or duck-typed equivalent (e.g. ``FakeSerial``).
        rx_queue: Queue fed by the background serial reader thread.
        log: Logging callback — log(direction, text). Direction is ">" for TX.
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
