"""XMODEM file-transfer handlers (private; mounted under /xfer).

Filename is underscore-prefixed so the plugin loader skips it --
the actual command tree (``/xfer.xmodem.send`` / ``recv``, plus the
hidden ``/xmodem`` legacy forwarders) is wired up in ``xfer.py``
and ``xmodem.py``.  This module exists only as a home for the
handlers and the ``QueueByteReader`` shared with ymodem.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, UsageError
from termapy.scripting import resolve_seq_filename
from termapy.vendor.xmodem import XMODEM

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


class QueueByteReader:
    """Adapt termapy's chunk-based rx_queue to xmodem's byte-level getc.

    The serial reader thread continuously feeds chunks into rx_queue.
    XMODEM calls getc(size) expecting exactly *size* bytes or None on
    timeout. This class bridges the two with an internal buffer.

    Args:
        rx_queue: The raw RX byte queue from SerialEngine.
        cancel: Optional threading.Event - when set, getc returns None
            immediately to abort the transfer.
    """

    def __init__(self, rx_queue: queue.Queue[bytes], cancel=None) -> None:
        self._queue = rx_queue
        self._buf = bytearray()
        self._cancel = cancel

    def getc(self, size: int, timeout: float = 1) -> bytes | None:
        """Read exactly *size* bytes, or None on timeout/cancel.

        Args:
            size: Number of bytes to read.
            timeout: Timeout in seconds.  Fractional values are supported
                -- ymodem passes sub-second timeouts through this path.

        Returns:
            Exactly *size* bytes, or None if timeout expires or canceled.
        """
        deadline = time.monotonic() + timeout
        while len(self._buf) < size:
            if self._cancel and self._cancel.is_set():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                self._buf.extend(
                    self._queue.get(timeout=min(remaining, 0.05))
                )
            except queue.Empty:
                continue
        result = bytes(self._buf[:size])
        self._buf = self._buf[size:]
        return result


def _get_xfer_root(ctx: PluginContext) -> Path:
    """Return the file transfer root directory from config, or cap_dir.

    Args:
        ctx: Plugin context.

    Returns:
        Resolved directory path for file transfer operations.
    """
    root = ctx.cfg.get("file_xfer_root", "")
    if root:
        return Path(root).resolve()
    return ctx.fs.cap_dir


def _resolve_path(filename: str, root_dir: Path) -> Path:
    """Resolve a filename against a root directory.

    Args:
        filename: Filename or absolute path.
        root_dir: Default directory for relative paths.

    Returns:
        Resolved absolute path.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = root_dir / filename
    return path.resolve()


def _handler_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send a file to the device via XMODEM.

    Args:
        ctx: Plugin context.
        args: Filename to send.
    """
    filename = args.strip()
    if not filename:
        raise UsageError()

    path = _resolve_path(filename, _get_xfer_root(ctx))
    if not path.is_file():
        return CmdResult.fail(msg=f"File not found: {path}")

    file_size = path.stat().st_size
    ctx.io.output(f"  XMODEM send: {path.name} ({file_size} bytes) -- Esc to cancel")

    cancel = ctx.internal.xfer_cancel
    if cancel:
        cancel.clear()
    with ctx.serial.io():
        ctx.serial.drain()
        reader = QueueByteReader(ctx.serial.rx_queue, cancel=cancel)
        modem = XMODEM(reader.getc, lambda data, timeout=1: ctx.serial.write(data) or len(data))

        _last = [0]

        def _progress(total: int, success: int, error: int, pkt_size: int = 128) -> None:
            if success != _last[0]:
                _last[0] = success
                ctx.io.status(f"  XMODEM: {success} packets ({success * pkt_size} bytes) sent, {error} errors")

        with open(path, "rb") as f:
            ok = modem.send(f, callback=_progress)

        if cancel and cancel.is_set():
            return CmdResult.fail(msg="XMODEM send canceled.")
        if ok:
            ctx.io.result(f"XMODEM send complete: {path} ({file_size} bytes)")
            return CmdResult.ok(value=path)
        return CmdResult.fail(msg="XMODEM send failed.")


def _handler_recv(ctx: PluginContext, args: str) -> CmdResult:
    """Receive a file from the device via XMODEM.

    Args:
        ctx: Plugin context.
        args: Filename to save to.
    """
    filename = args.strip()
    if not filename:
        raise UsageError()

    try:
        filename = resolve_seq_filename(filename, _get_xfer_root(ctx))
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    path = _resolve_path(filename, _get_xfer_root(ctx))
    ctx.io.output(f"  XMODEM recv: waiting for data -> {path} -- Esc to cancel")

    cancel = ctx.internal.xfer_cancel
    if cancel:
        cancel.clear()
    with ctx.serial.io():
        ctx.serial.drain()
        reader = QueueByteReader(ctx.serial.rx_queue, cancel=cancel)
        modem = XMODEM(reader.getc, lambda data, timeout=1: ctx.serial.write(data) or len(data))

        _last = [0]

        def _progress(total: int, success: int, error: int, pkt_size: int = 128) -> None:
            if success != _last[0]:
                _last[0] = success
                ctx.io.status(f"  XMODEM: {success} packets ({success * pkt_size} bytes) received, {error} errors")

        with open(path, "wb") as f:
            ok = modem.recv(f, callback=_progress)

        # Strip trailing 0x1A padding (standard XMODEM EOF fill)
        if ok and path.exists():
            data = path.read_bytes()
            stripped = data.rstrip(b"\x1a")
            if len(stripped) < len(data):
                path.write_bytes(stripped)

        if cancel and cancel.is_set():
            if path.exists():
                path.unlink(missing_ok=True)
            return CmdResult.fail(msg="XMODEM recv canceled.")
        if ok:
            size = path.stat().st_size
            ctx.io.result(f"XMODEM recv complete: {path} ({size} bytes)")
            return CmdResult.ok(value=path)
        # Clean up empty file on failure
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
        return CmdResult.fail(msg="XMODEM recv failed.")


# Public surface re-exported for xfer.py (the new home of the /xfer.xmodem
# command tree).  No ``COMMAND`` -- this file is underscore-prefixed so the
# plugin loader skips it.
__all__ = [
    "QueueByteReader",
    "_get_xfer_root",
    "_resolve_path",
    "_handler_send",
    "_handler_recv",
]
