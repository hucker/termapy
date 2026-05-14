"""SerialHandle -- all serial I/O operations.

Reachable as ``ctx.serial.*``.  Domain: reading and writing to the
serial port, observing TX/RX bytes, claiming exclusive access for
synchronous read cycles.

Self-contained dataclass: every operation is a callable field that
the host wires at construction time.  Context-manager methods
(``io``, ``rx_observer``, ``tx_observer``) live here as real methods
that delegate to the underscore-prefixed register/unregister
callables.

This handle is **not** capability-gated at the method level.  The
``serial_io`` baseline capability is always provided by shipped
environments; commands that require a *connected* port declare
``needs=CapabilitySet(serial_connected=True)`` and the dispatcher
gates them at call time.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Generator


@dataclass
class SerialHandle:
    """Serial I/O: TX, RX, observers, claim/release, port introspection."""

    # ── Connection state ─────────────────────────────────────────────
    is_connected: Callable = lambda: False
    port: Callable = lambda: None  # -> serial.Serial | None

    # ── Raw I/O ──────────────────────────────────────────────────────
    write: Callable = lambda data: None
    send: Callable = lambda text: None
    read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    drain: Callable = lambda: 0
    wait_for_data: Callable = lambda timeout_ms=250: False
    wait_idle: Callable = lambda timeout_ms=400: None

    # ── Claim/release primitives ─────────────────────────────────────
    # Prefer the ``io()`` context manager below; these are exposed for
    # the rare case where a handler needs to span multiple call sites.
    claim: Callable = lambda: None
    release: Callable = lambda: None

    # ── Observer register/unregister (private; use ctx managers) ─────
    _add_rx_observer: Callable = lambda cb: None
    _remove_rx_observer: Callable = lambda cb: None
    _add_tx_observer: Callable = lambda cb: None
    _remove_tx_observer: Callable = lambda cb: None

    # ── Context managers (the public path for synchronous I/O) ───────

    @contextmanager
    def io(self) -> Generator[None, None, None]:
        """Claim the port for the duration of a synchronous read cycle.

        Releases on every exit path including exceptions.  Use this for
        any drain -> write -> read sequence::

            with ctx.serial.io():
                ctx.serial.drain()
                ctx.serial.send(cmd)
                response = ctx.serial.read_raw()

        This is the only public path -- the bare ``claim``/``release``
        primitives exist on the handle for completeness but should not
        be used in new code.
        """
        self.claim()
        try:
            yield
        finally:
            self.release()

    @contextmanager
    def rx_observer(
        self, cb: Callable[[bytes], None],
    ) -> Generator[None, None, None]:
        """Register an RX byte observer for the duration of the block.

        Observers see every raw RX byte chunk alongside the normal
        line-decoding pipeline -- they cannot modify or block it.
        Callbacks fire on the reader background thread; keep them fast.

        Released on every exit path including exceptions.  This is the
        only public path -- the bare register/unregister methods are
        intentionally underscore-prefixed so leaks are structurally
        impossible from plugin code.
        """
        self._add_rx_observer(cb)
        try:
            yield
        finally:
            self._remove_rx_observer(cb)

    @contextmanager
    def tx_observer(
        self, cb: Callable[[bytes], None],
    ) -> Generator[None, None, None]:
        """Register a TX byte observer for the duration of the block.

        Observers see every raw TX byte chunk alongside the normal
        write path -- they cannot modify or block it.  Callbacks fire
        on the calling thread.

        Released on every exit path including exceptions.  Pairs with
        :meth:`rx_observer` for full traffic taps.
        """
        self._add_tx_observer(cb)
        try:
            yield
        finally:
            self._remove_tx_observer(cb)
