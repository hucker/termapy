"""Tests that run only when a real hardware loopback is plugged in.

These automate the checks that were previously done by hand against a
TX-RX-jumpered USB-serial adapter.  They exist because a whole class of
defects is invisible to ``FakeSerial`` and to pyserial's ``loop://``:
neither has an OS driver, a bridge chip, a finite FIFO, or real baud pacing,
so neither can drop a byte.  Every finding in
``docs/review/2026-08-19-v0.74.0-opus-5.md`` numbered T15/T16 was found only
once real hardware was attached.

Detection uses termapy's own serial-number resolution, so the rig is
identified by identity rather than by a COM number that moves between boots.
Point them at a different adapter with::

    TERMAPY_LOOPBACK_SN=<serial-number> uv run pytest -m hardware

Everything here skips cleanly when the adapter is absent, so CI and other
machines are unaffected.

SAFETY: the fixture refuses to run unless the port demonstrably echoes its
own output.  These tests write tens of kilobytes at speed; that is harmless
into a jumper and unacceptable into a real device.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from termapy.capture import CaptureEngine
from termapy.config import SERIAL_READ_TIMEOUT_S, SERIAL_RX_BUFFER_BYTES
from termapy.port_control import resolve_port
from termapy.serial_engine import READER_JOIN_TIMEOUT_S, SerialEngine

# Only one process may hold the port.  Without the group, xdist scatters
# these across workers and every worker but one fails to open it -- which
# would surface as a SKIP that reads exactly like "no hardware attached".
pytestmark = [
    pytest.mark.slow,
    pytest.mark.hardware,
    pytest.mark.xdist_group("serial_hardware"),
]

# The rig this suite was written against.  Overridable so the tests are not
# welded to one person's hardware.
LOOPBACK_SN = os.environ.get("TERMAPY_LOOPBACK_SN", "BG03U7VTA")

BAUD = 921600
# Stall that used to cost ~21% of the stream: above 4096 / (baud / 10) ms,
# the threshold set by pyserial's hardcoded 4 KB Windows RX buffer.
SLOW_CONSUMER_S = 0.060
FLOOD_KB = 64
_LINE = b"X" * 62 + b"\r\n"          # 64 bytes
_KB = _LINE * 16                     # 1024 bytes


def _open_raw(device: str, baud: int = BAUD):
    """Open the port the way termapy does, including the RX-buffer request."""
    import serial

    from termapy.config import request_rx_buffer

    port = serial.Serial(device, baud, timeout=SERIAL_READ_TIMEOUT_S)
    request_rx_buffer(port)
    return port


@pytest.fixture(scope="session")
def loopback_device() -> str:
    """Resolve the loopback adapter by serial number, or skip.

    Verifies the port genuinely echoes before handing it over -- see the
    SAFETY note in the module docstring.
    """
    from termapy.port_control import AmbiguousSerialNumberError

    try:
        device = resolve_port(LOOPBACK_SN)
    except AmbiguousSerialNumberError as exc:
        # Two adapters advertising the same serial number.  Rare, but it must
        # SKIP rather than error: absent-or-unusable hardware may never turn a
        # run red on a machine that simply isn't the rig.
        pytest.skip(f"serial number {LOOPBACK_SN!r} is ambiguous: {exc}")
    if device == LOOPBACK_SN:
        # resolve_port returns the spec verbatim when nothing matched.
        pytest.skip(f"no serial adapter with serial number {LOOPBACK_SN!r} attached")

    import serial

    probe = b"TERMAPY-LOOPBACK-PROBE\n"
    try:
        port = _open_raw(device, 115200)
    except (OSError, serial.SerialException) as exc:
        # Deliberately NOT phrased as "no hardware": the adapter IS attached
        # and something else is holding it (a running termapy, most likely).
        # A skip that reads like absent hardware would quietly hide the fact
        # that these checks never ran.
        pytest.skip(
            f"{device} (SN {LOOPBACK_SN}) is ATTACHED BUT BUSY -- close the "
            f"app holding it and re-run; these tests did NOT execute ({exc})"
        )
    try:
        port.reset_input_buffer()
        port.write(probe)
        port.flush()
        deadline = time.monotonic() + 1.0
        seen = b""
        while time.monotonic() < deadline and probe.strip() not in seen:
            seen += port.read(port.in_waiting or 1)
    finally:
        port.close()

    if probe.strip() not in seen:
        pytest.skip(
            f"{device} (SN {LOOPBACK_SN}) did not echo -- refusing to run "
            f"throughput tests against what may be a live device"
        )
    return device


@pytest.fixture
def engine(loopback_device):
    """A connected SerialEngine on the loopback, torn down after the test."""
    cfg = {
        "serial": {"port": loopback_device, "baud_rate": BAUD},
        "encoding": "utf-8",
        "eol_markers": False,
        "eol_rx": "auto",
    }
    eng = SerialEngine(
        cfg=cfg,
        capture=CaptureEngine(),
        open_fn=lambda c: _open_raw(loopback_device),
        log=lambda d, t: None,
    )
    assert eng.connect() is True, f"could not connect to {loopback_device}"
    try:
        yield eng
    finally:
        eng.stop_event.set()
        eng.disconnect()


def _flood(eng, kb: int = FLOOD_KB) -> int:
    """Write *kb* KB into the loopback.  Returns bytes sent."""
    sent = 0
    for _ in range(kb):
        eng.port_obj.write(_KB)
        sent += len(_KB)
    eng.port_obj.flush()
    return sent


def _settle(counter: list[int], quiet_s: float = 1.5, cap_s: float = 60.0) -> None:
    """Block until *counter* stops growing for *quiet_s*."""
    last, quiet, start = counter[0], time.monotonic(), time.monotonic()
    while time.monotonic() - quiet < quiet_s and time.monotonic() - start < cap_s:
        time.sleep(0.05)
        if counter[0] != last:
            last, quiet = counter[0], time.monotonic()


class TestDriverBufferLoss:
    """T15: pyserial hardcodes a 4 KB Windows RX buffer.

    Any main-thread stall longer than ``4096 / (baud / 10)`` ms overflowed it
    and the driver silently discarded the excess -- no exception, no gap
    marker, nothing in ``in_waiting``.
    """

    def test_slow_consumer_does_not_lose_bytes(self, engine):
        # Arrange -- a consumer slower than the old 44 ms threshold at this
        # baud.  With pyserial's default buffer this lost ~21% of the stream.
        received = [0]
        engine.add_rx_observer(lambda d: received.__setitem__(0, received[0] + len(d)))
        reader = threading.Thread(
            target=lambda: engine.read_loop(
                on_lines=lambda _lines: time.sleep(SLOW_CONSUMER_S)
            ),
            daemon=True,
        )
        reader.start()

        # Act
        sent = _flood(engine)
        _settle(received)

        # Assert -- a THRESHOLD rather than exact equality, deliberately.
        # The regression this guards is deterministic and large: with
        # pyserial's 4 KB buffer this stall loses ~21% every time.  A healthy
        # run loses 0% almost always but occasionally a few hundred bytes to
        # OS scheduling jitter at 921600, so asserting == sent is flaky.
        # 5% sits 4x below the regression and well above the jitter.
        lost = sent - received[0]
        actual = 100.0 * lost / sent
        assert actual < 5.0, (
            f"lost {lost} of {sent} bytes ({actual:.2f}%) with a "
            f"{SLOW_CONSUMER_S * 1000:.0f}ms consumer -- that is the signature "
            f"of the driver RX buffer being back at pyserial's 4 KB default "
            f"(expected {SERIAL_RX_BUFFER_BYTES}); see config.request_rx_buffer"
        )

    def test_buffer_request_is_actually_applied_to_this_port(self, loopback_device):
        # Arrange / Act -- the real Windows backend is the only one with the
        # API, so this asserts the call path end-to-end on real hardware.
        import serial

        from termapy.config import request_rx_buffer

        port = serial.Serial(loopback_device, BAUD, timeout=SERIAL_READ_TIMEOUT_S)
        try:
            actual = request_rx_buffer(port)
        finally:
            port.close()

        # Assert
        assert actual is True, (
            "the driver refused the RX buffer request; without it a stalled "
            "main thread silently drops received bytes"
        )


class TestDrainPurgesDriverBuffer:
    """T16: ``drain()`` emptied the queue but not the driver.

    Bytes parked in the driver survived the drain and arrived afterwards,
    landing in whatever reply the caller read next.  Every request/response
    path drains first precisely to get a clean slate.
    """

    def test_backlog_parked_in_the_driver_does_not_survive_drain(self, engine):
        # Arrange -- reader held off, so the backlog sits in the driver where
        # a queue-only drain cannot see it.  This is what happens when a
        # script issues /ping while the main thread is mid-render.
        hold = threading.Event()
        hold.set()

        def on_lines(_lines):
            while hold.is_set():
                time.sleep(0.005)

        reader = threading.Thread(
            target=lambda: engine.read_loop(on_lines=on_lines), daemon=True
        )
        reader.start()
        sent = _flood(engine, kb=128)
        time.sleep(sent / (BAUD / 10) + 0.5)

        # Act -- drain while the reader is STILL held off.
        engine.serial_port.drain()
        hold.clear()

        # Assert -- nothing pre-drain may reach the next reader.
        stale, deadline = 0, time.monotonic() + 2.0
        while time.monotonic() < deadline:
            time.sleep(0.05)
            while not engine.rx_queue.empty():
                stale += len(engine.rx_queue.get_nowait())
        assert stale == 0, (
            f"{stale} pre-drain bytes reached the next read -- drain() is not "
            f"purging the driver buffer, so they will be mistaken for the "
            f"reply to whatever command runs next"
        )


class TestReaderTeardown:
    """T1/T2: teardown ownership and promptness against a real driver."""

    def test_reader_stops_promptly_with_rx_in_flight(self, engine):
        # Arrange -- a live stream, then stop mid-flow.  The reader must
        # reach its stop check within a read timeout; it can only do that
        # because the RX handoff no longer blocks on the consumer.
        reader = threading.Thread(
            target=lambda: engine.read_loop(on_lines=lambda _l: None), daemon=True
        )
        reader.start()
        _flood(engine, kb=16)

        # Act
        started = time.monotonic()
        engine.stop_event.set()
        stopped = engine.stop_reader()
        elapsed = time.monotonic() - started

        # Assert
        assert stopped is True, (
            f"reader did not exit within {READER_JOIN_TIMEOUT_S}s "
            f"(took at least {elapsed:.3f}s) -- teardown is back to relying "
            f"on the wait expiring rather than the reader actually finishing"
        )

    def test_stale_reader_does_not_close_the_reconnected_port(self, engine):
        # Arrange -- park generation 1 in its callback so it outlives its own
        # disconnect, exactly as a blocked consumer used to.
        hold = threading.Event()
        hold.set()
        parked = threading.Event()

        def on_lines(_lines):
            parked.set()
            while hold.is_set():
                time.sleep(0.005)

        reader = threading.Thread(
            target=lambda: engine.read_loop(on_lines=on_lines),
            name="hw-reader-gen1",
            daemon=True,
        )
        reader.start()
        _flood(engine, kb=4)
        assert parked.wait(5) is True, "generation 1 should reach its callback"

        # Act -- disconnect and reconnect while gen 1 is still alive, then
        # let it run its teardown against the NEW handle.
        engine.disconnect()
        assert engine.connect() is True, "reconnect should succeed"
        new_port = engine.port_obj
        hold.clear()
        reader.join(5)
        time.sleep(0.2)

        # Assert
        assert engine.is_connected is True, (
            "a stale reader tore down the reconnected port -- the app would "
            "report Connected over a dead handle"
        )
        assert engine.port_obj is new_port, "the new handle must still be installed"
        assert getattr(new_port, "is_open", False) is True, "new port stays open"
