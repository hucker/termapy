"""Demo plugin: send text with XMODEM CRC-16 appended.

This plugin demonstrates using termapy's CRC registry to compute
a checksum and append it to outgoing serial data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

from termapy.plugins import Command
from termapy.protocol import get_crc_registry
from termapy.scripting import CmdResult


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send text with an XMODEM CRC-16 appended.

    Computes the CRC over the argument bytes and transmits
    ``<text> <CRC>\\n`` to the serial port.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Text to send.
    """
    if not args.strip():
        return CmdResult.fail(msg="Usage: /crcsend <text>")
    crc = get_crc_registry()["crc16-xmodem"].compute(args.encode())
    ctx.serial_send(f"{args} {crc:04X}")
    ctx.write(f"Sent: {args} {crc:04X}", "green")
    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Send text with XMODEM CRC-16 appended.",
    name="crcsend",
    args="<text>",
    handler=_handler,
)
