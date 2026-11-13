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
``needs=CapabilitySet.SERIAL_CONNECTED`` and the dispatcher
gates them at call time.
"""

from __future__ import annotations

import codecs
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from termapy.defaults import cfg_value


@dataclass
class SerialHandle:
    """Serial I/O: TX, RX, observers, claim/release.

    The raw pyserial object is deliberately NOT exposed here -- it lives on
    ``ctx.internal.port`` (the explicitly-unstable, built-ins-only escape
    hatch) so this stable handle stays free of pyserial coupling.
    """

    # ── Connection state ─────────────────────────────────────────────
    is_connected: Callable = lambda: False

    # ── Connection lifecycle / port control ──────────────────────────
    # Host-orchestrated: connect/disconnect fire lifecycle hooks and
    # update the frontend, update_port rewrites the port name, and
    # apply_port_effects pushes serial settings (baud, DTR/RTS, ...) to
    # the live port.  The host wires these; defaults are no-ops.
    # connect(port=None) opens the port, or raises an OSError
    # (SerialException) whose message is why it did not open; the caller
    # reports it.  A port that is already open is not a failure.
    connect: Callable = lambda port=None: None
    disconnect: Callable = lambda: None
    update_port: Callable = lambda name: None
    apply_port_effects: Callable = lambda effects: None

    # ── Raw I/O ──────────────────────────────────────────────────────
    # write(data) sends bytes as given: the binary path.  A text line goes
    # through send() below, which is built on write.
    write: Callable = lambda data: None
    read_raw: Callable = lambda timeout_ms=1000, frame_gap_ms=0: b""
    drain: Callable = lambda: 0
    wait_for_data: Callable = lambda timeout_ms=250: False
    # wait_idle(timeout_ms, max_wait_s): return once the device has been
    # quiet for timeout_ms after the call, or after max_wait_s at most.
    wait_idle: Callable = lambda timeout_ms=400, max_wait_s=3.0: None
    # byte_counts() -> (tx, rx): bytes sent and received since the port last
    # opened, what /term.info and the TUI's /term.traffic display show.
    byte_counts: Callable[[], tuple[int, int]] = lambda: (0, 0)
    # queue.Queue[bytes] - raw RX for protocol handlers.  Bounded: when full
    # the reader drops the oldest chunk, so drain() it inside io() and then
    # read as bytes arrive rather than letting it fill.
    rx_queue: Any = None

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
    # The live config send() reads (eol, encoding, the transmit delays);
    # PluginContext wires it to its own cfg.
    _cfg: Callable[[], Mapping[str, Any]] = lambda: {}

    def send(self, text: str) -> None:
        """Send ``text`` as one device line: the line ending added, encoded, paced.

        The one way a text line goes out (registry ``tx-text-one-path``).
        Binary -- a transfer, the protocol subsystem's packets and setup
        lines, ``/raw`` -- goes straight to ``write`` and is never paced.

        Pacing is TeraTerm's transmit delay, both off by default: with
        ``tx_char_delay_ms`` each character is written on its own and
        followed by that wait, and ``tx_line_delay_ms`` waits after the line
        ending.  For a device that drops bytes arriving back to back.  The
        wait blocks the calling thread, a worker wherever a command runs.

        The whole line is encoded first, so a character the encoding cannot
        take fails the send before any of it goes out.

        Args:
            text: The line without its ending (``""`` sends the ending alone).

        Raises:
            OSError: What ``write`` raises; pyserial's ``SerialException``
                is one.
            UnicodeError: The configured encoding cannot take the text.
            LookupError: The configured encoding does not exist.
        """
        cfg = self._cfg()
        line = text + cfg_value(cfg, "eol")
        encoding = cfg_value(cfg, "encoding")
        data = line.encode(encoding)
        char_delay_s = cfg_value(cfg, "tx_char_delay_ms") / 1000
        line_delay_s = cfg_value(cfg, "tx_line_delay_ms") / 1000
        if char_delay_s <= 0:
            self.write(data)
        else:
            # Incremental, so a multi-byte character goes out whole and a
            # stateful encoding writes its preamble once, not per character.
            encoder = codecs.getincrementalencoder(encoding)()
            for char in line:
                chunk = encoder.encode(char)
                if chunk:
                    self.write(chunk)
                    time.sleep(char_delay_s)
            tail = encoder.encode("", final=True)
            if tail:
                self.write(tail)
        if line_delay_s > 0:
            time.sleep(line_delay_s)

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
        self,
        cb: Callable[[bytes], None],
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
        self,
        cb: Callable[[bytes], None],
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
