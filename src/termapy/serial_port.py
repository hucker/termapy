"""Serial port I/O wrapper and reader data processor.

``SerialPort`` wraps a serial port with logging, frame reading, idle
detection, and queue draining. ``SerialReader`` processes raw bytes into
display lines, handling encoding, line splitting, EOL markers, ANSI
partial-escape buffering, and clear-screen detection.

No Textual dependency - fully testable.
"""

from __future__ import annotations

import codecs
import queue
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable


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
        port: Any,
        rx_queue: "queue.Queue[bytes]",
        log: Callable[[str, str], None] | None = None,
        encoding: str = "utf-8",
        last_rx: Callable[[], float] | None = None,
    ) -> None:
        self._port = port
        self._rx_queue = rx_queue
        self._log = log or (lambda _d, _t: None)
        self._encoding = encoding
        self._last_rx = last_rx

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
        """Discard all pending input, in the RX queue AND in the driver.

        Emptying the queue alone is not enough: the reader thread only moves
        bytes into it when it runs, so anything still parked in the driver's
        RX buffer survives the drain and arrives afterwards -- landing in
        whatever reply the caller goes on to read.  Every request/response
        path (``/ping``, ``/proto.*``, profiles) drains first precisely to
        get a clean slate, so the driver buffer has to go too.

        Measured on a hardware loopback with the reader stalled: draining the
        queue alone let ~5 KB of stale bytes hit the next reply at pyserial's
        4 KB default, and ~128 KB at the buffer size termapy now requests.
        Purging drops both to zero.  Bytes still in flight on the wire at
        purge time cannot be caught by any buffer policy and still arrive.

        Returns:
            Number of bytes discarded from the queue.  Driver-side bytes are
            purged without being counted -- they were never handed to us.
        """
        count = 0
        while not self._rx_queue.empty():
            try:
                count += len(self._rx_queue.get_nowait())
            except queue.Empty:
                break
        # Duck-typed ports (FakeSerial and friends) may not implement it;
        # a port that cannot purge is not a reason to fail the drain.
        reset = getattr(self._port, "reset_input_buffer", None)
        if reset is not None:
            import serial as _serial
            try:
                reset()
            except (OSError, _serial.SerialException):
                pass
        return count

    def wait_for_data(self, timeout_ms: int = 250) -> bool:
        """Wait until at least one byte arrives, or timeout expires.

        Checks the rx_queue (not the raw port) because the background
        reader thread drains in_waiting continuously - by the time we
        check, the bytes are already in the queue.

        Args:
            timeout_ms: Maximum time to wait (milliseconds).

        Returns:
            True if data arrived, False on timeout.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            if not self.is_open:
                return False
            if not self._rx_queue.empty():
                return True
            time.sleep(0.005)
        return False

    def wait_for_idle(self, timeout_ms: int = 100, max_wait_s: float = 3.0) -> None:
        """Wait until the device has been quiet for *timeout_ms*.

        Keys off the reader's "device last spoke" clock when one was supplied
        (``last_rx``), NOT off the port's ``in_waiting``.  With a background
        reader running, ``in_waiting`` is drained to zero continuously, so a
        sample taken between two lines of a streaming response sees 0 bytes
        and reports idle while the response is still arriving -- callers using
        this to sequence commands then send the next one too early.  ``cli.py``
        hit exactly this and hand-rolled an rx-observer clock to avoid it
        (docs/review/2026-08-19-v0.74.0-opus-5.md, finding T10).

        Without a ``last_rx`` provider there is no reader draining the port, so
        ``in_waiting`` *is* authoritative and is used instead.

        Args:
            timeout_ms: Silence period to consider idle (milliseconds).
            max_wait_s: Maximum total wait time (seconds).
        """
        gap = timeout_ms / 1000.0
        started = time.monotonic()
        deadline = started + max_wait_s
        if self._last_rx is not None:
            while time.monotonic() < deadline:
                if not self.is_open:
                    return
                # ``max(..., started)`` is load-bearing in BOTH directions.
                # The reader clock alone reports idle while a reply is merely
                # not-yet-started -- it still holds the stamp from the PREVIOUS
                # response -- so callers fire the next command over the top of
                # the answer they are waiting for.  Flooring at the call time
                # guarantees one full quiet gap after we were asked, while the
                # reader clock extends that for as long as bytes keep arriving.
                last = max(self._last_rx(), started)
                if (time.monotonic() - last) >= gap:
                    return
                time.sleep(0.01)
            return
        # No reader in the picture: nothing else is consuming the port, so its
        # own buffer is the honest signal.
        last_data = time.monotonic()
        while time.monotonic() < deadline:
            if not self.is_open:
                return
            try:
                waiting = self._port.in_waiting
            except (OSError, AttributeError):
                return
            if waiting > 0:
                last_data = time.monotonic()
            elif (time.monotonic() - last_data) >= gap:
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


# Receive-newline modes (mirrors TeraTerm's Receive newline: AUTO/CR/LF/CR+LF).
RX_NEWLINE_MODES = ("auto", "cr", "lf", "crlf")

# Longest run of undelimited characters ``SerialReader`` accumulates before
# flushing it as one line.
#
# Every chunk re-scans the whole pending buffer: ``apply_backspace`` walks it,
# the clear-screen regex searches it, and ``split_rx_lines`` splits it.  That
# is fine while lines terminate, and quadratic when they never do -- a device
# streaming without any terminator makes each chunk cost more than the last,
# on the reader thread, forever.  Flushing at a cap bounds both the per-chunk
# scan and the memory.  64 K characters is far past any real line, so a device
# that terminates its output never reaches it.
RX_LINE_MAX_CHARS = 65536

_EOL_MARKERS = {"cr": _EOL_CR, "lf": _EOL_LF, "crlf": _EOL_CR + _EOL_LF}


def split_rx_lines(buf: str, mode: str) -> tuple[list[tuple[str, str]], str]:
    """Split a receive buffer into complete ``(content, terminator)`` lines.

    Universal-newline handling for serial RX: which byte(s) end a line
    depends on ``mode`` (mirrors TeraTerm's Receive-newline selector):

    - ``auto`` -- ``\\r``, ``\\n``, and ``\\r\\n`` all terminate a line;
      ``\\r\\n`` counts as one break.  Works for any device.
    - ``lf`` -- only ``\\n`` breaks (``\\r`` is data).
    - ``cr`` -- only ``\\r`` breaks (``\\n`` is data).
    - ``crlf`` -- only the ``\\r\\n`` pair breaks; a lone ``\\r`` or ``\\n``
      is data.

    A lone ``\\r`` at the very end of the buffer (in ``auto``/``crlf``) is
    *not* emitted -- it might be the CR of a CRLF whose LF has not arrived
    yet.  It stays in the returned remainder to be resolved on the next
    call (or flushed on idle).  This is what prevents a CRLF split across
    two reads from producing a spurious blank line.

    Args:
        buf: The accumulated (decoded) receive buffer.
        mode: One of :data:`RX_NEWLINE_MODES`.

    Returns:
        ``(pairs, remainder)`` -- complete ``(content, terminator)`` lines
        (terminator is ``"cr"``/``"lf"``/``"crlf"``) and the leftover,
        possibly ending in a deferred ``\\r``.
    """
    pairs: list[tuple[str, str]] = []
    start = i = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == "\n" and mode in ("auto", "lf"):
            pairs.append((buf[start:i], "lf"))
            i += 1
            start = i
        elif c == "\r" and mode in ("auto", "cr", "crlf"):
            nxt = buf[i + 1] if i + 1 < n else ""
            if nxt == "\n" and mode in ("auto", "crlf"):
                pairs.append((buf[start:i], "crlf"))
                i += 2
                start = i
            elif nxt == "" and mode in ("auto", "crlf"):
                break  # trailing CR: may still become CRLF -> defer
            elif mode in ("auto", "cr"):
                pairs.append((buf[start:i], "cr"))
                i += 1
                start = i
            else:  # crlf mode, lone CR not part of a pair -> data
                i += 1
        else:
            i += 1
    return pairs, buf[start:]


def apply_backspace(text: str) -> str:
    """Resolve backspace (``\\b``) and DEL (``\\x7f``) in received text.

    A device that overwrites in place -- progress readouts, prompt line
    editing -- sends ``\\b``/``\\x7f`` to erase the previous character.
    Interpret them (like TeraTerm/CoolTerm) instead of showing the raw
    control byte: each erases the char before it, but never crosses a line
    terminator (a ``\\b`` at the start of a line is dropped rather than
    eating the preceding ``\\r``/``\\n``).  A no-op for text without either
    byte, so it is safe to re-run over the accumulating buffer.
    """
    if "\b" not in text and "\x7f" not in text:
        return text
    out: list[str] = []
    for ch in text:
        if ch in ("\b", "\x7f"):
            if out and out[-1] not in ("\r", "\n"):
                out.pop()
        else:
            out.append(ch)
    return "".join(out)


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
        serial_claimed: Callable returning True while the serial port is
            claimed by a synchronous reader.  Display is suppressed and
            bytes are queued for ``serial_read_raw()`` instead of feeding
            ``on_lines``.  ``PluginContext.serial_io()`` is the canonical
            way to enter this state.
    """

    def __init__(
        self,
        encoding: str = "utf-8",
        show_line_endings: bool = False,
        capture: Any | None = None,
        serial_claimed: Callable[[], bool] | None = None,
        rx_newline: str = "auto",
    ) -> None:
        self._encoding = encoding
        self._show_line_endings = show_line_endings
        self._rx_newline = rx_newline if rx_newline in RX_NEWLINE_MODES else "auto"
        self._capture = capture
        self._serial_claimed = serial_claimed or (lambda: False)
        self._buf: str = ""
        # Stateful decoder: holds the trailing bytes of a multi-byte
        # character that gets split across two serial reads, so the char
        # is decoded once whole instead of becoming two U+FFFD on each
        # side of the split.  (Byte-level analog of the partial-ANSI
        # buffering below.)
        self._decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
        self._last_rx: float = time.monotonic()

    @property
    def encoding(self) -> str:
        return self._encoding

    @encoding.setter
    def encoding(self, value: str) -> None:
        self._encoding = value
        # Rebuild the incremental decoder for the new codec; any bytes
        # buffered for the old encoding are dropped (they were for a
        # codec we're no longer using).
        self._decoder = codecs.getincrementaldecoder(value)(errors="replace")

    @property
    def show_line_endings(self) -> bool:
        return self._show_line_endings

    @show_line_endings.setter
    def show_line_endings(self, value: bool) -> None:
        self._show_line_endings = value

    @property
    def rx_newline(self) -> str:
        return self._rx_newline

    @rx_newline.setter
    def rx_newline(self, value: str) -> None:
        # Fall back to the robust default on an unknown mode rather than
        # raising on the read thread.
        self._rx_newline = value if value in RX_NEWLINE_MODES else "auto"

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

            # Suppress display while the port is claimed for synchronous read.
            if self._serial_claimed():
                self._last_rx = time.monotonic()
                self._buf = ""
                self._decoder.reset()  # drop any half-decoded char too
                return result

            self._last_rx = time.monotonic()
            # Incremental decode: a multi-byte char split across two reads
            # is held until its continuation bytes arrive, instead of
            # decoding each chunk independently and emitting U+FFFD twice.
            text = self._decoder.decode(data)
            self._buf += text
            # Resolve backspace/DEL against the whole buffer so an erase can
            # reach back into bytes carried over from a previous read.
            self._buf = apply_backspace(self._buf)

            # Check for clear screen escape
            m = CLEAR_SCREEN_RE.search(self._buf)
            if m:
                self._buf = self._buf[m.end():]
                result.clear_screen = True

            # Collect complete lines per the receive-newline mode.  Markers
            # are applied per detected terminator (see _render_line) rather
            # than pre-inserted into the text -- pre-insertion would wedge an
            # ANSI marker between the CR and LF of a CRLF and defeat the
            # "one break" detection.  Blank lines are preserved.
            pairs, self._buf = split_rx_lines(self._buf, self._rx_newline)
            for content, term in pairs:
                result.lines.append(self._render_line(content, term))

            # Nothing terminated it and the buffer has grown past the cap:
            # flush it as one line rather than re-scanning an ever-longer
            # buffer on every chunk (see RX_LINE_MAX_CHARS above).
            if len(self._buf) >= RX_LINE_MAX_CHARS:
                result.lines.append(self._render_line(self._buf, ""))
                self._buf = ""

        else:
            # No data - flush partial line after 200ms of silence
            if (time.monotonic() - self._last_rx) >= 0.2:
                # Drain any bytes the decoder is holding mid-character so a
                # truncated multi-byte tail surfaces (as U+FFFD) instead of
                # lingering until the next read.
                tail = self._decoder.decode(b"", final=True)
                self._decoder.reset()
                if tail:
                    self._buf += tail
                if self._buf and not PARTIAL_ANSI_RE.search(self._buf):
                    # No more bytes coming: flush the remainder as one final
                    # line.  A deferred trailing CR (held by split_rx_lines
                    # in case a CRLF was mid-arrival) is now known to be a
                    # lone CR terminator, so surface it as such.
                    content, term = self._buf, ""
                    if content.endswith("\r"):
                        content, term = content[:-1], "cr"
                    result.lines.append(self._render_line(content, term))
                    self._buf = ""

        return result

    def _render_line(self, content: str, term: str) -> str:
        """Clean and (optionally) annotate one extracted line for display.

        In the single-terminator modes the "other" terminator can ride
        along as a stray byte (e.g. a CRLF device in ``lf`` mode leaves a
        ``\\r``); strip it so lines read cleanly, matching the historical
        LF-split-then-strip behavior.  ``auto``/``crlf`` consume their
        terminators exactly, so nothing is stripped there.  When
        ``show_line_endings`` is on, append a dim marker for the terminator
        that ended this line.
        """
        if self._rx_newline == "lf":
            content = content.strip("\r")
        elif self._rx_newline == "cr":
            content = content.strip("\n")
        if self._show_line_endings and term:
            content += _EOL_MARKERS[term]
        return content

    def reset(self) -> None:
        """Clear the internal buffer."""
        self._buf = ""
        self._decoder.reset()
        self._last_rx = time.monotonic()
