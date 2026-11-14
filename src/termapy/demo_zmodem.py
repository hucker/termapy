"""The demo device's ZMODEM peer: the real engine, stepped synchronously.

The demo's XMODEM and YMODEM responders are hand-written byte-level state
machines driven one host write at a time, inside the FakeSerial lock.  ZMODEM
is more involved (ZDLE framing, hex and binary headers, CRC-32 subpackets,
ZRPOS resume), so rather than reimplement it the demo runs the same
``yesterwind-xyzmodem`` engine the real device would.  The engine is async,
but it is driven the same way X/Y are: a private event loop steps the
transfer coroutine until it next blocks waiting for a host byte, then returns
control (``zmodem_transport.StepDriver``).  No second thread, so no race at
the claim/release seam; a transfer advances only when the host writes,
exactly like ``_process_xmodem``.

This gives ``termapy --demo`` a genuine ZMODEM peer: ``/xfer.zmodem.send`` and
``/xfer.zmodem.recv`` work against it as they would against a Linux box
running ``rz`` / ``sz``.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.zmodem_transport import StepDriver, step_receive, step_send

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping


class DemoZmodemPeer:
    """One ZMODEM transfer the demo device runs, stepped by the host's writes.

    ``feed`` advances the transfer with the host's latest bytes and returns
    whatever the device sends back; it runs the engine until it is waiting for
    more host input or the transfer is done.  ``active`` is true until then,
    so the FakeSerial routes protocol bytes here and refuses a second
    transfer.  There is no thread: the whole peer lives on the calling
    (FakeSerial write) path, under its lock.
    """

    def __init__(self, vfs: MutableMapping[str, bytes]) -> None:
        self._vfs = vfs
        self._folder = Path(tempfile.mkdtemp())
        self._driver: StepDriver | None = None

    @property
    def active(self) -> bool:
        """Whether a transfer is under way (started and not yet finished)."""
        return self._driver is not None and not self._driver.done

    def start_send(self, names: list[str]) -> None:
        """Begin ``rz``'s peer: send ``names`` from the device VFS to the host."""
        paths = []
        for name in names:
            path = self._folder / Path(name).name
            path.write_bytes(self._vfs.get(name, b""))
            paths.append(path)
        transport, coro = step_send(paths)
        self._driver = StepDriver(transport, coro)

    def start_recv(self) -> None:
        """Begin ``sz``'s peer: receive the host's files into the device VFS."""
        transport, coro = step_receive(self._folder, self._store)
        self._driver = StepDriver(transport, coro)

    def feed(self, host_bytes: bytes) -> bytes:
        """Advance the transfer with ``host_bytes``; return the device's reply bytes."""
        if self._driver is None:
            return b""
        return self._driver.feed(host_bytes)

    def close(self) -> None:
        """End the transfer and join the worker (the port is closing)."""
        if self._driver is not None:
            self._driver.cancel()
            self._driver = None

    def _store(self, name: str, data: bytes) -> None:
        self._vfs[name] = data


def available_names(vfs: Mapping[str, bytes], names: list[str]) -> list[str] | None:
    """The VFS names a send would use, or None if one is missing.

    With no names given, the whole VFS, sorted; each named file must exist.
    """
    if not names:
        return sorted(vfs)
    return names if all(name in vfs for name in names) else None
