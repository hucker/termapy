"""The ZMODEM engine bound to termapy's byte transports, and the transfer cores.

The protocol engine is the ``yesterwind-xyzmodem`` package, used for ZMODEM
only (XMODEM and YMODEM stay on the vendored libraries, decision
2026-11-14).  Its ``Transport`` is async and reads a byte at a time; this
module adapts it to termapy's synchronous byte sources -- a serial reader
queue (the ``/xfer.zmodem`` commands) and a pair of buffers behind a lock
(the demo device's own peer) -- and runs one transfer with ``asyncio.run``.

This is core, not a plugin: the demo device (``demo_zmodem``) runs the same
engine the ``/xfer.zmodem`` handlers do, so the bridge and the transfer
cores live here and the command handlers are a thin surface over them
(registry ``infra-not-under-builtins``; cf. ``variables.py`` under
``var.py``).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from yesterwind_xyzmodem import ZModem
from yesterwind_xyzmodem.callbacks import TransferProgress
from yesterwind_xyzmodem.constants import DEFAULT_TIMEOUT
from yesterwind_xyzmodem.transport import Transport

if TYPE_CHECKING:
    pass

# What ends rz or sz on the device: lrzsz's canit() (zm.c) sends eight CAN,
# then ten BS to erase the CANs a shell would otherwise echo.
ZMODEM_CANCEL = bytes([0x18] * 8 + [0x08] * 10)

# One idle wait before the transport yields to the event loop, so the
# engine's own asyncio timers (its wait_for around a header) still fire.
_IDLE_SLICE_S = 0.05

Progress = Callable[[TransferProgress], None]


class TransferAborted(Exception):
    """The local side aborted (Esc, or the port closing): raised by a transport.

    Not one of the engine's own exceptions, so it propagates out of
    ``asyncio.run`` untouched: the engine catches only its ``TransferTimeout``
    and ``ProtocolError`` and ``asyncio.TimeoutError``.
    """


class ByteReader(Protocol):
    """A synchronous source of bytes: ``getc(size, timeout)`` -> bytes or None on timeout.

    ``QueueByteReader`` (the serial reader queue adapter) satisfies it; the
    transport needs nothing more of its reader.
    """

    def getc(self, size: int, timeout: float = ...) -> bytes | None: ...


class QueueTransport(Transport):
    """The engine's async ``Transport`` over a synchronous ``ByteReader`` and a write callable.

    The engine awaits one byte at a time, so each read is one direct call
    into ``reader.getc`` on the thread the command runs on.  Blocking there
    is deliberate: a command runs on a worker (registry ``commands-off-main``),
    ``asyncio.run`` gives it a private loop that runs this one task, and an
    executor hop per byte would cap a receive below 115200 baud.  While no
    bytes have arrived the transport waits in short slices and yields to the
    loop between them, which lets the engine's own ``asyncio.wait_for`` fire
    when the device stalls, and a cancel land within one slice.

    A timeout raises ``asyncio.TimeoutError``, as the engine expects; a set
    cancel event raises ``TransferAborted`` from a read or a write.
    """

    def __init__(
        self,
        reader: ByteReader,
        write: Callable[[bytes], object],
        cancel: threading.Event | None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._reader = reader
        # Not named _write: the io-semantic-channels guard forbids handler
        # code touching a ``_write`` attribute, and this is the port's write.
        self._send_bytes = write
        self._cancel = cancel
        self._timeout = timeout

    def _canceled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    async def _read(self, size: int, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            data = self._reader.getc(size, min(max(remaining, 0.0), _IDLE_SLICE_S))
            if data is not None:
                return data
            if self._canceled():
                raise TransferAborted
            if remaining <= 0:
                raise TimeoutError
            await asyncio.sleep(0)

    async def read(self, n: int) -> bytes:
        return await self._read(n, self._timeout)

    async def read_byte(self) -> int:
        return (await self._read(1, self._timeout))[0]

    async def read_with_timeout(self, n: int, timeout: float) -> bytes:
        return await self._read(n, timeout)

    async def read_byte_with_timeout(self, timeout: float) -> int:
        return (await self._read(1, timeout))[0]

    async def write(self, data: bytes) -> None:
        if self._canceled():
            raise TransferAborted
        self._send_bytes(bytes(data))


# "Over and Out": the two bytes a ZMODEM sender transmits after the ZFIN
# exchange to tell the receiver the session is truly done.  The reference
# lrzsz `rz` waits for it and does not exit without it; the yesterwind engine
# sends ZFIN but omits OO, so termapy appends it after a send completes.
_ZMODEM_OVER_AND_OUT = b"OO"


def send_files(transport: Transport, paths: list[Path], progress: Progress) -> int:
    """Send ``paths`` over ``transport`` as one ZMODEM batch; the data bytes sent."""
    with contextlib.ExitStack() as stack:
        files = [
            (str(path), stack.enter_context(path.open("rb")), path.stat().st_size) for path in paths
        ]

        async def run() -> int:
            sent = await ZModem(transport, callback=progress).send(files)
            # The engine ends with ZFIN but not the OO terminator that lrzsz
            # rz needs to exit; send it so a real receiver completes.
            await transport.write(_ZMODEM_OVER_AND_OUT)
            return sent

        return asyncio.run(run())


def receive_files(transport: Transport, folder: Path, progress: Progress) -> list[Path]:
    """Receive one ZMODEM batch over ``transport`` into ``folder``; the files written."""
    written = asyncio.run(ZModem(transport, callback=progress).receive(str(folder)))
    return [Path(name) for name in written]


# ── Stepping a transfer one host write at a time (the demo peer) ──────────────
#
# The demo device drives the engine in lock-step with the host, so a transfer
# never free-runs and never races the claim/release seam.  The engine needs a
# running event loop (it calls ``asyncio.wait_for`` internally), and the host's
# own ``/xfer.zmodem`` handler is already inside an ``asyncio.run`` on the same
# thread when it writes to the FakeSerial, so the peer's loop cannot run on that
# thread.  It runs on a worker thread instead, but as a strict request/response:
# ``feed`` hands the worker the host's bytes and blocks until the worker has
# produced its reply and parked on the next read.  One side runs at a time, so
# the determinism of a synchronous state machine holds with no shared-buffer
# race.


def _noop_progress(progress: TransferProgress) -> None:
    """The demo shows no transfer status."""


class StepTransport(Transport):
    """A ``Transport`` whose reads block until the driver thread is handed host bytes.

    ``write`` collects the device's reply in ``outbox`` and, each time, lets
    the ``feed`` caller know a step produced output.  A read waits on
    ``_input_ready`` until ``inbox`` can satisfy it; between reads the worker
    sets ``_parked`` so ``feed`` knows the step is complete.  The two events
    make one side run at a time.
    """

    def __init__(self) -> None:
        self.inbox = bytearray()
        self.outbox = bytearray()
        self._lock = threading.Lock()
        self._input_ready = threading.Event()
        self.parked = threading.Event()
        self.closed = threading.Event()

    def feed_input(self, data: bytes) -> None:
        """Hand the worker more host bytes and release a read waiting for them."""
        with self._lock:
            self.inbox.extend(data)
        self._input_ready.set()

    def take_output(self) -> bytes:
        """Remove and return everything the device has written so far."""
        with self._lock:
            reply = bytes(self.outbox)
            self.outbox.clear()
        return reply

    async def _read(self, size: int) -> bytes:
        while True:
            with self._lock:
                if len(self.inbox) >= size:
                    chunk = bytes(self.inbox[:size])
                    del self.inbox[:size]
                    return chunk
            # Nothing to serve: announce we are parked and wait for input.
            self._input_ready.clear()
            self.parked.set()
            await asyncio.get_running_loop().run_in_executor(None, self._wait_input)

    def _wait_input(self) -> None:
        while not self._input_ready.wait(timeout=0.1):
            if self.closed.is_set():
                raise TransferAborted

    async def read(self, n: int) -> bytes:
        return await self._read(n)

    async def read_byte(self) -> int:
        return (await self._read(1))[0]

    async def read_with_timeout(self, n: int, timeout: float) -> bytes:
        return await self._read(n)

    async def read_byte_with_timeout(self, timeout: float) -> int:
        return (await self._read(1))[0]

    async def write(self, data: bytes) -> None:
        with self._lock:
            self.outbox.extend(bytes(data))


class StepDriver:
    """Advance one ZMODEM coroutine in lock-step with the host, on a worker thread.

    The worker runs the transfer with ``asyncio.run``; it parks whenever the
    transport has no host bytes.  ``feed`` hands over the host's latest bytes,
    waits for the worker to park again (or finish), and returns the device's
    reply.  Only one side runs at a time, so a transfer is as deterministic as
    a synchronous state machine while still giving the engine its event loop.
    """

    def __init__(
        self,
        transport: StepTransport,
        coro: Coroutine[Any, Any, object],
    ) -> None:
        self._transport = transport
        self._coro = coro
        self._done = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        # Let the worker run to its first host read, so the opening frame is
        # staged in outbox but not yet taken (feed emits it once the host
        # sends).
        self._await_park()

    @property
    def done(self) -> bool:
        """Whether the transfer has finished (or failed)."""
        return self._done.is_set()

    def feed(self, host_bytes: bytes) -> bytes:
        """Advance the transfer with ``host_bytes``; return the device's reply bytes."""
        if self._done.is_set():
            return b""
        self._transport.feed_input(host_bytes)
        self._await_park()
        return self._transport.take_output()

    def cancel(self) -> None:
        """Stop the transfer and join the worker."""
        self._transport.closed.set()
        self._transport.feed_input(b"")
        self._thread.join(timeout=5)

    def _run(self) -> None:
        from termapy import diag_log

        try:
            asyncio.run(self._coro)
        except BaseException as e:  # noqa: BLE001 -- the demo peer must never crash the port
            self._error = e
            if not isinstance(e, TransferAborted):
                diag_log.warning("demo ZMODEM peer stopped: %s: %s", type(e).__name__, e)
        finally:
            self._done.set()
            self._transport.parked.set()

    def _await_park(self) -> None:
        """Block until the worker has parked on a read or the transfer has ended."""
        while not self._done.is_set():
            if self._transport.parked.wait(timeout=0.1):
                self._transport.parked.clear()
                # A finished worker also sets parked; re-check done.
                if not self._done.is_set():
                    return
            if self._done.is_set():
                return


def step_send(paths: list[Path]) -> tuple[StepTransport, Coroutine[Any, Any, object]]:
    """A demo ZMODEM send (device -> host): the transport and the coroutine the driver steps."""
    transport = StepTransport()

    async def run() -> int:
        with contextlib.ExitStack() as stack:
            files = [
                (str(path), stack.enter_context(path.open("rb")), path.stat().st_size)
                for path in paths
            ]
            return await ZModem(transport, callback=_noop_progress).send(files)

    return transport, run()


def step_receive(
    folder: Path, on_file: Callable[[str, bytes], None]
) -> tuple[StepTransport, Coroutine[Any, Any, object]]:
    """A demo ZMODEM receive (host -> device): the transport and the coroutine the driver steps."""
    transport = StepTransport()

    async def run() -> None:
        written = await ZModem(transport, callback=_noop_progress).receive(str(folder))
        for name in written:
            path = Path(name)
            on_file(path.name, path.read_bytes())

    return transport, run()
