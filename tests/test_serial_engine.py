"""Tests for SerialEngine - connection lifecycle and reader loop."""

import threading
import time

import pytest

from termapy.capture import CaptureEngine
from termapy.demo import FakeSerial
from termapy.serial_engine import RX_QUEUE_MAX_CHUNKS, SerialEngine


def _make_engine(cfg=None, open_fn=None):
    """Create a SerialEngine with FakeSerial (or the given open_fn) as the open function."""
    cfg = cfg or {"port": "DEMO", "baud_rate": 115200, "encoding": "utf-8",
                  "eol": "\r", "eol_markers": False}
    capture = CaptureEngine()
    logged = []
    engine = SerialEngine(
        cfg=cfg,
        capture=capture,
        open_fn=open_fn or (lambda c: FakeSerial(baudrate=c["baud_rate"])),
        log=lambda d, t: logged.append((d, t)),
    )
    return engine, capture, logged


class _BreakRecordingSerial(FakeSerial):
    """FakeSerial whose send_break records each duration instead of doing nothing."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.breaks: list[float] = []

    def send_break(self, duration: float = 0.25) -> None:
        self.breaks.append(duration)


# -- Connection lifecycle ------------------------------------------------------


class TestConnect:
    def test_connect_succeeds(self):
        """A successful connect holds the very port open_fn opened, plus a reader for it."""
        # Arrange -- keep a handle on what open_fn returns.
        opened = []
        engine, _, _ = _make_engine(
            open_fn=lambda c: opened.append(FakeSerial(baudrate=c["baud_rate"])) or opened[-1],
        )

        # Act
        result = engine.connect()

        # Assert
        assert result is True, "connected"
        assert engine.is_connected is True, "engine should be connected"
        assert engine.port_obj is opened[0], "engine holds the port open_fn opened"
        assert engine.serial_port is not None, "serial_port should be set"
        assert engine.reader is not None, "reader should be set"
        engine.disconnect()

    def test_connect_when_already_connected(self):
        """Connecting while already connected reports success instead of failing."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        result = engine.connect()

        # Assert
        assert result is True, "idempotent"
        engine.disconnect()

    def test_connect_failure(self):
        """An open_fn that raises makes connect return False and leaves it disconnected."""
        # Arrange
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"serial": {"port": "BAD", "baud_rate": 9600}, "encoding": "utf-8"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )

        # Act
        result = engine.connect()

        # Assert
        assert result is False, "failed"
        assert engine.is_connected is False, "engine should not be connected"

    def test_disconnect(self):
        """Disconnect clears both the connected flag and the serial port handle."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.disconnect()

        # Assert
        assert engine.is_connected is False, "engine should be disconnected"
        assert engine.serial_port is None, "serial_port should be cleared"

    def test_disconnect_when_not_connected(self):
        """Disconnecting a never-connected engine is a harmless no-op."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act - should not raise
        engine.disconnect()

        # Assert
        assert engine.is_connected is False, "engine should remain disconnected"


# -- Properties ----------------------------------------------------------------


class TestProperties:
    def test_port_obj_is_fake_serial(self):
        """port_obj exposes the exact object open_fn returned."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Assert
        assert isinstance(engine.port_obj, FakeSerial), "port_obj should be FakeSerial"
        engine.disconnect()

    def test_rx_queue_exists(self):
        """The RX queue exists from construction, before any connect."""
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.rx_queue is not None, "rx_queue should exist"

    def test_serial_claimed_default(self):
        """A new engine starts with the port unclaimed."""
        # Arrange
        engine, _, _ = _make_engine()

        # Assert
        assert engine.serial_claimed is False, "serial_claimed defaults to False"

    def test_serial_claimed_setter(self):
        """serial_claimed is writable and reads back what was set."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        # Act
        engine.serial_claimed = True

        # Assert
        assert engine.serial_claimed is True, "serial_claimed True after set"
        engine.disconnect()


# -- Reader loop ---------------------------------------------------------------


class TestReadLoop:
    def test_read_loop_receives_lines(self):
        """Bytes waiting on the port reach on_lines as decoded lines."""
        # Arrange - FakeSerial enqueues the reply synchronously on write, so
        # it is already waiting to be read.
        engine, _, _ = _make_engine()
        engine.connect()
        engine.port_obj.write(b"AT\r")

        lines_received: list[str] = []
        got_ok = threading.Event()

        def on_lines(lines):
            lines_received.extend(lines)
            if any("OK" in line for line in lines):
                got_ok.set()

        # Act - run the reader until it delivers the reply, then stop it.
        # Waiting on the event (not a fixed sleep) is deterministic: it
        # returns the instant the line arrives and only times out on a real
        # failure to read.
        t = threading.Thread(
            target=lambda: engine.read_loop(on_lines=on_lines), daemon=True
        )
        t.start()
        delivered = got_ok.wait(timeout=2.0)
        engine.stop_event.set()
        t.join(timeout=1.0)

        # Assert
        assert delivered is True, "reader delivered an OK line within 2s"
        assert len(lines_received) > 0, "got some output"
        assert any("OK" in line for line in lines_received), "AT should produce OK"

    def test_read_loop_stops_on_event(self):
        """Setting stop_event ends the loop, exits the thread, and signals reader_stopped."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()

        def run():
            engine.read_loop()

        # Act - the reader signals reader_stopped from its finally, so wait on
        # that instead of guessing a sleep before/after setting the stop flag.
        t = threading.Thread(target=run, daemon=True)
        t.start()
        engine.stop_event.set()
        stopped = engine.reader_stopped.wait(timeout=2.0)
        t.join(timeout=1.0)

        # Assert
        assert stopped is True, "reader signaled stopped within 2s"
        assert not t.is_alive(), "thread exited"
        assert engine.reader_stopped.is_set(), "flag set"

    def test_read_loop_calls_on_error(self):
        """A port read that raises is reported through on_error with its message."""
        # Arrange - port that raises on read
        errors = []

        class BadPort:
            is_open = True
            in_waiting = 1
            def read(self, n):
                raise OSError("read failed")
            def close(self):
                self.is_open = False

        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"encoding": "utf-8", "eol_markers": False},
            capture=capture,
            open_fn=lambda c: BadPort(),
        )
        engine.connect()

        def run():
            engine.read_loop(on_error=errors.append)

        # Act
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=1.0)

        # Assert
        assert len(errors) >= 1, "error callback fired"
        assert "read failed" in errors[0], "error message should contain 'read failed'"

    def test_read_loop_without_connect(self):
        """With no port the loop returns at once and still signals reader_stopped."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act - should return immediately
        engine.read_loop()

        # Assert
        assert engine.reader_stopped.is_set(), "reader_stopped should be set without connect"


class _NumberedPort:
    """Port double that yields ``total`` numbered one-line chunks, then reads empty.

    Injected through ``open_fn``, like ``FakeSerial``; no patching.
    """

    def __init__(self, total: int) -> None:
        self.total = total
        self.sent = 0
        self.is_open = True
        self.exhausted = threading.Event()

    @property
    def in_waiting(self) -> int:
        return 8 if self.sent < self.total else 0

    def read(self, size: int) -> bytes:
        if self.sent >= self.total:
            self.exhausted.set()
            time.sleep(0.005)  # an empty read is a read timeout
            return b""
        self.sent += 1
        return f"{self.sent:07d}\n".encode()

    def close(self) -> None:
        self.is_open = False


class TestRxQueueBounded:
    """rx-queue-bounded: with nobody reading, the raw queue holds the newest chunks only."""

    def test_a_long_run_with_no_consumer_keeps_only_the_newest_chunks(self):
        """Past the cap the reader drops the oldest chunk; the queue never grows beyond it."""
        # Arrange -- more chunks than the queue holds, and nothing draining it
        total = RX_QUEUE_MAX_CHUNKS + 500
        port = _NumberedPort(total)
        engine, _, _ = _make_engine(open_fn=lambda c: port)
        engine.connect()
        reader = threading.Thread(target=engine.read_loop, daemon=True)

        # Act
        reader.start()
        assert port.exhausted.wait(timeout=10.0), "the port never ran dry"
        engine.stop_event.set()
        reader.join(timeout=2.0)
        queued = []
        while not engine.rx_queue.empty():
            queued.append(engine.rx_queue.get_nowait())

        # Assert
        actual_count = len(queued)
        expected_count = RX_QUEUE_MAX_CHUNKS
        assert actual_count == expected_count, (
            "the queue held more than its bound -- an idle session keeps every byte"
        )
        actual_ends = (queued[0], queued[-1])
        expected_ends = (f"{total - RX_QUEUE_MAX_CHUNKS + 1:07d}\n".encode(), f"{total:07d}\n".encode())
        assert actual_ends == expected_ends, "the OLDEST chunks are the ones dropped"


# -- Reconnect ----------------------------------------------------------------


class TestReconnect:
    def test_try_reconnect_success(self):
        """try_reconnect returns True when the port opens."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act
        result = engine.try_reconnect()

        # Assert
        assert result is True, "FakeSerial always opens"

    def test_try_reconnect_failure(self):
        """try_reconnect returns False, not an exception, when the open raises."""
        # Arrange
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"port": "BAD"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )

        # Act
        result = engine.try_reconnect()

        # Assert
        assert result is False, "reconnect should fail with bad port"


# -- RX observers ---------------------------------------------------------------


class TestRxObservers:

    def test_add_and_remove(self):
        """A removed RX observer is gone from the observer list."""
        # Arrange
        engine, _, _ = _make_engine()
        received = []
        def cb(data):
            return received.append(data)

        # Act
        engine.add_rx_observer(cb)
        engine.remove_rx_observer(cb)

        # Assert - no error, observer list empty
        assert cb not in engine._rx_observers, "observer should be removed"

    def test_add_duplicate_is_noop(self):
        """Adding the same RX observer twice registers it once, so it fires once."""
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_rx_observer(cb)
        engine.add_rx_observer(cb)

        # Assert
        assert engine._rx_observers.count(cb) == 1, "should not add duplicate"

    def test_remove_nonexistent_is_noop(self):
        """Removing an RX observer that was never added does not raise."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert - should not raise
        engine.remove_rx_observer(lambda data: None)

    def test_observer_receives_rx_data(self):
        """RX observers get the raw received bytes from the reader loop."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        received = []
        got_data = threading.Event()

        def observer(data):
            received.append(data)
            got_data.set()

        engine.add_rx_observer(observer)
        engine.port_obj.write(b"AT\r")  # reply enqueued synchronously

        # Act - run the reader until the observer fires, then stop.
        t = threading.Thread(
            target=lambda: engine.read_loop(on_lines=lambda lines: None),
            daemon=True,
        )
        t.start()
        fired = got_data.wait(timeout=2.0)
        engine.stop_event.set()
        t.join(timeout=2)

        # Assert
        assert fired is True, "observer fired within 2s"
        assert len(received) > 0, "observer should have received RX data"
        assert all(isinstance(d, bytes) for d in received), "data should be bytes"
        engine.disconnect()

    def test_exception_in_observer_does_not_block_others(self):
        """One RX observer raising does not stop later observers getting data."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        received = []
        got_data = threading.Event()

        def bad_observer(data):
            raise ValueError("boom")

        def good_observer(data):
            received.append(data)
            got_data.set()

        engine.add_rx_observer(bad_observer)
        engine.add_rx_observer(good_observer)
        engine.port_obj.write(b"AT\r")  # reply enqueued synchronously

        # Act - run the reader until the good observer fires, then stop.
        t = threading.Thread(
            target=lambda: engine.read_loop(on_lines=lambda lines: None),
            daemon=True,
        )
        t.start()
        fired = got_data.wait(timeout=2.0)
        engine.stop_event.set()
        t.join(timeout=2)

        # Assert - second observer should still receive data
        assert fired is True, "second observer fired despite the first raising"
        assert len(received) > 0, "second observer should work despite first raising"
        engine.disconnect()


# -- TX observers ---------------------------------------------------------------


class TestTxObservers:

    def test_add_and_remove(self):
        """A removed TX observer is gone from the observer list."""
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_tx_observer(cb)
        engine.remove_tx_observer(cb)

        # Assert
        assert cb not in engine._tx_observers, "observer should be removed"

    def test_notify_tx_fires_observers(self):
        """notify_tx hands each payload to TX observers in send order."""
        # Arrange
        engine, _, _ = _make_engine()
        received = []
        engine.add_tx_observer(lambda data: received.append(data))

        # Act
        engine.notify_tx(b"hello")
        engine.notify_tx(b"world")

        # Assert
        assert received == [b"hello", b"world"], "observer should receive TX data"

    def test_exception_in_tx_observer_does_not_block_others(self):
        """One TX observer raising does not stop later observers getting data."""
        # Arrange
        engine, _, _ = _make_engine()
        received = []

        def bad_observer(data):
            raise ValueError("boom")

        engine.add_tx_observer(bad_observer)
        engine.add_tx_observer(lambda data: received.append(data))

        # Act
        engine.notify_tx(b"test")

        # Assert
        assert received == [b"test"], "second observer should work despite first raising"

    def test_add_duplicate_is_noop(self):
        """Adding the same TX observer twice registers it once, so it fires once."""
        # Arrange
        engine, _, _ = _make_engine()
        def cb(data):
            return None

        # Act
        engine.add_tx_observer(cb)
        engine.add_tx_observer(cb)

        # Assert
        assert engine._tx_observers.count(cb) == 1, "should not add duplicate"


# -- Hardware signal control ---------------------------------------------------


class TestHardwareSignals:
    def test_toggle_dtr(self):
        """toggle_dtr flips the port's DTR line and returns the new level; RTS is untouched."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        port = engine.port_obj
        assert isinstance(port, FakeSerial), "DEMO engine opens a FakeSerial"
        dtr_before, rts_before = port.dtr, port.rts

        # Act
        result = engine.toggle_dtr()

        # Assert
        assert result is (not dtr_before), "returns the new DTR level"
        assert (port.dtr, port.rts) == (not dtr_before, rts_before), "only DTR flipped"
        engine.disconnect()

    def test_toggle_rts(self):
        """toggle_rts flips the port's RTS line and returns the new level; DTR is untouched."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        port = engine.port_obj
        assert isinstance(port, FakeSerial), "DEMO engine opens a FakeSerial"
        dtr_before, rts_before = port.dtr, port.rts

        # Act
        result = engine.toggle_rts()

        # Assert
        assert result is (not rts_before), "returns the new RTS level"
        assert (port.dtr, port.rts) == (dtr_before, not rts_before), "only RTS flipped"
        engine.disconnect()

    def test_send_break(self):
        """send_break reaches the port once, carrying the requested duration."""
        # Arrange
        engine, _, _ = _make_engine(open_fn=lambda c: _BreakRecordingSerial())
        engine.connect()
        port = engine.port_obj
        assert isinstance(port, _BreakRecordingSerial), "engine holds the recording port"

        # Act
        engine.send_break(duration=0.1)

        # Assert
        assert port.breaks == [0.1], "one break, duration passed through"
        engine.disconnect()

    def test_get_hw_state(self):
        """get_hw_state reports the port's live levels as (DTR, RTS), in that order."""
        # Arrange -- opposite levels, so a swapped tuple cannot pass.
        engine, _, _ = _make_engine()
        engine.connect()
        port = engine.port_obj
        assert isinstance(port, FakeSerial), "DEMO engine opens a FakeSerial"
        port.dtr, port.rts = False, True

        # Act
        actual = engine.get_hw_state()

        # Assert
        assert actual == (False, True), "(DTR, RTS) mirrors the port"
        engine.disconnect()

    def test_toggle_dtr_when_disconnected(self):
        """toggle_dtr with no port raises OSError Not connected rather than crashing."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.toggle_dtr()

    def test_toggle_rts_when_disconnected(self):
        """toggle_rts with no port raises OSError Not connected rather than crashing."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.toggle_rts()

    def test_send_break_when_disconnected(self):
        """send_break with no port raises OSError Not connected rather than crashing."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.send_break()

    def test_get_hw_state_when_disconnected(self):
        """get_hw_state with no port raises OSError Not connected rather than crashing."""
        # Arrange
        engine, _, _ = _make_engine()

        # Act / Assert
        with pytest.raises(OSError, match="Not connected"):
            engine.get_hw_state()


# -- Reconnect loop ------------------------------------------------------------


@pytest.mark.slow  # ~2.5s sleep-based reconnect simulation
class TestReconnectLoop:
    def test_reconnect_loop_success(self):
        """The reconnect loop reports Connecting status while it works toward a connection."""
        # Arrange
        engine, _, _ = _make_engine()
        statuses = []

        # Act - FakeSerial always succeeds, so first try should work
        def run():
            return engine.reconnect_loop(on_status=statuses.append)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=5.0)

        # Assert
        assert len(statuses) > 0, "should have received status updates"
        assert all("Connecting" in status for status in statuses), "status should show connecting"

    def test_reconnect_loop_cancelled(self):
        """stop_event ends a reconnect loop that keeps failing, so its thread exits."""
        # Arrange - use a port that always fails
        capture = CaptureEngine()
        engine = SerialEngine(
            cfg={"port": "BAD"},
            capture=capture,
            open_fn=lambda c: (_ for _ in ()).throw(OSError("no port")),
        )
        attempted = threading.Event()

        # Act - let the loop make at least one (failing) attempt, then cancel.
        # Gating the cancel on the status callback (not a sleep) guarantees the
        # loop was actually running when we stopped it.
        def run():
            return engine.reconnect_loop(
                interval=0.5, on_status=lambda msg: attempted.set()
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        started = attempted.wait(timeout=2.0)
        engine.stop_event.set()
        t.join(timeout=2.0)

        # Assert
        assert started is True, "reconnect loop attempted within 2s"
        assert not t.is_alive(), "thread should have exited"


# -- Error classification & port-holder lookup ---------------------------------


class TestClassifySerialError:
    """_classify_serial_error maps raw exceptions to friendly messages,
    and on Unix tries to name the process holding the port."""

    def test_permission_error_without_port_name(self):
        """A chained PermissionError reads as permission denied; no port means no holder."""
        # Arrange
        from termapy.serial_engine import _classify_serial_error
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert -- no port name passed, no holder lookup performed
        assert "Permission denied" in actual, "friendly permission message"
        assert "held by" not in actual, "no holder appended when port_name empty"

    def test_file_not_found_returns_helpful_message(self):
        """A chained FileNotFoundError reads as port not found and points at /port.list."""
        # Arrange
        from termapy.serial_engine import _classify_serial_error
        inner = FileNotFoundError("no such device")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert
        assert "Port not found" in actual, "friendly not-found message"
        assert "/port.list" in actual, "points user at the discovery command"

    def test_oserror_errno_13_treated_as_permission(self, monkeypatch):
        """A bare OSError with errno 13 (EACCES) is classified as permission denied."""
        # Arrange -- errno 13 is EACCES on POSIX
        from termapy.serial_engine import _classify_serial_error
        inner = OSError(13, "Permission denied")
        outer = Exception("open failed")
        outer.__cause__ = inner
        # No holder lookup (port_name empty) so sys.platform doesn't matter.

        # Act
        actual = _classify_serial_error(outer, port_name="")

        # Assert
        assert "Permission denied" in actual, "classified as in-use"

    def test_oserror_errno_2_treated_as_not_found(self):
        """A bare OSError with errno 2 (ENOENT) is classified as port not found."""
        # Arrange -- errno 2 is ENOENT
        from termapy.serial_engine import _classify_serial_error
        inner = OSError(2, "No such file")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = _classify_serial_error(outer)

        # Assert
        assert "Port not found" in actual, "classified as missing"

    def test_permission_error_with_holder_is_appended(self, monkeypatch):
        """With a port name and a known holder, the message names the holding process."""
        # Arrange -- stub out the holder lookup to return a known value.
        import termapy.serial_engine as se
        monkeypatch.setattr(se, "_find_port_holder", lambda _p: "arduino (PID 1234)")
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = se._classify_serial_error(outer, port_name="ttyUSB0")

        # Assert
        assert "Permission denied" in actual, "still has the base message"
        assert "held by arduino (PID 1234)" in actual, "holder appended"

    def test_permission_error_holder_lookup_returns_none(self, monkeypatch):
        """A holder lookup that finds nothing leaves the base message without a holder."""
        # Arrange -- holder lookup fails silently (Windows, or lsof missing)
        import termapy.serial_engine as se
        monkeypatch.setattr(se, "_find_port_holder", lambda _p: None)
        inner = PermissionError("Access is denied")
        outer = Exception("open failed")
        outer.__cause__ = inner

        # Act
        actual = se._classify_serial_error(outer, port_name="ttyUSB0")

        # Assert
        assert "Permission denied" in actual, "base message intact"
        assert "held by" not in actual, "no holder clause when lookup returned None"

    def test_pyserial_windows_permission_via_message(self):
        """Unchained Windows pyserial errors are classified by message, raw class hidden."""
        # Arrange -- mimic the exact shape pyserial raises on Windows when
        # another app holds the port: SerialException with the OSError
        # stringified into the message and no __cause__ chaining.
        from termapy.serial_engine import _classify_serial_error
        exc = Exception(
            "could not open port 'COM4': "
            "PermissionError(13, 'Access is denied.', None, 5)"
        )
        # cause deliberately None -- this is the bug we're guarding against.
        assert exc.__cause__ is None, "pyserial doesn't chain; the test knows that"

        # Act
        actual = _classify_serial_error(exc, port_name="COM4")

        # Assert
        assert "Permission denied -- port may be in use" in actual, \
            f"classified via string match, got {actual!r}"
        assert "PermissionError" not in actual, \
            "raw exception-class text is not shown to the user"

    def test_pyserial_windows_file_not_found_via_message(self):
        """An unchained Windows missing-port error is classified as not found by message."""
        # Arrange -- same pattern for a missing port.
        from termapy.serial_engine import _classify_serial_error
        exc = Exception(
            "could not open port 'COM99': "
            "FileNotFoundError(2, 'The system cannot find the file specified.', None, 2)"
        )

        # Act
        actual = _classify_serial_error(exc, port_name="COM99")

        # Assert
        assert "Port not found" in actual, f"classified as missing, got {actual!r}"


class TestFindPortHolder:
    """_find_port_holder uses lsof on Unix; silent None on Windows
    or any failure."""

    def test_windows_returns_none(self, monkeypatch):
        """Windows skips the holder lookup entirely and returns None."""
        # Arrange
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "win32")

        # Act
        actual = se._find_port_holder("COM7")

        # Assert
        assert actual is None, "no holder lookup on Windows"

    def test_lsof_missing_returns_none(self, monkeypatch):
        """A missing lsof binary yields None instead of raising."""
        # Arrange -- simulate lsof not installed on PATH.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        def _raise_filenotfound(*args, **kwargs):
            raise FileNotFoundError("lsof")

        monkeypatch.setattr(se.subprocess, "run", _raise_filenotfound)

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "missing lsof doesn't raise, just bails silently"

    def test_lsof_timeout_returns_none(self, monkeypatch):
        """An lsof call that times out yields None instead of raising."""
        # Arrange
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        def _raise_timeout(*args, **kwargs):
            raise se.subprocess.TimeoutExpired(cmd="lsof", timeout=2.0)

        monkeypatch.setattr(se.subprocess, "run", _raise_timeout)

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "timeout bails silently"

    def test_lsof_success_parses_pid_and_command(self, monkeypatch):
        """lsof -F pc output is parsed into a command (PID n) label."""
        # Arrange -- simulate lsof -F pc output format.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        class _Result:
            returncode = 0
            stdout = "p1234\ncarduino-ide\n"

        monkeypatch.setattr(se.subprocess, "run", lambda *a, **kw: _Result())

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        expected = "arduino-ide (PID 1234)"
        assert actual == expected, f"parsed as command + PID, got {actual!r}"

    def test_lsof_success_with_nothing_holding(self, monkeypatch):
        """Empty lsof output means no holder, returned as None."""
        # Arrange -- lsof exit=0 but no output means nothing has the port.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        class _Result:
            returncode = 0
            stdout = ""

        monkeypatch.setattr(se.subprocess, "run", lambda *a, **kw: _Result())

        # Act
        actual = se._find_port_holder("ttyUSB0")

        # Assert
        assert actual is None, "empty output -> no holder identified"

    def test_lsof_absolute_path_passed_through(self, monkeypatch):
        """A port already given as an absolute path is not prefixed with /dev/ again."""
        # Arrange -- user-supplied port already looks absolute; don't
        # prepend /dev/ again.
        import termapy.serial_engine as se
        monkeypatch.setattr(se.sys, "platform", "linux")

        captured = {}

        class _Result:
            returncode = 1
            stdout = ""

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        monkeypatch.setattr(se.subprocess, "run", _fake_run)

        # Act
        se._find_port_holder("/dev/tty.usbserial-XYZ")

        # Assert -- argv[-1] is the path lsof queried.
        actual_path = captured["cmd"][-1]
        expected_path = "/dev/tty.usbserial-XYZ"
        assert actual_path == expected_path, \
            "absolute path passed through untouched"


class TestTeardownIsAJoin:
    """Teardown waits on the reader THREAD, not on an Event with a timeout.

    The old contract pinned ``SERIAL_READ_TIMEOUT_S << READER_STOP_WAIT_S`` and
    was wrong in the case it was written for: the reader's stall was bounded by
    consumer availability, not by ``read()``, so the wait expired every time RX
    was in flight.  Those tests are gone with the constant they guarded.
    """

    def test_disconnect_leaves_no_reader_thread_running(self):
        """Disconnect returns only after the reader thread has exited."""
        # Arrange
        engine, _, _ = _make_engine()
        engine.connect()
        engine.start_reader(on_lines=lambda _lines: None)
        thread = engine._reader_thread
        assert thread is not None and thread.is_alive(), "reader should be running"

        # Act
        engine.disconnect()

        # Assert -- genuinely exited, not probably-exited.
        assert thread.is_alive() is False, (
            "disconnect returned while the reader thread was still alive; "
            "teardown must join the thread, not wait on an Event"
        )

    def test_stop_reader_is_idempotent_and_safe_with_no_reader(self):
        """stop_reader with no reader reports success, and repeat calls stay safe."""
        # Arrange -- never connected, never started.
        engine, _, _ = _make_engine()

        # Act / Assert -- must not raise, and must report success.
        assert engine.stop_reader() is True, "no reader is trivially stopped"
        assert engine.stop_reader() is True, "repeat calls stay safe"

    def test_start_reader_joins_the_previous_generation(self):
        """Starting a new reader leaves no previous-generation reader alive."""
        # Arrange -- a reader is already running.
        engine, _, _ = _make_engine()
        engine.connect()
        engine.start_reader(on_lines=lambda _lines: None)
        first = engine._reader_thread

        # Act -- reconnect and start a new one.
        engine.disconnect()
        engine.connect()
        engine.start_reader(on_lines=lambda _lines: None)

        # Assert -- exactly one reader alive; the old one is gone, so it can
        # no longer reach a teardown that touches the new connection.
        assert first is not None and first.is_alive() is False, (
            "the previous generation's reader outlived start_reader"
        )
        assert engine._reader_thread is not first, "a new thread should be running"
        engine.disconnect()


class _ControlledPort:
    """Serial-port double whose reads never run dry and whose close is traceable.

    Injected through ``SerialEngine``'s existing ``open_fn`` seam (the same
    seam ``_make_engine`` uses for ``FakeSerial``), so no patching is involved.

    Args:
        tag: Human label used in assertion messages.
    """

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.is_open = True
        self.closed_by: str | None = None

    @property
    def in_waiting(self) -> int:
        return 16

    def read(self, size: int) -> bytes:
        return b"LINE\r\n"

    def close(self) -> None:
        self.is_open = False
        self.closed_by = threading.current_thread().name


class TestReaderOwnership:
    """A reader must only ever tear down the generation it belongs to.

    Teardown is now a real join, so a stale reader is *prevented* rather than
    merely survived -- but the join has a timeout, and a callback that blocks
    forever can still outlive it.  These tests cover that degraded path: when
    it happens, ownership by IDENTITY (``_reader_gen`` plus the
    ``self._port_obj is port`` check) has to keep the stale generation away
    from the new connection.

    Regression guard for the reproduced bug where a stale reader closed the
    port the user had just selected, leaving the app "connected" to a dead
    handle (docs/review/2026-08-19-v0.74.0-opus-5.md, finding T1).
    """

    def _engine_with_ports(self, ports):
        cfg = {"port": "DEMO", "baud_rate": 115200, "encoding": "utf-8",
               "eol": "\r", "eol_markers": False}
        queue = list(ports)
        return SerialEngine(
            cfg=cfg,
            capture=CaptureEngine(),
            open_fn=lambda c: queue.pop(0),
            log=lambda d, t: None,
        )

    def _stale_reader_scenario(self):
        """Park a reader in on_lines, then disconnect+reconnect past it.

        Returns:
            ``(engine, first, second, release, thread)`` with the generation-1
            reader still parked -- the caller releases it and joins.
        """
        # Arrange -- generation 1 connected and streaming.
        first, second = _ControlledPort("first"), _ControlledPort("second")
        engine = self._engine_with_ports([first, second])
        assert engine.connect() is True, "generation 1 should connect"

        parked, release = threading.Event(), threading.Event()

        def on_lines(_lines):
            # Stands in for call_from_thread blocking on a busy event loop.
            parked.set()
            release.wait(5)

        # Started directly rather than via start_reader: this models the
        # degraded path where the join ALREADY expired and left the thread
        # running, which is the only way a stale reader still exists.
        engine._reader_stopped.clear()
        thread = threading.Thread(
            target=lambda: engine.read_loop(on_lines=on_lines),
            name="reader-gen1",
            daemon=True,
        )
        thread.start()
        assert parked.wait(2) is True, "reader should reach on_lines"

        # Act -- a disconnect/reconnect turn that could not wait for the
        # parked reader, exactly as _switch_config and the port picker do.
        engine.disconnect()
        assert engine.connect() is True, "generation 2 should connect"

        return engine, first, second, release, thread

    def test_stale_reader_cannot_touch_the_next_connection(self):
        """A stale reader neither closes the new port nor marks the new reader stopped."""
        # Arrange / Act -- release the stale generation and let its finally run.
        engine, _first, second, release, thread = self._stale_reader_scenario()
        release.set()
        thread.join(5)

        # Assert -- the port the user just selected survives.
        assert second.is_open is True, (
            f"generation-2 port was closed by {second.closed_by!r}; a stale "
            f"reader must close only the handle it read from"
        )
        assert second.closed_by is None, "generation-2 port must not be closed at all"
        assert engine.is_connected is True, (
            "engine should still report the generation-2 connection as live"
        )
        # ...and it must not claim generation 2's reader has stopped, which
        # would let the NEXT teardown skip its wait entirely.
        assert engine.reader_stopped.is_set() is False, (
            "a stale generation set reader_stopped, disarming the gate the "
            "current generation relies on"
        )
        engine.disconnect()

    def test_stop_reader_reports_a_callback_that_will_not_let_go(self):
        """A reader stuck in its callback makes stop_reader fail loudly with a reason."""
        # Arrange -- a reader parked in its callback, started through the
        # real path so the engine owns the thread.
        only = _ControlledPort("only")
        engine = self._engine_with_ports([only])
        assert engine.connect() is True, "should connect"
        parked, release = threading.Event(), threading.Event()

        def on_lines(_lines):
            parked.set()
            release.wait(30)

        engine.start_reader(on_lines=on_lines)
        assert parked.wait(2) is True, "reader should reach on_lines"

        # Act -- the join cannot succeed while the callback holds on.
        actual = engine.stop_reader()

        # Assert -- reported, not silently swallowed, so the degraded path is
        # visible rather than looking like a clean teardown.
        assert actual is False, (
            "stop_reader must report failure when the reader is still alive; "
            "silently returning success is what made the old timeout handshake "
            "look correct while it was expiring every time"
        )
        assert "did not exit" in engine.last_error, (
            f"the reason should be recorded, got {engine.last_error!r}"
        )
        release.set()

    def test_reader_closes_its_own_port_on_the_normal_path(self):
        """On a clean stop the owning reader thread closes its port and signals stopped."""
        # Arrange -- no interference: one generation, stopped normally.
        only = _ControlledPort("only")
        engine = self._engine_with_ports([only])
        assert engine.connect() is True, "should connect"

        thread = threading.Thread(
            target=lambda: engine.read_loop(), name="reader-solo", daemon=True
        )
        thread.start()

        # Act
        engine.stop_event.set()
        thread.join(5)

        # Assert -- ownership held, so the reader still does the closing.
        assert only.is_open is False, "reader should close the port it opened over"
        assert only.closed_by == "reader-solo", (
            f"port should be closed by the reader thread, not {only.closed_by!r}"
        )
        assert engine.reader_stopped.is_set() is True, (
            "the owning generation must still signal reader_stopped"
        )
