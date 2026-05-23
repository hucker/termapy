"""Built-in plugin: file-transfer commands (settings + XMODEM + YMODEM).

``/xfer`` owns everything related to moving files over the serial
link: the root-directory setting, and the XMODEM / YMODEM
send/recv operations.  Handlers for the binary protocols live in
the private sibling modules ``_xmodem_handlers`` and
``_ymodem_handlers`` (underscore-prefixed so the loader skips
them); this file is the public mounting point.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from termapy.builtins.commands._xmodem_handlers import (
    _handler_recv as _xmodem_recv,
)
from termapy.builtins.commands._xmodem_handlers import (
    _handler_send as _xmodem_send,
)
from termapy.builtins.commands._ymodem_handlers import (
    _handler_recv as _ymodem_recv,
)
from termapy.builtins.commands._ymodem_handlers import (
    _handler_send as _ymodem_send,
)
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler_root(ctx: PluginContext, args: str) -> CmdResult:
    """Show or set the file transfer root directory.

    With no argument, shows the current root.  With a path argument,
    sets ``file_xfer_root`` in the config.  Both XMODEM and YMODEM
    resolve relative paths against this directory.

    Args:
        ctx: Plugin context.
        args: Optional directory path to set.
    """
    arg = args.strip()

    if not arg:
        root = ctx.cfg.get("file_xfer_root", "")
        if root:
            resolved = Path(root).resolve()
            ctx.io.result(str(resolved))
            return CmdResult.ok(value=str(resolved))
        ctx.io.result(f"{ctx.fs.cap_dir}  (default)")
        return CmdResult.ok(value=str(ctx.fs.cap_dir))

    # Set the root
    path = Path(arg)
    if not path.is_dir():
        return CmdResult.fail(msg=f"Directory not found: {path.resolve()}")

    resolved = str(path.resolve())
    ctx.engine.apply_cfg("file_xfer_root", resolved)
    ctx.io.result(f"Transfer root: {resolved}")
    return CmdResult.ok(value=resolved)


# Long-blocking binary protocols, gated on a connected port + interactive
# host -- mirrors the gating the standalone /xmodem and /ymodem had before
# they moved under /xfer.
_BINARY_NEEDS = CapabilitySet(serial_connected=True, interactive=True)


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="xfer",
    help="File transfer: settings and XMODEM / YMODEM send and receive.",
    long_help=(
        "Move files between the host and the device over the serial "
        "link.\n"
        "\n"
        "Settings:\n"
        "  /xfer.root [path]            Show or set the transfer root "
        "directory.\n"
        "\n"
        "XMODEM (single file, 128-byte blocks):\n"
        "  /xfer.xmodem.send <file>     Send a file to the device.\n"
        "  /xfer.xmodem.recv <file>     Receive a file from the device.\n"
        "\n"
        "YMODEM (batch, 1K blocks):\n"
        "  /xfer.ymodem.send <file> ... Send one or more files.\n"
        "  /xfer.ymodem.recv {dir}      Receive into a directory.\n"
    ),
    handler=None,
    sub_commands={
        "root": Command(
            args="{path}",
            help="Show or set the file transfer root directory.",
            handler=_handler_root,
        ),
        "xmodem": Command(
            help="XMODEM file transfer.",
            handler=None,
            needs=CapabilitySet(interactive=True),
            sub_commands={
                "send": Command(
                    args="<file>",
                    help="Send a file via XMODEM to the device.",
                    handler=_xmodem_send,
                    needs=_BINARY_NEEDS,
                ),
                "recv": Command(
                    args="<file>",
                    help="Receive a file via XMODEM from the device.",
                    handler=_xmodem_recv,
                    needs=_BINARY_NEEDS,
                ),
            },
        ),
        "ymodem": Command(
            help="YMODEM file transfer (batch, 1K blocks).",
            handler=None,
            needs=CapabilitySet(interactive=True),
            sub_commands={
                "send": Command(
                    args="<file> {file2} ...",
                    help="Send file(s) via YMODEM to the device.",
                    handler=_ymodem_send,
                    needs=_BINARY_NEEDS,
                ),
                "recv": Command(
                    args="{directory}",
                    help="Receive file(s) via YMODEM from the device.",
                    handler=_ymodem_recv,
                    needs=_BINARY_NEEDS,
                ),
            },
        ),
    },
)
