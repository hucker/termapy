"""SerialEngine - orchestrates serial port, reader, and capture.

Owns the connection lifecycle and reader loop. No Textual dependency -
the caller provides callbacks for UI events and runs ``read_loop`` in
a background thread.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import time
from threading import Event
from typing import Any, Callable

from termapy.capture import CaptureEngine
from termapy.plugins import BoundaryException
from termapy.serial_port import SerialPort, SerialReader

# How long ``disconnect`` (and the app's reconnect gate) waits for the
# reader loop to signal ``reader_stopped`` before closing the port.
#
# Teardown-ownership contract: ``disconnect`` and the reader's ``finally``
# both close/null the *shared* ``_port_obj``, so exactly one must win.  The
# reader wins because it always signals ``reader_stopped`` within roughly
# one ``config.SERIAL_READ_TIMEOUT_S`` (a blocking ``read()`` cannot stall
# longer) plus a little line processing -- comfortably inside this window.
# The invariant that keeps this race unreachable is therefore
# ``SERIAL_READ_TIMEOUT_S << READER_STOP_WAIT_S``; ``test_serial_engine``
# pins the margin.  If that ever inverts, ``disconnect`` could close the
# handle while the reader is still mid-read and, after a reconnect, a stale
# reader's ``finally`` could close a *newer* port -- switch to a real
# reader-thread join before raising the read timeout.
READER_STOP_WAIT_S = 0.3


def _find_port_holder(port_name: str) -> str | None:
    """Return the process holding ``port_name``, or None if unavailable.

    Uses ``lsof`` on Linux and macOS to identify which process owns
    the serial device file.  Windows has no stock equivalent -- it
    would require Sysinternals ``handle.exe`` or a WMI probe -- so
    this returns None on Windows and the caller prints the generic
    "port may be in use" message without holder detail.

    Everything failure-related (``lsof`` missing, timeout, port path
    not found, parse error) collapses to None: a best-effort hint,
    never a second error stacked on the first.

    Returns a string like ``"arduino (PID 1234)"`` when a holder
    is found.
    """
    if sys.platform == "win32":
        return None
    # lsof wants an absolute device path.  Users typically type the
    # bare name (ttyUSB0, cu.usbserial-XXXX); normalize to /dev/...
    path = port_name if port_name.startswith("/") else f"/dev/{port_name}"
    try:
        result = subprocess.run(
            ["lsof", "-F", "pc", path],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    # lsof -F output: one tag-prefixed field per line.  We asked for
    # "pc" so we get p<pid> and c<command> lines; take the first pair.
    pid = None
    command = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p" and pid is None:
            pid = value
        elif tag == "c" and command is None:
            command = value
        if pid and command:
            break
    if pid and command:
        return f"{command} (PID {pid})"
    return None


def _classify_serial_error(exc: Exception, port_name: str = "") -> str:
    """Turn a serial open exception into a user-friendly message.

    If a ``port_name`` is provided and the error looks like "port in
    use" (PermissionError / EACCES), also try to identify the holder
    process on Linux/macOS.  The holder lookup is best-effort and
    silent on failure.

    For "port not found" errors with a multi-candidate spec (pipe
    fallback chain), also walks ``resolve_port_trace()`` to show
    which candidates failed and what IS currently connected, since
    the whole point of the fallback chain is that exactly one of the
    candidates was supposed to work.

    Pyserial on Windows wraps the OS error's *repr* into its own
    SerialException message instead of chaining the exception via
    ``raise ... from ...``, so ``exc.__cause__`` is None and we have
    to fall back to substring matching on the message itself to
    recognise PermissionError / FileNotFoundError cases.
    """
    msg = str(exc)
    cause = exc.__cause__ or exc.__context__

    # Also check the top-level exception type.  A bare FileNotFoundError
    # or PermissionError (no chained cause) would otherwise only get
    # matched by substring, which misses some platform-dependent
    # phrasings.
    if cause is None:
        if isinstance(exc, PermissionError):
            cause = exc
        elif isinstance(exc, FileNotFoundError):
            cause = exc

    def _in_use() -> str:
        base = "Permission denied -- port may be in use by another application"
        holder = _find_port_holder(port_name) if port_name else None
        return f"{base}: held by {holder}" if holder else base

    def _not_found() -> str:
        base = "Port not found -- check the port name with /port.list"
        if not port_name or "|" not in port_name:
            return base
        # Multi-candidate spec: walk the trace and list what's plugged
        # in so the user can see which candidate SHOULD have matched.
        from termapy.port_control import (
            _gather_all_chip_facts,
            resolve_port_trace,
        )
        trace = resolve_port_trace(port_name)
        lines = [f"Cannot open {port_name!r}. Tried each candidate:"]
        for candidate, reason in trace:
            if reason is None:
                lines.append(f"  {candidate}: not found")
            elif reason == "ambiguous":
                lines.append(f"  {candidate}: ambiguous (multiple SN matches)")
            else:
                lines.append(f"  {candidate}: matched via {reason}")
        # fast=True: this builds a "what's plugged in" hint after a failed
        # connect and reads only identity fields -- never probe (opening
        # every port here would disturb bystander boards mid-error).
        facts = _gather_all_chip_facts(fast=True)
        if facts:
            conn = ", ".join(
                f"{f.device} ({f.manufacturer or '?'}, SN {f.serial or 'n/a'})"
                for f in facts
            )
            lines.append(f"Currently connected: {conn}")
        else:
            lines.append("No serial ports currently connected.")
        return " | ".join(lines)

    # Preferred path: chained exception exposes the underlying cause.
    if isinstance(cause, PermissionError):
        return _in_use()
    if isinstance(cause, FileNotFoundError):
        return _not_found()
    if isinstance(cause, OSError):
        code = getattr(cause, "errno", None)
        if code == 2:
            return _not_found()
        if code == 13:
            return _in_use()
        return f"{cause}"

    # Pyserial / Windows path: cause is None, diagnosis is in the string.
    # Matches both the exception class name (pyserial's repr-based wrap)
    # and common OS phrasings.
    lowered = msg.lower()
    if (
        "permissionerror" in lowered
        or "access is denied" in lowered
        or "resource busy" in lowered
        or "device or resource busy" in lowered
    ):
        return _in_use()
    if (
        "filenotfounderror" in lowered
        or "no such file" in lowered
        or "cannot find the file" in lowered
    ):
        return _not_found()

    # Fall back to the original message, stripped of Python class noise.
    if "could not open port" in lowered:
        return msg.split(":", 1)[-1].strip() if ":" in msg else msg
    return msg


class SerialEngine:
    """Serial connection manager.

    Combines SerialPort, SerialReader, and CaptureEngine into a single
    lifecycle. The caller (app.py or a CLI tool) provides an ``open_fn``
    to create the port and callbacks for UI events.

    Args:
        cfg: Config dict (read for encoding, line endings, etc.).
        capture: CaptureEngine instance for data capture.
        open_fn: Callable that takes cfg and returns a serial port object.
        log: Log callback - log(direction, text).
    """

    def __init__(
        self,
        cfg: dict,
        capture: CaptureEngine,
        open_fn: Callable[[dict], Any],
        log: Callable[[str, str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._capture = capture
        self._open_fn = open_fn
        self._log = log or (lambda _d, _t: None)

        self._port_obj: Any | None = None
        self._serial_port: SerialPort | None = None
        self._reader: SerialReader | None = None
        self._rx_queue: queue.Queue[bytes] = queue.Queue()
        self._stop_event = Event()
        self._reader_stopped = Event()
        self._reader_stopped.set()
        self._serial_claimed: bool = False
        self._rx_observers: list[Callable[[bytes], None]] = []
        self._tx_observers: list[Callable[[bytes], None]] = []
        self.last_error: str = ""

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
    def serial_claimed(self) -> bool:
        """True while a synchronous reader has claimed the port.

        When True, incoming bytes accumulate in ``rx_queue`` for
        ``serial_read_raw()`` to drain instead of flowing through
        ``on_lines`` (terminal display + session log).  The setter
        is internal -- ``PluginContext.serial_io()`` is the public
        path for entering and exiting this state.
        """
        return self._serial_claimed

    @serial_claimed.setter
    def serial_claimed(self, value: bool) -> None:
        # The reader is built with ``serial_claimed=lambda: self._serial_claimed``
        # (a live view of this field), so setting the field is enough -- there is
        # no separate reader-side copy to keep in sync.
        self._serial_claimed = value

    def add_rx_observer(self, cb: Callable[[bytes], None]) -> None:
        """Register a callback that receives every raw RX byte chunk.

        Observers see data alongside the normal pipeline - they cannot
        modify or block it.  Callbacks fire on the reader background
        thread; keep them fast or offload to a queue.

        Args:
            cb: Called with raw bytes on each serial read.
        """
        if cb not in self._rx_observers:
            self._rx_observers.append(cb)

    def remove_rx_observer(self, cb: Callable[[bytes], None]) -> None:
        """Unregister an RX observer callback.

        Args:
            cb: The callback previously registered.
        """
        try:
            self._rx_observers.remove(cb)
        except ValueError:
            pass

    def add_tx_observer(self, cb: Callable[[bytes], None]) -> None:
        """Register a callback that receives every TX byte chunk.

        Observers see data alongside the normal write path - they cannot
        modify or block it.  Callbacks fire on the calling thread.

        Args:
            cb: Called with raw bytes on each serial write.
        """
        if cb not in self._tx_observers:
            self._tx_observers.append(cb)

    def remove_tx_observer(self, cb: Callable[[bytes], None]) -> None:
        """Unregister a TX observer callback.

        Args:
            cb: The callback previously registered.
        """
        try:
            self._tx_observers.remove(cb)
        except ValueError:
            pass

    def notify_tx(self, data: bytes) -> None:
        """Fire TX observers. Called by the app/cli write path.

        Observers are registered by plugins / callers and can raise
        anything -- BoundaryException signals the reviewed broad
        catch so one bad observer can't break the others or fail
        the write.
        """
        for obs in self._tx_observers:
            try:
                obs(data)
            except BoundaryException:
                pass

    def connect(self) -> bool:
        """Open the serial port and create SerialPort + SerialReader.

        Returns:
            True if the port opened successfully.
        """
        if self.is_connected:
            return True
        import serial as _serial
        from termapy.port_control import AmbiguousSerialNumberError
        # pyserial open() can raise SerialException (typical: port in
        # use, port missing), OSError (permissions, device vanished),
        # or ValueError (bad baud / unsupported parameter combo).
        # AmbiguousSerialNumberError comes from resolve_port() when a
        # user's SN spec matches 2+ connected devices.
        # _classify_serial_error normalises each into a friendly
        # message for the user.
        try:
            self._port_obj = self._open_fn(self._cfg)
            self.last_error = ""
        except AmbiguousSerialNumberError as e:
            matches = ", ".join(e.matches)
            self.last_error = (
                f"serial number {e.sn!r} matches {len(e.matches)} devices "
                f"({matches}) -- disambiguate with a COM name or use a "
                f"fallback chain like {e.sn!r}|<COM-name>"
            )
            return False
        except (_serial.SerialException, OSError, ValueError) as e:
            self.last_error = _classify_serial_error(e, self._cfg["serial"]["port"])
            return False

        self._serial_port = SerialPort(
            port=self._port_obj,
            rx_queue=self._rx_queue,
            log=self._log,
            encoding=self._cfg.get("encoding", "utf-8"),
        )
        self._reader = SerialReader(
            encoding=self._cfg.get("encoding", "utf-8"),
            show_line_endings=self._cfg.get("eol_markers", False),
            capture=self._capture,
            serial_claimed=lambda: self._serial_claimed,
            rx_newline=self._cfg.get("eol_rx", "auto"),
        )
        self._stop_event.clear()
        self._reader_stopped.clear()
        return True

    def disconnect(self) -> None:
        """Signal the reader to stop, wait, and close the port.

        Normally the reader observes ``_stop_event`` and closes ``_port_obj``
        itself within one read-timeout, so the wait below returns with the
        handle already gone and the ``if self._port_obj`` branch is skipped.
        Closing here is the fallback for the reader-never-started case.  See
        ``READER_STOP_WAIT_S`` for the timing contract that keeps these two
        closers from racing.
        """
        import serial as _serial
        self._stop_event.set()
        self._reader_stopped.wait(timeout=READER_STOP_WAIT_S)
        if self._port_obj:
            # Close can raise if the device already vanished; nothing
            # to recover from during disconnect, just drop the handle.
            try:
                self._port_obj.close()
            except (OSError, _serial.SerialException):
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
        """Blocking reader loop - call from a background thread.

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

        import serial as _serial
        try:
            while not self._stop_event.is_set():
                if not getattr(port, "is_open", False):
                    break
                # pyserial's read path raises SerialException on a lost
                # device and OSError on underlying fd failure; either
                # signals an effective disconnect.
                try:
                    waiting = getattr(port, "in_waiting", 0) or 1
                    data = port.read(min(waiting, 4096))
                except (OSError, _serial.SerialException) as exc:
                    detail = f"{exc.__class__.__name__}: {exc}"
                    if on_error:
                        on_error(detail)
                    if on_disconnect:
                        on_disconnect()
                    break

                if data:
                    self._rx_queue.put(data)
                    for obs in self._rx_observers:
                        # RX observers are third-party callbacks;
                        # BoundaryException signals the reviewed broad
                        # catch so one misbehaving observer can't
                        # break the others or stop the reader loop.
                        try:
                            obs(data)
                        except BoundaryException:
                            pass

                # Resync the receive-newline mode from the (shared) cfg each
                # chunk so /term.eol.rx takes effect live, without a
                # reconnect.  Cheap dict read; the cfg dict is the same
                # object /cfg mutates (see how TX line_ending stays live).
                reader.rx_newline = self._cfg.get("eol_rx", "auto")
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
            # Primary closer of the shared handle: on the normal stop path
            # the reader reaches here first and ``disconnect`` finds nothing
            # to close.  Safe only while READER_STOP_WAIT_S dominates the read
            # timeout (see that constant); the trailing ``reader_stopped.set``
            # is what releases the waiter in ``disconnect``.
            if self._port_obj:
                try:
                    self._port_obj.close()
                except (OSError, _serial.SerialException):
                    pass
                self._port_obj = None
                self._serial_port = None
            self._reader_stopped.set()

    # -- Hardware signal control ------------------------------------------------

    def toggle_dtr(self) -> bool:
        """Toggle DTR and return new state.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError("Not connected")
        self._port_obj.dtr = not self._port_obj.dtr
        return self._port_obj.dtr

    def toggle_rts(self) -> bool:
        """Toggle RTS and return new state.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError("Not connected")
        self._port_obj.rts = not self._port_obj.rts
        return self._port_obj.rts

    def send_break(self, duration: float = 0.25) -> None:
        """Send a break signal.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError("Not connected")
        self._port_obj.send_break(duration=duration)

    def get_hw_state(self) -> tuple[bool, bool]:
        """Return current (DTR, RTS) states.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError("Not connected")
        return self._port_obj.dtr, self._port_obj.rts

    # -- Reconnection ----------------------------------------------------------

    def try_reconnect(self) -> bool:
        """Attempt a single reconnect. Returns True on success.

        Same failure modes as ``connect`` -- SerialException when the
        port is still missing / in use, OSError for permissions,
        ValueError for an unsupported parameter combo.
        """
        import serial as _serial
        try:
            port = self._open_fn(self._cfg)
            port.close()
            return True
        except (_serial.SerialException, OSError, ValueError):
            return False

    def reconnect_loop(
        self,
        *,
        interval: float = 2.5,
        on_status: Callable[[str], None] | None = None,
    ) -> bool:
        """Blocking reconnect loop - call from a background thread.

        Retries ``try_reconnect()`` every *interval* seconds until success
        or ``stop_event`` is set.  Calls *on_status* with a spinner label
        every 0.25 s so the frontend can animate.

        Returns:
            True if reconnection succeeded, False if cancelled.
        """
        spinner = "|/-\\"
        step = 0
        ticks = int(interval / 0.25)
        while not self._stop_event.is_set():
            for _ in range(ticks):
                if self._stop_event.is_set():
                    return False
                if on_status:
                    ch = spinner[step % len(spinner)]
                    on_status(f"Connecting {ch}")
                step += 1
                time.sleep(0.25)
            if self._stop_event.is_set():
                return False
            if self.try_reconnect():
                return True
        return False
