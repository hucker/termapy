"""Built-in plugin: /raw -- send text to the device exactly as given."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send the text encoded with the cfg encoding, with no line ending.

    The one ``/raw`` in every frontend.  A typed ``/raw <text>`` reaches
    this handler from ``ReplEngine.dispatch_full`` with the text exactly
    as typed (no directives, transforms or flag stripping); ``raw_args``
    keeps the ``$(VAR)`` transform off on any other route here.

    Args:
        ctx: Plugin context.
        args: The text to send.
    """
    if not args:
        raise UsageError()
    if not ctx.serial.is_connected():
        return CmdResult.fail(msg="Not connected.")
    try:
        ctx.serial.write(args.encode(ctx.cfg.get("encoding", "utf-8")))
    # OSError covers pyserial's SerialException; the encode can fail on a
    # character outside the cfg encoding, or on an unknown encoding.
    except (OSError, UnicodeError, LookupError) as e:
        return CmdResult.fail(msg=f"Send error: {e}")
    # SPECIAL CASE: like /term.send, a fire-and-forget send; the device's
    # reply arrives through the read pipeline, not this CmdResult.
    return CmdResult.ok(value="")


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    name="raw",
    args="<text>",
    help="Send raw text to serial (no transforms or line ending).",
    long_help=(
        "Sends the text exactly as typed, encoded with the config's\n"
        "encoding: no line ending, no $(VAR) expansion, no transforms.\n"
        "Use it for a partial line or a device that frames without a\n"
        "line ending.  For a literal line WITH the line ending, use\n"
        "{prefix}term.send."
    ),
    handler=_handler,
    raw_args=True,
)
