"""Capture engine — file capture state machine (no UI dependency).

Handles text and binary capture sessions: buffering, format-spec decoding,
CSV writing, and progress tracking. The caller feeds data in via `feed_bytes()`
or `feed_text()`, and the engine writes to the output file.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Callable

from termapy.protocol import apply_format, parse_hex


@dataclass
class CaptureProgress:
    """Snapshot of capture progress for UI display."""

    path_name: str
    mode: str          # "text" or "bin"
    pct: int           # 0-100
    bytes_captured: int
    target_bytes: int  # 0 for text mode
    remaining_s: float  # seconds remaining (text mode), 0 for bin


@dataclass
class CaptureResult:
    """Final result when a capture completes."""

    path: Path
    byte_count: int
    raw: bool

    @property
    def size_label(self) -> str:
        if self.byte_count > 1024:
            return f"{self.byte_count / 1024:.1f} KB"
        return f"{self.byte_count} bytes"


class CaptureEngine:
    """Stateful capture session — text or binary.

    Lifecycle:
        1. Call ``start()`` to begin a capture.
        2. Feed data via ``feed_bytes()`` (binary) or ``feed_text()`` (text).
        3. Engine writes to file, calls ``on_progress`` and ``on_echo``.
        4. When target reached or ``stop()`` called, calls ``on_complete``.

    Callbacks:
        on_progress(CaptureProgress) — periodic progress update.
        on_echo(line: str) — echo formatted line to terminal (if enabled).
        on_complete(CaptureResult) — capture finished.
        on_flush() — binary buffer flushed (caller may need to dispatch stop).
    """

    def __init__(
        self,
        on_progress: Callable[[CaptureProgress], None] | None = None,
        on_echo: Callable[[str], None] | None = None,
        on_complete: Callable[[CaptureResult], None] | None = None,
    ) -> None:
        self._on_progress = on_progress
        self._on_echo = on_echo
        self._on_complete = on_complete

        # State
        self._fh: IO[Any] | None = None
        self._path: Path | None = None
        self._mode: str = ""
        self._raw: bool = False
        self._bytes: int = 0
        self._target: int = 0
        self._end: float = 0.0
        self._total: float = 0.0
        self._columns: list = []
        self._record_size: int = 0
        self._sep: str = ","
        self._echo: bool = False
        self._header_written: bool = False
        self._buf: bytearray = bytearray()
        self._hex_mode: bool = False
        self._hex_line_buf: str = ""

    @property
    def active(self) -> bool:
        """True if a capture session is in progress."""
        return self._fh is not None

    @property
    def mode(self) -> str:
        """Current capture mode ('text', 'bin', or '' if inactive)."""
        return self._mode

    @property
    def path(self) -> Path | None:
        """Output file path, or None if inactive."""
        return self._path

    @property
    def bytes_captured(self) -> int:
        return self._bytes

    @property
    def target_bytes(self) -> int:
        return self._target

    @property
    def suppress_display(self) -> bool:
        """True if the UI should suppress serial output (binary capture)."""
        return self.active and self._mode == "bin"

    def start(
        self,
        *,
        path: Path,
        file_mode: str,
        mode: str,
        duration: float = 0.0,
        target_bytes: int = 0,
        columns: list | None = None,
        record_size: int = 0,
        sep: str = ",",
        echo: bool = False,
        hex_mode: bool = False,
        timeout: float = 0.0,
    ) -> bool:
        """Begin a capture session.

        Args:
            path: Output file path (resolved).
            file_mode: File open mode ('a', 'w', 'ab', 'wb').
            mode: 'text' or 'bin'.
            duration: Capture duration in seconds (text mode).
            target_bytes: Target byte count (bin mode).
            columns: Parsed format spec columns (bin mode, None = raw).
            record_size: Bytes per record (bin mode with format spec).
            sep: Column separator for formatted output.
            echo: Print formatted values to terminal (bin mode).
            hex_mode: Parse hex text lines instead of raw bytes.
            timeout: Safety timeout in seconds (bin mode).

        Returns:
            True if capture started, False on error.
        """
        if self._fh:
            return False

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fh = open(path, file_mode, encoding=None if "b" in file_mode else "utf-8")
        except (OSError, ValueError):
            return False

        self._fh = fh
        self._path = path
        self._mode = mode
        self._raw = not columns
        self._bytes = 0
        self._columns = columns or []
        self._record_size = record_size
        self._sep = sep
        self._echo = echo
        self._header_written = False
        self._buf = bytearray()
        self._hex_mode = hex_mode
        self._hex_line_buf = ""

        if mode == "text":
            self._end = time.monotonic() + duration
            self._total = duration
            self._target = 0
        else:
            self._target = target_bytes
            self._total = float(target_bytes)
            self._end = 0.0

        return True

    def stop(self) -> CaptureResult | None:
        """End the capture: flush, close file, return result.

        Returns:
            CaptureResult if a capture was active, None otherwise.
        """
        if not self._fh:
            return None

        if self._mode == "bin" and self._buf:
            self._flush_bin()

        path = self._path
        byte_count = self._bytes
        raw = self._raw

        try:
            self._fh.close()
        except OSError:
            pass

        self._reset()

        result = CaptureResult(path=path, byte_count=byte_count, raw=raw)
        if self._on_complete:
            self._on_complete(result)
        return result

    def feed_bytes(self, data: bytes) -> bool:
        """Feed raw bytes from the serial reader (binary capture).

        Returns:
            True if the capture target was reached and stop is needed.
        """
        if not self._fh or self._mode != "bin":
            return False

        if self._hex_mode:
            text = data.decode("utf-8", errors="replace")
            self._hex_line_buf += text
            while "\n" in self._hex_line_buf:
                line, self._hex_line_buf = self._hex_line_buf.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        self._buf.extend(parse_hex(line))
                    except ValueError:
                        pass
        else:
            self._buf.extend(data)

        if self._target and len(self._buf) >= self._target:
            self._buf = self._buf[: self._target]
            self._flush_bin()
            return True  # caller should call stop()

        if len(self._buf) >= 4096:
            self._flush_bin()

        return False

    def feed_text(self, lines: list[str]) -> None:
        """Feed decoded text lines (text capture)."""
        if not self._fh or self._mode != "text":
            return
        try:
            for text in lines:
                self._fh.write(text + "\n")
                self._bytes += len(text) + 1
            self._fh.flush()
        except OSError:
            pass

    def get_progress(self) -> CaptureProgress | None:
        """Build a progress snapshot for UI display."""
        if not self._fh:
            return None
        path_name = self._path.name if self._path else "?"
        if self._mode == "text":
            remaining = max(0.0, self._end - time.monotonic())
            elapsed = self._total - remaining
            pct = min(100, int(elapsed / self._total * 100)) if self._total > 0 else 100
        else:
            pct = min(100, int(self._bytes / self._total * 100)) if self._total > 0 else 0
            remaining = 0.0
        return CaptureProgress(
            path_name=path_name,
            mode=self._mode,
            pct=pct,
            bytes_captured=self._bytes,
            target_bytes=self._target,
            remaining_s=remaining,
        )

    def _flush_bin(self) -> None:
        """Write accumulated binary buffer to file (complete records only)."""
        if not self._fh or not self._buf:
            return

        data = bytes(self._buf)
        if self._raw:
            try:
                self._fh.write(data)
                self._bytes += len(data)
            except OSError:
                pass
        elif self._record_size > 0:
            usable = len(data) - (len(data) % self._record_size)
            if usable > 0:
                lines: list[str] = []
                sep = self._sep
                if not self._header_written:
                    headers, _ = apply_format(
                        data[: self._record_size], self._columns
                    )
                    has_names = any(
                        h != col.type_code
                        for h, col in zip(headers, self._columns)
                        if col.type_code != "_"
                    )
                    if has_names:
                        lines.append(sep.join(headers))
                    self._header_written = True

                for offset in range(0, usable, self._record_size):
                    record = data[offset : offset + self._record_size]
                    _, values = apply_format(record, self._columns)
                    lines.append(sep.join(values))

                text = "\n".join(lines) + "\n"
                try:
                    self._fh.write(text)
                    self._bytes += usable
                except OSError:
                    pass
                if self._echo and self._on_echo:
                    for line in lines:
                        self._on_echo(line)
        self._buf.clear()

    def _reset(self) -> None:
        """Clear all state back to idle."""
        self._fh = None
        self._path = None
        self._mode = ""
        self._raw = False
        self._bytes = 0
        self._target = 0
        self._end = 0.0
        self._total = 0.0
        self._columns = []
        self._record_size = 0
        self._sep = ","
        self._echo = False
        self._header_written = False
        self._buf = bytearray()
        self._hex_mode = False
        self._hex_line_buf = ""
