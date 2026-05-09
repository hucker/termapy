"""SerialHandle -- all serial I/O operations.

Reachable as ``ctx.serial.*``.  Domain: reading and writing to the
serial port, observing TX/RX bytes, claiming exclusive access for
synchronous read cycles.

Field names map cleanly from the flat API:

  - ``ctx.serial_write``     -> ``ctx.serial.write``
  - ``ctx.serial_send``      -> ``ctx.serial.send``
  - ``ctx.serial_read_raw``  -> ``ctx.serial.read_raw``
  - ``ctx.serial_drain``     -> ``ctx.serial.drain``
  - ``ctx.serial_io()``      -> ``ctx.serial.io()``  (context manager)
  - ``ctx.rx_observer(cb)``  -> ``ctx.serial.rx_observer(cb)``  (context manager)
  - ``ctx.tx_observer(cb)``  -> ``ctx.serial.tx_observer(cb)``  (context manager)
  - ``ctx.is_connected()``   -> ``ctx.serial.is_connected()``
  - ``ctx.port()``           -> ``ctx.serial.port()``

This handle is **not** capability-gated at the method level.  The
``serial_io`` baseline capability is always provided by shipped
environments; commands that require a *connected* port declare
``needs=CapabilitySet(serial_connected=True)`` and the dispatcher
gates them at call time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, ContextManager

if TYPE_CHECKING:
    from termapy.plugins.context import PluginContext


class SerialHandle:
    """Serial I/O: TX, RX, observers, claim/release, port introspection."""

    def __init__(self, ctx: "PluginContext") -> None:
        self._ctx = ctx

    # ── Connection state ─────────────────────────────────────────────

    def is_connected(self) -> bool:
        """True when the serial port is currently open."""
        return self._ctx.is_connected()

    def port(self):
        """The pyserial ``Serial`` object, or ``None`` when disconnected."""
        return self._ctx.port()

    # ── Raw I/O ──────────────────────────────────────────────────────

    def write(self, data: bytes) -> None:
        """Send raw bytes.  No line ending is appended; exact bytes go on the wire."""
        self._ctx.serial_write(data)

    def send(self, text: str) -> None:
        """Send text with the configured line ending and encoding."""
        self._ctx.serial_send(text)

    def read_raw(self, timeout_ms: int = 1000, frame_gap_ms: int = 0) -> bytes:
        """Read bytes with timeout-based framing.

        Returns a complete frame (possibly empty on timeout).  Use inside
        :meth:`io` to ensure the bytes are queued for synchronous read
        rather than fed to the line pipeline.
        """
        return self._ctx.serial_read_raw(timeout_ms=timeout_ms, frame_gap_ms=frame_gap_ms)

    def drain(self) -> int:
        """Discard all pending RX bytes; returns count discarded."""
        return self._ctx.serial_drain()

    def wait_for_data(self, timeout_ms: int = 250) -> bool:
        """Block until first RX byte arrives or timeout elapses.  Returns True on data."""
        return self._ctx.serial_wait_for_data(timeout_ms=timeout_ms)

    def wait_idle(self, timeout_ms: int = 400) -> None:
        """Block until the RX stream has been quiet for ``timeout_ms``."""
        self._ctx.serial_wait_idle(timeout_ms=timeout_ms)

    # ── Claim/release primitives ─────────────────────────────────────
    # Prefer the ``io()`` context manager below; these are exposed for
    # the rare case where a handler needs to span multiple call sites.

    def claim(self) -> None:
        """Claim the serial port for synchronous read.  Prefer :meth:`io` instead."""
        self._ctx.serial_claim()

    def release(self) -> None:
        """Release the serial port back to the line pipeline.  Prefer :meth:`io` instead."""
        self._ctx.serial_release()

    # ── Context managers (the public path for synchronous I/O) ───────

    def io(self) -> ContextManager[None]:
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
        return self._ctx.serial_io()

    def rx_observer(self, cb: Callable[[bytes], None]) -> ContextManager[None]:
        """Register an RX byte observer for the duration of the block.

        Observers see every raw RX byte chunk alongside the normal
        line-decoding pipeline -- they cannot modify or block it.
        Callbacks fire on the reader background thread; keep them fast.

        Released on every exit path including exceptions.  This is the
        only public path -- bare register/unregister methods are
        intentionally unreachable from plugin code.
        """
        return self._ctx.rx_observer(cb)

    def tx_observer(self, cb: Callable[[bytes], None]) -> ContextManager[None]:
        """Register a TX byte observer for the duration of the block.

        Observers see every raw TX byte chunk alongside the normal
        write path -- they cannot modify or block it.  Callbacks fire
        on the calling thread.

        Released on every exit path including exceptions.  Pairs with
        :meth:`rx_observer` for full traffic taps.
        """
        return self._ctx.tx_observer(cb)
