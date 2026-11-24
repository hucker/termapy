"""ZMODEM file-transfer handlers (private; mounted under /xfer).

Sibling of ``_xmodem_handlers`` and ``_ymodem_handlers``, mounted by
``xfer.py`` as ``/xfer.zmodem.{send,recv}``.  The engine, its byte transports
and the transfer cores are core (``termapy.zmodem_transport``), because the
demo device runs the same engine; this module is only the CLI surface: the
status line, the error wording, and the two handlers.  The engine
(yesterwind-xyzmodem, which brings asyncio with it) and the transport over
it are imported where a transfer runs, never at module level: this body runs
at every start of every frontend, and a one-shot that prints a line has no
use for them (registry ``flag-paths-light``).
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._xmodem_handlers import (
    QueueByteReader,
    _get_xfer_root,
    _resolve_path,
)
from termapy.folder_ops import staged_receive
from termapy.folders import ensure_folder, file_suggestion
from termapy.plugins import CmdResult, UsageError

if TYPE_CHECKING:
    from yesterwind_xyzmodem.callbacks import TransferProgress

    from termapy.plugins import PluginContext
    from termapy.zmodem_transport import QueueTransport


class _StatusLine:
    """The one status line a transfer shows, rewritten once per percent.

    The engine fires it per 1 KB subpacket and propagates anything it
    raises, so it only formats and writes.
    """

    def __init__(self, ctx: PluginContext) -> None:
        from yesterwind_xyzmodem.callbacks import EventType

        self._ctx = ctx
        self._last: tuple[str, int] | None = None
        self._file_start = EventType.FILE_START
        self._progress_events = (
            EventType.BLOCK_SENT,
            EventType.BLOCK_RECEIVED,
            EventType.FILE_END,
        )

    def __call__(self, progress: TransferProgress) -> None:
        name = Path(progress.filename).name
        if progress.event is self._file_start:
            self._last = None
            self._ctx.io.status(f"  ZMODEM: {name} ({progress.total_bytes} bytes)")
        elif progress.event in self._progress_events:
            done, total = progress.bytes_transferred, progress.total_bytes
            pct = done * 100 // total if total else 0
            if (name, pct) != self._last:
                self._last = (name, pct)
                text = f"{pct}% ({done}/{total} bytes)" if total else f"{done} bytes"
                self._ctx.io.status(f"  ZMODEM: {name} {text}")


def _failure(ctx: PluginContext, verb: str, error: Exception) -> CmdResult:
    """The failure a transfer ended in, after telling the device to stop.

    A remote cancel needs no reply: the device has already stopped.
    Everything else sends ``ZMODEM_CANCEL`` so rz or sz on the device
    returns to its prompt instead of waiting out its own timeout.
    """
    from yesterwind_xyzmodem.exceptions import TransferCancelled

    from termapy.zmodem_transport import ZMODEM_CANCEL, TransferAborted

    if isinstance(error, TransferCancelled):
        return CmdResult.fail(msg=f"ZMODEM {verb} canceled by the device.")
    with contextlib.suppress(OSError):
        ctx.serial.write(ZMODEM_CANCEL)
    if isinstance(error, TransferAborted):
        return CmdResult.fail(msg=f"ZMODEM {verb} canceled.")
    if isinstance(error, OSError):
        subsystem = "Read" if verb == "send" else "Write"
        return CmdResult.fail(msg=f"{subsystem} error: {error}")
    return CmdResult.fail(msg=f"ZMODEM error: {error}")


def _transport(ctx: PluginContext) -> QueueTransport:
    """The transport for one transfer on the claimed port, its cancel event cleared."""
    from termapy.zmodem_transport import QueueTransport

    cancel = ctx.internal.xfer_cancel
    if cancel:
        cancel.clear()
    ctx.serial.drain()
    reader = QueueByteReader(ctx.serial.rx_queue, cancel=cancel)
    return QueueTransport(reader, ctx.serial.write, cancel)


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).
#
# Hand-rolled like their siblings: the variadic file list follows
# ymodem.send's holdout and the family migrates to params together (CLAUDE.md
# "Declarative Command Parameters"); the typed MCP schema has no value for a
# command MCP never sees.


def _handler_send(ctx: PluginContext, args: str) -> CmdResult:
    """Send file(s) to the device via ZMODEM; the device runs ``rz`` first.

    Args:
        ctx: Plugin context.
        args: One or more file names, under the transfer root unless absolute.
    """
    from yesterwind_xyzmodem.exceptions import XYZModemError

    from termapy.zmodem_transport import TransferAborted, send_files

    filenames = args.split()
    if not filenames:
        raise UsageError()

    paths: list[Path] = []
    for filename in filenames:
        path = _resolve_path(filename, _get_xfer_root(ctx))
        if not path.is_file():
            hint = file_suggestion(path.parent, filename)
            return CmdResult.fail(msg=f"File not found: {path}{hint}")
        paths.append(path)

    total_size = sum(path.stat().st_size for path in paths)
    ctx.io.output(f"  ZMODEM send: {len(paths)} file(s), {total_size} bytes -- Esc to cancel")

    with ctx.serial.io():
        transport = _transport(ctx)
        try:
            sent = send_files(transport, paths, _StatusLine(ctx))
        except (TransferAborted, XYZModemError, TimeoutError, OSError) as e:
            return _failure(ctx, "send", e)
        # The receiver answers our ZFIN with its own after we have stopped
        # reading; wait for the line to fall quiet, then discard that tail so
        # no leftover header reaches the terminal as a stray command.
        ctx.serial.wait_idle(timeout_ms=200, max_wait_s=2.0)
        ctx.serial.drain()

    names = [path.name for path in paths]
    ctx.io.result(f"ZMODEM send complete: {', '.join(names)} ({sent} bytes)")
    return CmdResult.ok(value=len(names), data={"files": names, "bytes": sent})


def _handler_recv(ctx: PluginContext, args: str) -> CmdResult:
    """Receive file(s) from the device via ZMODEM; the device runs ``sz <file>`` first.

    The sender names the files.  They arrive in a staging folder and move
    into the cap/ directory (or the specified directory) only when the
    transfer completes (registry ``xfer-recv-staged``).

    Args:
        ctx: Plugin context.
        args: Optional directory to save to (defaults to cap/).
    """
    from yesterwind_xyzmodem.exceptions import XYZModemError

    from termapy.zmodem_transport import TransferAborted, receive_files

    target_dir = args.strip()
    if target_dir:
        out_dir = _resolve_path(target_dir, _get_xfer_root(ctx))
        if not out_dir.is_dir():
            return CmdResult.fail(msg=f"Directory not found: {out_dir}")
    else:
        # The default is termapy's own transfer root (cap/): a write creates it.
        out_dir = ensure_folder(_get_xfer_root(ctx))

    ctx.io.output(f"  ZMODEM recv: waiting for data -> {out_dir} -- Esc to cancel")

    with ctx.serial.io():
        transport = _transport(ctx)
        with staged_receive(out_dir) as staging:
            try:
                receive_files(transport, staging.folder, _StatusLine(ctx))
                landed = staging.commit()
            except (TransferAborted, XYZModemError, TimeoutError, OSError) as e:
                return _failure(ctx, "recv", e)
            except ValueError as e:
                return CmdResult.fail(msg=str(e))
        # The sender may send a trailing ZFIN after we have stopped reading;
        # wait for the line to fall quiet, then discard that tail so no
        # leftover header reaches the terminal as a stray command.
        ctx.serial.wait_idle(timeout_ms=200, max_wait_s=2.0)
        ctx.serial.drain()

    names = ", ".join(path.name for path in landed) or "no files"
    ctx.io.result(f"ZMODEM recv complete: {names} -> {out_dir}")
    return CmdResult.ok(value=str(out_dir), data=[str(path) for path in landed])


# No ``COMMAND`` -- this file is underscore-prefixed so the plugin loader
# skips it.  ``xfer.py`` imports the handlers to mount them under
# ``/xfer.zmodem``; the rest is for tests that wire both ends.
__all__ = ["_handler_recv", "_handler_send"]
