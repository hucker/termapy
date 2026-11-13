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
    _handler_recv as _handler_xmodem_recv,
    _handler_send as _handler_xmodem_send,
)
from termapy.builtins.commands._ymodem_handlers import (
    _handler_recv as _handler_ymodem_recv,
    _handler_send as _handler_ymodem_send,
)
from termapy.defaults import cfg_value
from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


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
        root = cfg_value(ctx.cfg, "file_xfer_root")
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
    ctx.internal.apply_cfg("file_xfer_root", resolved)
    ctx.io.result(f"Transfer root: {resolved}")
    return CmdResult.ok(value=resolved)


# Long-blocking binary protocols, gated on a connected port + interactive
# host -- mirrors the gating the standalone /xmodem and /ymodem had before
# they moved under /xfer.


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    group="protocol",
    name="xfer",
    help="File transfer: settings and XMODEM / YMODEM send and receive.",
    intro=(
        "Move files between the host and the device over the serial link, using XMODEM or YMODEM."
    ),
    long_help=(
        "Settings:\n"
        "  {prefix}xfer.root [path]            Show or set the transfer root "
        "directory.\n"
        "\n"
        "XMODEM (single file, 128-byte blocks):\n"
        "  {prefix}xfer.xmodem.send <file>     Send a file to the device.\n"
        "  {prefix}xfer.xmodem.recv <file>     Receive a file from the device.\n"
        "\n"
        "YMODEM (batch, 1K blocks):\n"
        "  {prefix}xfer.ymodem.send <file> ... Send one or more files.\n"
        "  {prefix}xfer.ymodem.recv {dir}      Receive into a directory.\n"
    ),
    handler=None,
    sub_commands={
        "root": Command(
            args="{path}",
            help="Show or set the file transfer root directory.",
            handler=_handler_root,
            cfg={"file_xfer_root": "set for the session; bare shows it"},
            state=True,
        ),
        "xmodem": Command(
            help="XMODEM file transfer.",
            intro="Send or receive one file over XMODEM, in 128-byte blocks.",
            handler=None,
            needs=CapabilitySet.INTERACTIVE,
            sub_commands={
                "send": Command(
                    args="<file>",
                    help="Send a file via XMODEM to the device.",
                    handler=_handler_xmodem_send,
                    cfg={"file_xfer_root": "the folder files are sent from"},
                    needs=CapabilitySet.SERIAL_INTERACTIVE,
                ),
                "recv": Command(
                    args="<file>",
                    help="Receive a file via XMODEM from the device.",
                    handler=_handler_xmodem_recv,
                    cfg={"file_xfer_root": "the folder received files land in"},
                    needs=CapabilitySet.SERIAL_INTERACTIVE,
                ),
            },
        ),
        "ymodem": Command(
            help="YMODEM file transfer (batch, 1K blocks).",
            intro=(
                "Send or receive files over YMODEM, in 1K blocks; a send can take several files."
            ),
            handler=None,
            needs=CapabilitySet.INTERACTIVE,
            sub_commands={
                "send": Command(
                    args="<file> {file2} ...",
                    help="Send file(s) via YMODEM to the device.",
                    handler=_handler_ymodem_send,
                    cfg={"file_xfer_root": "the folder files are sent from"},
                    needs=CapabilitySet.SERIAL_INTERACTIVE,
                ),
                "recv": Command(
                    args="{directory}",
                    help="Receive file(s) via YMODEM from the device.",
                    handler=_handler_ymodem_recv,
                    cfg={"file_xfer_root": "the folder received files land in"},
                    needs=CapabilitySet.SERIAL_INTERACTIVE,
                ),
            },
        ),
    },
)
