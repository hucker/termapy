"""Built-in plugin: /raw -- send text to the device exactly as given."""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command, UsageError

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


# ── Handlers ─────────────────────────────────────────────────────────────────
# A handler's docstring is its developer help: /help.dev <command> prints it
# and developer /search indexes it, so keep it true (registry handler-docstring).


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Send the text encoded with the cfg encoding, with no line ending.

    The one ``/raw`` in every frontend.  A typed ``/raw <text>`` reaches
    this handler from ``ReplEngine.dispatch_full`` with the text exactly
    as typed (no directives, transforms or flag stripping) but through
    the same capability gate; ``raw_args`` keeps the ``$(VAR)`` transform
    off on any other route here.

    Args:
        ctx: Plugin context.
        args: The text to send.
    """
    if not args:
        raise UsageError()
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
    group="terminal",
    name="raw",
    args="<text>",
    help="Send raw text to serial (no transforms or line ending).",
    long_help=(
        "Sends the text exactly as typed, encoded with the config's\n"
        "encoding: no line ending, no $(VAR) expansion, no transforms,\n"
        "and no pacing (tx_char_delay_ms and tx_line_delay_ms apply to\n"
        "lines only).  Use it for a partial line or a device that frames\n"
        "without a line ending.  For a literal line WITH the line ending,\n"
        "paced, use {prefix}term.send."
    ),
    handler=_handler,
    cfg={"encoding": "encodes the text sent"},
    needs=CapabilitySet.SERIAL_CONNECTED,
    raw_args=True,
)
