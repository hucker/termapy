"""YMODEM file-transfer handlers (private; mounted under /xfer).

Sibling of ``_xmodem_handlers``; both are mounted by ``xfer.py``
into the ``/xfer.{xmodem,ymodem}.{send,recv}`` tree.  Hidden
``/ymodem`` forwarders live in ``ymodem.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.vendor.ymodem.Socket import ModemSocket
from termapy.vendor.ymodem.Protocol import ProtocolType

from termapy.plugins import CmdResult

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

from termapy.builtins.commands._xmodem_handlers import (
    QueueByteReader,
    _get_xfer_root,
    _resolve_path,
)


def _handler_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send file(s) to the device via YMODEM.

    Args:
        ctx: Plugin context.
        args: One or more filenames to send.
    """
    filenames = args.strip().split()
    if not filenames:
        return CmdResult.fail(msg="Usage: /ymodem.send <file> {file2} ...")

    paths: list[str] = []
    for filename in filenames:
        path = _resolve_path(filename, _get_xfer_root(ctx))
        if not path.is_file():
            return CmdResult.fail(msg=f"File not found: {path}")
        paths.append(str(path))

    total_size = sum(Path(p).stat().st_size for p in paths)
    ctx.io.output(f"  YMODEM send: {len(paths)} file(s), {total_size} bytes -- Esc to cancel")

    cancel = ctx.internal.xfer_cancel
    if cancel:
        cancel.clear()
    with ctx.serial.io():
        ctx.serial.drain()
        reader = QueueByteReader(ctx.serial.rx_queue, cancel=cancel)

        def read(size: int, timeout: float | None = None) -> bytes:
            result = reader.getc(size, timeout=timeout or 1)
            return result if result else b""

        def write(data: bytes | bytearray, timeout: float | None = None) -> int:
            ctx.serial.write(bytes(data))
            return len(data)

        def progress(task_index: int, name: str, sent: int, total: int) -> None:
            pct = (sent * 100 // total) if total else 0
            ctx.io.status(f"  YMODEM: {name} {pct}% ({sent}/{total} bytes)")

        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)
        ok = modem.send(paths, callback=progress)

        if cancel and cancel.is_set():
            return CmdResult.fail(msg="YMODEM send canceled.")
        if ok:
            names = ", ".join(Path(p).name for p in paths)
            ctx.io.result(f"YMODEM send complete: {names} ({total_size} bytes)")
            return CmdResult.ok(value=names)
        return CmdResult.fail(msg="YMODEM send failed.")


def _handler_recv(ctx: PluginContext, args: str) -> CmdResult:
    """Receive file(s) from the device via YMODEM.

    YMODEM batch mode: the sender provides filenames. Files are saved
    to the cap/ directory (or to a specified directory).

    Args:
        ctx: Plugin context.
        args: Optional directory to save to (defaults to cap/).
    """
    target_dir = args.strip() if args.strip() else ""

    if target_dir:
        out_dir = _resolve_path(target_dir, _get_xfer_root(ctx))
    else:
        out_dir = _get_xfer_root(ctx)

    if not out_dir.is_dir():
        return CmdResult.fail(msg=f"Directory not found: {out_dir}")

    ctx.io.output(f"  YMODEM recv: waiting for data -> {out_dir} -- Esc to cancel")

    cancel = ctx.internal.xfer_cancel
    if cancel:
        cancel.clear()
    with ctx.serial.io():
        ctx.serial.drain()
        reader = QueueByteReader(ctx.serial.rx_queue, cancel=cancel)

        def read(size: int, timeout: float | None = None) -> bytes:
            result = reader.getc(size, timeout=timeout or 1)
            return result if result else b""

        def write(data: bytes | bytearray, timeout: float | None = None) -> int:
            ctx.serial.write(bytes(data))
            return len(data)

        def progress(task_index: int, name: str, received: int, total: int) -> None:
            pct = (received * 100 // total) if total else 0
            ctx.io.status(f"  YMODEM: {name} {pct}% ({received}/{total} bytes)")

        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)
        ok = modem.recv(str(out_dir), callback=progress)

        if cancel and cancel.is_set():
            return CmdResult.fail(msg="YMODEM recv canceled.")
        if ok:
            ctx.io.result(f"YMODEM recv complete -> {out_dir}")
            return CmdResult.ok(value=str(out_dir))
        return CmdResult.fail(msg="YMODEM recv failed.")


# No ``COMMAND`` -- this file is underscore-prefixed so the plugin
# loader skips it.  ``xfer.py`` imports ``_handler_send`` and
# ``_handler_recv`` to mount them under ``/xfer.ymodem``.
__all__ = ["_handler_send", "_handler_recv"]
