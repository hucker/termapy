"""SerialEngine — orchestrates serial port, reader, and capture.

Owns the connection lifecycle and reader loop. No Textual dependency —
the caller provides callbacks for UI events and runs ``read_loop`` in
a background thread.
"""

from __future__ import annotations

import queue
import time
import traceback
from threading import Event
from typing import Callable

from termapy.capture import CaptureEngine
from termapy.serial_port import SerialPort, SerialReader


class SerialEngine:
    """Serial connection manager.

    Combines SerialPort, SerialReader, and CaptureEngine into a single
    lifecycle. The caller (app.py or a CLI tool) provides an ``open_fn``
    to create the port and callbacks for UI events.

    Args:
        cfg: Config dict (read for encoding, line endings, etc.).
        capture: CaptureEngine instance for data capture.
        open_fn: Callable that takes cfg and returns a serial port object.
        log: Log callback — log(direction, text).
    """

    def __init__(
        self,
        cfg: dict,
        capture: CaptureEngine,
        open_fn: Callable[[dict], object],
        log: Callable[[str, str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._capture = capture
        self._open_fn = open_fn
        self._log = log or (lambda _d, _t: None)

        self._port_obj: object | None = None
        self._serial_port: SerialPort | None = None
        self._reader: SerialReader | None = None
        self._rx_queue: queue.Queue[bytes] = queue.Queue()
        self._stop_event = Event()
        self._reader_stopped = Event()
        self._reader_stopped.set()
        self._proto_active: bool = False

    @property
    def is_connected(self) -> bool:
        """True if the serial port is open."""
        return self._port_obj is not None and getattr(self._port_obj, "is_open", False)

    @property
    def serial_port(self) -> SerialPort | None:
        """The SerialPort wrapper, or None if not connected."""
        return self._serial_port

    @property
    def reader(self) -> SerialReader | None:
        """The SerialReader, or None if not connected."""
        return self._reader

    @property
    def port_obj(self) -> object | None:
        """The underlying serial port object (Serial or FakeSerial)."""
        return self._port_obj

    @property
    def rx_queue(self) -> queue.Queue[bytes]:
        """The raw RX byte queue (fed by read_loop, consumed by SerialPort.read_raw)."""
        return self._rx_queue

    @property
    def stop_event(self) -> Event:
        """Event to signal the reader loop to stop."""
        return self._stop_event

    @property
    def reader_stopped(self) -> Event:
        """Event set when the reader loop has exited."""
        return self._reader_stopped

    @property
    def proto_active(self) -> bool:
        return self._proto_active

    @proto_active.setter
    def proto_active(self, value: bool) -> None:
        self._proto_active = value
        if self._reader:
            self._reader._proto_active = lambda: value

    def connect(self) -> bool:
        """Open the serial port and create SerialPort + SerialReader.

        Returns:
            True if the port opened successfully.
        """
        if self.is_connected:
            return True
        try:
            self._port_obj = self._open_fn(self._cfg)
        except Exception:
            return False

        self._serial_port = SerialPort(
            port=self._port_obj,
            rx_queue=self._rx_queue,
            log=self._log,
            encoding=self._cfg.get("encoding", "utf-8"),
        )
        self._reader = SerialReader(
            encoding=self._cfg.get("encoding", "utf-8"),
            show_line_endings=self._cfg.get("show_line_endings", False),
            capture=self._capture,
            proto_active=lambda: self._proto_active,
        )
        self._stop_event.clear()
        self._reader_stopped.clear()
        return True

    def disconnect(self) -> None:
        """Signal the reader to stop, wait, and close the port."""
        self._stop_event.set()
        self._reader_stopped.wait(timeout=0.3)
        if self._port_obj:
            try:
                self._port_obj.close()
            except Exception:
                pass
        self._port_obj = None
        self._serial_port = None

    def read_loop(
        self,
        *,
        on_lines: Callable[[list[str]], None] | None = None,
        on_clear: Callable[[], None] | None = None,
        on_capture_done: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        """Blocking reader loop — call from a background thread.

        Reads from the serial port, processes bytes through SerialReader,
        and calls callbacks for each event. Returns when stop_event is set
        or the port closes/errors.

        Args:
            on_lines: Called with a batch of display lines.
            on_clear: Called when a clear-screen escape is detected.
            on_capture_done: Called when binary capture hits its target.
            on_error: Called with error detail string on serial read error.
            on_disconnect: Called when the port disconnects unexpectedly.
        """
        reader = self._reader
        port = self._port_obj
        if not reader or not port:
            self._reader_stopped.set()
            return

        try:
            while not self._stop_event.is_set():
                if not getattr(port, "is_open", False):
                    break
                try:
                    waiting = getattr(port, "in_waiting", 0) or 1
                    data = port.read(min(waiting, 4096))
                except (OSError, Exception) as exc:
                    detail = f"{exc.__class__.__name__}: {exc}"
                    if on_error:
                        on_error(detail)
                    if on_disconnect:
                        on_disconnect()
                    break

                if data:
                    self._rx_queue.put(data)

                result = reader.process(data)

                if result.capture_target_reached and on_capture_done:
                    on_capture_done()
                if result.clear_screen and on_clear:
                    on_clear()
                if result.lines and on_lines:
                    on_lines(result.lines)

                if not data:
                    time.sleep(0.01)
        finally:
            if self._port_obj:
                try:
                    self._port_obj.close()
                except Exception:
                    pass
                self._port_obj = None
                self._serial_port = None
            self._reader_stopped.set()

    def try_reconnect(self) -> bool:
        """Attempt a single reconnect. Returns True on success."""
        try:
            port = self._open_fn(self._cfg)
            port.close()
            return True
        except Exception:
            return False
