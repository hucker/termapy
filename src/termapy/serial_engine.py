"""SerialEngine - orchestrates serial port, reader, and capture.

Owns the connection lifecycle and reader loop. No Textual dependency -
the caller provides callbacks for UI events and runs ``read_loop`` in
a background thread.
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
from threading import Event
from typing import TYPE_CHECKING, Any, Callable

from termapy.capture import CaptureEngine
from termapy.plugins import BoundaryException
from termapy.plugins.capabilities import STATE_MESSAGES
from termapy.serial_port import SerialPort, SerialReader

if TYPE_CHECKING:
    from termapy.port_control import PortSource

# Upper bound on ``disconnect``'s join.  Teardown is a real
# ``Thread.join()`` -- the engine owns the reader thread (``start_reader``),
# so there is a thread to join rather than an Event to gamble on.
#
# This number is a safety net, not a contract: the reader reaches its stop
# check within one ``config.SERIAL_READ_TIMEOUT_S`` (50 ms) because no reader
# callback may block on another thread.  That is the ONE invariant this
# depends on -- any new ``read_loop`` callback that blocks (a bare
# ``call_from_thread``, a lock the main thread holds) reintroduces the
# deadlock this replaced, where teardown waited on the reader from the very
# thread the reader was waiting for.  Hand work off without blocking; see
# ``app._rx_enqueue``.
#
# Degrades safely if it ever expires: ownership is established by IDENTITY
# (the ``self._port_obj is port`` check in ``read_loop``'s finally), so a
# reader that outlives its join can only ever close the handle it read from
# -- never a newer connection's.
READER_JOIN_TIMEOUT_S = 2.0

# Largest single ``port.read`` the reader issues, so the largest chunk it ever
# queues.
RX_READ_MAX_BYTES = 4096

# Bound on ``rx_queue``, in chunks: at most 4 MiB at ``RX_READ_MAX_BYTES``.
# The reader feeds the queue on every chunk, but only request/response
# commands read it, and each of those drains it first, inside
# ``ctx.serial.io()``, and then reads as bytes arrive.  So the queue only has
# to hold what one waiting reader has not consumed yet.  Unbounded, it kept
# every byte a session received when nothing was reading, for the life of the
# connection.  When full the reader discards the OLDEST chunk (registry
# ``rx-queue-bounded``).
RX_QUEUE_MAX_CHUNKS = 1024


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


def _classify_serial_error(
    exc: Exception, port_name: str = "", *, source: PortSource | None = None
) -> str:
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
    recognize PermissionError / FileNotFoundError cases.

    ``source`` is forwarded to the trace and the currently-connected
    listing (see ``port_control.resolve_port_source``).  ``connect()``
    never sets it -- a real failure is always classified against real
    hardware -- so it exists to make the diagnostic reproducible against
    a known fleet.
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
        trace = resolve_port_trace(port_name, source=source)
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
        facts = _gather_all_chip_facts(fast=True, source=source)
        if facts:
            conn = ", ".join(
                f"{fact.device} ({fact.manufacturer or '?'}, SN {fact.serial or 'n/a'})"
                for fact in facts
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
        self._rx_queue: queue.Queue[bytes] = queue.Queue(maxsize=RX_QUEUE_MAX_CHUNKS)
        self._stop_event = Event()
        # The reader thread this engine owns, so teardown can join it rather
        # than wait on an Event and proceed regardless.
        self._reader_thread: "threading.Thread | None" = None
        # Monotonic stamp of the last chunk the reader actually received.
        # ``SerialPort.wait_for_idle`` keys off this rather than the port's
        # ``in_waiting``, which the reader keeps drained to zero.
        self._last_rx_monotonic: float = time.monotonic()
        self._serial_claimed: bool = False
        self._rx_observers: list[Callable[[bytes], None]] = []
        self._tx_observers: list[Callable[[bytes], None]] = []
        self.last_error: str = ""
        # Makes check-and-open atomic: a /port.connect worker and an
        # auto-reconnect run can open at once, and POSIX lets both succeed,
        # leaving one handle nobody owns.
        self._connect_lock = threading.Lock()

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
        """The raw RX byte queue (fed by read_loop, consumed by SerialPort.read_raw).

        Bounded at ``RX_QUEUE_MAX_CHUNKS``; when full the reader discards the
        oldest chunk.  Drain it, then read it as bytes arrive.
        """
        return self._rx_queue

    def _queue_rx(self, data: bytes) -> None:
        """Put a chunk on ``rx_queue``, discarding the oldest when it is full.

        Reader thread only, the queue's one producer, so the loop ends: once a
        chunk is removed nothing else can refill the slot first.  A consumer
        that empties the queue in between just makes the next put succeed.
        """
        while True:
            try:
                self._rx_queue.put_nowait(data)
                return
            except queue.Full:
                try:
                    self._rx_queue.get_nowait()
                except queue.Empty:
                    pass

    @property
    def stop_event(self) -> Event:
        """Event to signal the reader loop to stop.

        The reader's alone: ``reconnect_loop`` takes its own token.
        """
        return self._stop_event

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
        for tx_observer in self._tx_observers:
            try:
                tx_observer(data)
            except BoundaryException:
                pass

    def connect(self) -> bool:
        """Open the serial port and create SerialPort + SerialReader.

        Returns:
            True if the port is open, by this call or an earlier one.
        """
        with self._connect_lock:
            if self.is_connected:
                return True
            return self._open()

    def _open(self) -> bool:
        """The body of ``connect``, also run by ``reconnect_loop``; the caller holds ``_connect_lock``.

        Every open goes through here, so every failure is classified the
        same way and none escapes as an exception (registry
        ``reconnect-opens-once``).
        """
        # A prior reader must be GONE, not probably-gone, before a new handle
        # opens; stop_reader is a real join, not a timed wait.
        self.stop_reader()
        import serial as _serial

        from termapy.port_control import AmbiguousSerialNumberError
        # pyserial open() can raise SerialException (typical: port in
        # use, port missing), OSError (permissions, device vanished),
        # or ValueError (bad baud / unsupported parameter combo).
        # AmbiguousSerialNumberError comes from resolve_port() when a
        # user's SN spec matches 2+ connected devices.
        # _classify_serial_error normalizes each into a friendly
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

        self._last_rx_monotonic = time.monotonic()
        self._serial_port = SerialPort(
            port=self._port_obj,
            rx_queue=self._rx_queue,
            log=self._log,
            encoding=self._cfg.get("encoding", "utf-8"),
            # Lets wait_for_idle ask "when did the DEVICE last speak?" instead
            # of inspecting a port buffer this engine keeps drained.
            last_rx=lambda: self._last_rx_monotonic,
        )
        self._reader = SerialReader(
            encoding=self._cfg.get("encoding", "utf-8"),
            show_line_endings=self._cfg.get("eol_markers", False),
            capture=self._capture,
            serial_claimed=lambda: self._serial_claimed,
            rx_newline=self._cfg.get("eol_rx", "auto"),
        )
        self._stop_event.clear()
        return True

    def start_reader(self, **callbacks: Any) -> None:
        """Run ``read_loop`` on a thread this engine owns.

        The engine creates the thread so ``disconnect`` has something real to
        join.  Previously each frontend spun up its own -- the TUI via a
        Textual ``@work``, the CLI via a thread it dropped on the floor, MCP
        via one it kept -- so teardown could only wait on an Event and hope.

        Args:
            **callbacks: Passed straight to ``read_loop`` (``on_lines``,
                ``on_clear``, ``on_capture_done``, ``on_error``,
                ``on_disconnect``, ``on_hex``, plus the ``hex_view``
                predicate).  None of them may block on another thread;
                see ``READER_JOIN_TIMEOUT_S``.
        """
        self.stop_reader()
        self._stop_event.clear()
        thread = threading.Thread(
            target=self.read_loop,
            kwargs=callbacks,
            name="termapy-reader",
            daemon=True,
        )
        self._reader_thread = thread
        thread.start()

    def stop_reader(self) -> bool:
        """Stop the reader thread and wait for it to actually exit.

        Idempotent, and safe to call when no reader is running.

        Returns:
            True if no reader is left running.  False means the join expired
            -- see ``READER_JOIN_TIMEOUT_S`` for why that degrades safely
            rather than corrupting the next connection.
        """
        self._stop_event.set()
        thread = self._reader_thread
        if thread is None:
            return True
        if thread is threading.current_thread():
            # A reader callback is tearing down its own engine; joining here
            # would deadlock on itself.  The loop is already unwinding.
            return True
        thread.join(timeout=READER_JOIN_TIMEOUT_S)
        alive = thread.is_alive()
        if not alive:
            self._reader_thread = None
        else:
            self.last_error = (
                f"reader thread did not exit within {READER_JOIN_TIMEOUT_S}s "
                f"-- a read_loop callback is blocking"
            )
        return not alive

    def disconnect(self) -> None:
        """Stop the reader, then close the port.

        The reader normally closes its own handle on the way out, so the
        close below is the fallback for the reader-never-started case (and
        for a join that expired).
        """
        import serial as _serial
        self.stop_reader()
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
        on_hex: Callable[[list[str], list[str]], None] | None = None,
        hex_view: Callable[[], bool] | None = None,
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
            on_hex: Called in place of ``on_lines`` while the hex view is
                on, with the hex rows to display and the decoded lines for
                the log, expect watcher and text capture.
            hex_view: Asked once per chunk whether the hex view is on, so a
                toggle takes effect at a chunk boundary and every chunk is
                delivered in exactly one view.  Ignored without ``on_hex``.
        """
        reader = self._reader
        port = self._port_obj
        if not reader or not port:
            return

        import serial as _serial
        try:
            while not self._stop_event.is_set():
                if not getattr(port, "is_open", False):
                    break
                failure = ""
                try:
                    waiting = getattr(port, "in_waiting", 0) or 1
                    data = port.read(min(waiting, RX_READ_MAX_BYTES))

                    if data:
                        # One authoritative "device last spoke" clock.
                        # Consumers cannot use the port's ``in_waiting`` for
                        # this: THIS loop drains it to zero continuously, so a
                        # sample taken mid-response reads 0 and looks like
                        # silence.
                        self._last_rx_monotonic = time.monotonic()
                        self._queue_rx(data)
                        for rx_observer in self._rx_observers:
                            # RX observers are third-party callbacks;
                            # BoundaryException signals the reviewed broad
                            # catch so one misbehaving observer can't
                            # break the others or stop the reader loop.
                            try:
                                rx_observer(data)
                            except BoundaryException:
                                pass

                    # Resync the receive-newline mode from the (shared) cfg
                    # each chunk so /term.eol.rx takes effect live, without a
                    # reconnect.  Cheap dict read; the cfg dict is the same
                    # object /cfg mutates (see how TX line_ending stays live).
                    reader.rx_newline = self._cfg.get("eol_rx", "auto")
                    # The same live resync for /term.hex, which is a session
                    # flag the host owns rather than a cfg key.
                    reader.hex_display = bool(on_hex and hex_view and hex_view())
                    result = reader.process(data)

                    if result.capture_target_reached and on_capture_done:
                        on_capture_done()
                    if result.clear_screen and on_clear:
                        on_clear()
                    if on_hex and result.hex_rows is not None:
                        if result.hex_rows or result.lines:
                            on_hex(result.hex_rows, result.lines)
                    elif result.lines and on_lines:
                        on_lines(result.lines)

                    if not data:
                        time.sleep(0.01)
                except (OSError, _serial.SerialException) as exc:
                    # pyserial raises SerialException on a lost device and
                    # OSError on an underlying fd failure; either is an
                    # effective disconnect.
                    failure = f"{exc.__class__.__name__}: {exc}"
                except BoundaryException as exc:
                    # Last resort: a bug in SerialReader or a frontend
                    # callback.  Ending the thread silently would leave the
                    # frontend showing Connected over a port the ``finally``
                    # below closes, so it is reported like a lost port
                    # (registry ``reader-failure-reported``).
                    failure = (
                        f"reader stopped by an internal error -- "
                        f"{exc.__class__.__name__}: {exc}"
                    )
                if failure:
                    if on_error:
                        on_error(failure)
                    if on_disconnect:
                        on_disconnect()
                    break
        finally:
            # Close only the handle THIS loop read from -- ``port`` is the
            # local captured at entry, never the shared field.  A reader can
            # outlive its own teardown (``stop_reader``'s join has a timeout,
            # as a safety net), and by the time it lands here ``_port_obj`` may
            # already belong to a newer connection.  Closing the shared field
            # would kill that new port; closing our own local is always right,
            # and double-close is a no-op.
            try:
                port.close()
            except (OSError, _serial.SerialException):
                pass
            # Disown the shared fields only while they still point at OUR
            # port.  Once a later connect() has replaced them they belong to
            # that connection.
            if self._port_obj is port:
                self._port_obj = None
                self._serial_port = None

    # -- Hardware signal control ------------------------------------------------

    def toggle_dtr(self) -> bool:
        """Toggle DTR and return new state.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError(STATE_MESSAGES["serial_connected"])
        self._port_obj.dtr = not self._port_obj.dtr
        return self._port_obj.dtr

    def toggle_rts(self) -> bool:
        """Toggle RTS and return new state.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError(STATE_MESSAGES["serial_connected"])
        self._port_obj.rts = not self._port_obj.rts
        return self._port_obj.rts

    def send_break(self, duration: float = 0.25) -> None:
        """Send a break signal.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError(STATE_MESSAGES["serial_connected"])
        self._port_obj.send_break(duration=duration)

    def get_hw_state(self) -> tuple[bool, bool]:
        """Return current (DTR, RTS) states.

        Raises:
            OSError: If the port operation fails.
        """
        if not self._port_obj:
            raise OSError(STATE_MESSAGES["serial_connected"])
        return self._port_obj.dtr, self._port_obj.rts

    # -- Reconnection ----------------------------------------------------------

    def reconnect_loop(
        self,
        stop: Event,
        *,
        interval: float = 2.5,
        on_status: Callable[[str], None] | None = None,
    ) -> Any | None:
        """Blocking reconnect loop - call from a background thread.

        Tries to open the port every *interval* seconds until it opens or
        *stop* is set.  Calls *on_status* with a spinner label every 0.25 s
        so the frontend can animate.

        An attempt is a real open through ``connect``'s own path, and a
        successful one is kept: the caller finishes that connection rather
        than opening the port a second time.  The probe-then-reopen this
        replaced toggled DTR twice per reconnect (a double reset on an
        auto-reset board) and, catching less than ``connect`` does, let an
        ambiguous serial number escape the loop (registry
        ``reconnect-opens-once``).

        *stop* belongs to this one run, never the reader's ``stop_event``.
        Sharing that event let any connect attempt end the loop:
        ``stop_reader()`` sets it, so a connect that then failed left
        nothing retrying (registry ``reconnect-survives-connect-attempts``).

        Args:
            stop: This run's cancel token; set it to end the loop.
            interval: Seconds between open attempts.
            on_status: Called with a spinner label each tick.

        Returns:
            The port object this run opened, or None if the run was
            cancelled or another connect opened the port first.  The engine
            is connected but has no reader: finish the connection, or
            ``disconnect`` if it is no longer wanted.
        """
        spinner = "|/-\\"
        step = 0
        ticks = int(interval / 0.25)
        while not stop.is_set():
            for _ in range(ticks):
                if stop.is_set():
                    return None
                if on_status:
                    ch = spinner[step % len(spinner)]
                    on_status(f"Connecting {ch}")
                step += 1
                stop.wait(0.25)
            with self._connect_lock:
                if stop.is_set() or self.is_connected:
                    return None
                if self._open():
                    return self._port_obj
        return None
