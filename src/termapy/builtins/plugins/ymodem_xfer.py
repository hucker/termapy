"""Built-in plugin: YMODEM file transfer over serial."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ymodem.Socket import ModemSocket
from ymodem.Protocol import ProtocolType

from termapy.plugins import CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

from termapy.builtins.plugins.xmodem_xfer import (
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

    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    paths: list[str] = []
    for filename in filenames:
        path = _resolve_path(filename, _get_xfer_root(ctx))
        if not path.is_file():
            return CmdResult.fail(msg=f"File not found: {path}")
        paths.append(str(path))

    total_size = sum(Path(p).stat().st_size for p in paths)
    ctx.write(f"  YMODEM send: {len(paths)} file(s), {total_size} bytes -- Esc to cancel")

    cancel = ctx.engine.xfer_cancel
    if cancel:
        cancel.clear()
    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    try:
        reader = QueueByteReader(ctx.engine.rx_queue, cancel=cancel)

        def read(size: int, timeout: float | None = None) -> bytes:
            result = reader.getc(size, timeout=timeout or 1)
            return result if result else b""

        def write(data: bytes | bytearray, timeout: float | None = None) -> int:
            ctx.serial_write(bytes(data))
            return len(data)

        def progress(task_index: int, name: str, sent: int, total: int) -> None:
            pct = (sent * 100 // total) if total else 0
            ctx.status(f"  YMODEM: {name} {pct}% ({sent}/{total} bytes)")

        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)
        ok = modem.send(paths, callback=progress)

        if cancel and cancel.is_set():
            return CmdResult.fail(msg="YMODEM send cancelled.")
        if ok:
            names = ", ".join(Path(p).name for p in paths)
            ctx.result(f"YMODEM send complete: {names} ({total_size} bytes)")
            return CmdResult.ok(value=names)
        return CmdResult.fail(msg="YMODEM send failed.")
    finally:
        ctx.engine.set_proto_active(False)


def _handler_recv(ctx: PluginContext, args: str) -> CmdResult:
    """Receive file(s) from the device via YMODEM.

    YMODEM batch mode: the sender provides filenames. Files are saved
    to the cap/ directory (or to a specified directory).

    Args:
        ctx: Plugin context.
        args: Optional directory to save to (defaults to cap/).
    """
    target_dir = args.strip() if args.strip() else ""

    if not ctx.is_connected():
        return CmdResult.fail(msg="Not connected.")

    if target_dir:
        out_dir = _resolve_path(target_dir, _get_xfer_root(ctx))
    else:
        out_dir = _get_xfer_root(ctx)

    if not out_dir.is_dir():
        return CmdResult.fail(msg=f"Directory not found: {out_dir}")

    ctx.write(f"  YMODEM recv: waiting for data -> {out_dir} -- Esc to cancel")

    cancel = ctx.engine.xfer_cancel
    if cancel:
        cancel.clear()
    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    try:
        reader = QueueByteReader(ctx.engine.rx_queue, cancel=cancel)

        def read(size: int, timeout: float | None = None) -> bytes:
            result = reader.getc(size, timeout=timeout or 1)
            return result if result else b""

        def write(data: bytes | bytearray, timeout: float | None = None) -> int:
            ctx.serial_write(bytes(data))
            return len(data)

        def progress(task_index: int, name: str, received: int, total: int) -> None:
            pct = (received * 100 // total) if total else 0
            ctx.status(f"  YMODEM: {name} {pct}% ({received}/{total} bytes)")

        modem = ModemSocket(read, write, protocol_type=ProtocolType.YMODEM)
        ok = modem.recv(str(out_dir), callback=progress)

        if cancel and cancel.is_set():
            return CmdResult.fail(msg="YMODEM recv cancelled.")
        if ok:
            ctx.result(f"YMODEM recv complete -> {out_dir}")
            return CmdResult.ok(value=str(out_dir))
        return CmdResult.fail(msg="YMODEM recv failed.")
    finally:
        ctx.engine.set_proto_active(False)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="ymodem",
    help="YMODEM file transfer (batch, 1K blocks).",
    handler=None,
    sub_commands={
        "send": Command(
            args="<file> {file2} ...",
            help="Send file(s) via YMODEM to the device.",
            handler=_handler_send,
        ),
        "recv": Command(
            args="{directory}",
            help="Receive file(s) via YMODEM from the device.",
            handler=_handler_recv,
        ),
    },
)
