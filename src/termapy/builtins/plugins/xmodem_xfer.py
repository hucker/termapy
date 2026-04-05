"""Built-in plugin: XMODEM file transfer over serial."""

from __future__ import annotations

import queue
import time
from pathlib import Path
from typing import TYPE_CHECKING

from xmodem import XMODEM

from termapy.plugins import CmdResult, Command
from termapy.scripting import resolve_seq_filename

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


class QueueByteReader:
    """Adapt termapy's chunk-based rx_queue to xmodem's byte-level getc.

    The serial reader thread continuously feeds chunks into rx_queue.
    XMODEM calls getc(size) expecting exactly *size* bytes or None on
    timeout. This class bridges the two with an internal buffer.

    Args:
        rx_queue: The raw RX byte queue from SerialEngine.
    """

    def __init__(self, rx_queue: queue.Queue[bytes]) -> None:
        self._queue = rx_queue
        self._buf = bytearray()

    def getc(self, size: int, timeout: int = 1) -> bytes | None:
        """Read exactly *size* bytes, or None on timeout.

        Args:
            size: Number of bytes to read.
            timeout: Timeout in seconds.

        Returns:
            Exactly *size* bytes, or None if timeout expires.
        """
        deadline = time.monotonic() + timeout
        while len(self._buf) < size:
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


def _resolve_path(filename: str, cap_dir: Path) -> Path:
    """Resolve a filename against the capture directory.

    Args:
        filename: Filename or absolute path.
        cap_dir: Default directory for relative paths.

    Returns:
        Resolved absolute path.
    """
    path = Path(filename)
    if not path.is_absolute():
        path = cap_dir / filename
    return path.resolve()


def _handler_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send a file to the device via XMODEM.

    Args:
        ctx: Plugin context.
        args: Filename to send.
    """
    filename = args.strip()
    if not filename:
        return CmdResult.fail(msg="Usage: /xmodem.send <file>")

    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    path = _resolve_path(filename, ctx.cap_dir)
    if not path.is_file():
        return CmdResult.fail(msg=f"File not found: {path}")

    file_size = path.stat().st_size
    ctx.write(f"  XMODEM send: {path.name} ({file_size} bytes)")

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    try:
        reader = QueueByteReader(ctx.engine.rx_queue)
        modem = XMODEM(reader.getc, lambda data, timeout=1: ctx.serial_write(data) or len(data))

        def _progress(total: int, success: int, error: int) -> None:
            ctx.status(f"  XMODEM: {success} packets sent, {error} errors")

        with open(path, "rb") as f:
            ok = modem.send(f, callback=_progress)

        if ok:
            ctx.result(f"XMODEM send complete: {path.name} ({file_size} bytes)")
            return CmdResult.ok(value=str(path))
        return CmdResult.fail(msg="XMODEM send failed.")
    finally:
        ctx.engine.set_proto_active(False)


def _handler_recv(ctx: PluginContext, args: str) -> CmdResult:
    """Receive a file from the device via XMODEM.

    Args:
        ctx: Plugin context.
        args: Filename to save to.
    """
    filename = args.strip()
    if not filename:
        return CmdResult.fail(msg="Usage: /xmodem.recv <file>")

    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    try:
        filename = resolve_seq_filename(filename, ctx.cap_dir)
    except ValueError as e:
        return CmdResult.fail(msg=str(e))

    path = _resolve_path(filename, ctx.cap_dir)
    ctx.write(f"  XMODEM recv: waiting for data -> {path.name}")

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    try:
        reader = QueueByteReader(ctx.engine.rx_queue)
        modem = XMODEM(reader.getc, lambda data, timeout=1: ctx.serial_write(data) or len(data))

        def _progress(total: int, success: int, error: int) -> None:
            ctx.status(f"  XMODEM: {success} packets received, {error} errors")

        with open(path, "wb") as f:
            ok = modem.recv(f, callback=_progress)

        if ok:
            size = path.stat().st_size
            ctx.result(f"XMODEM recv complete: {path.name} ({size} bytes)")
            return CmdResult.ok(value=str(path))
        # Clean up empty file on failure
        if path.exists() and path.stat().st_size == 0:
            path.unlink()
        return CmdResult.fail(msg="XMODEM recv failed.")
    finally:
        ctx.engine.set_proto_active(False)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="xmodem",
    help="XMODEM file transfer.",
    handler=None,
    sub_commands={
        "send": Command(
            args="<file>",
            help="Send a file via XMODEM to the device.",
            handler=_handler_send,
        ),
        "recv": Command(
            args="<file>",
            help="Receive a file via XMODEM from the device.",
            handler=_handler_recv,
        ),
    },
)
