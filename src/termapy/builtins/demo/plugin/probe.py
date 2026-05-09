"""Demo plugin: query a device and display formatted results.

This plugin is a working example showing how to build a termapy plugin
that interacts with a serial device.  It demonstrates the key pattern
that most plugin authors will need:

    serial_io() -> drain -> write -> read -> parse -> display

Copy this file as a starting point for your own plugins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from termapy.plugins import CapabilitySet, CmdResult, Command

if TYPE_CHECKING:
    from termapy.plugins import PluginContext


_LONG_HELP = """\
Send an AT command to the device, read the response, and display it.

  /probe              - run a standard device survey (ID, temp, status)
  /probe AT+TEMP      - send one command and show the response
  /probe AT+INFO      - send any command the device supports

This is a demo plugin - read the source to learn how to write your own.
Key concepts demonstrated:
  • ctx.serial.io()   context manager  (suppress terminal display during I/O)
  • ctx.serial.drain / send / read_raw  (the I/O cycle)
  • reading ctx.cfg for encoding and line_ending
  • declaring needs=CapabilitySet(serial_connected=True) so dispatch
    refuses the command when no port is open
  • formatted output via ctx.io.write with colors"""


def _send_cmd(ctx: PluginContext, command: str) -> str | None:
    """Send one AT command and return the decoded response text.

    Performs the drain -> write -> read cycle that most device-interaction
    plugins need.  Returns ``None`` when there is no response (timeout).

    Args:
        ctx: Plugin context for serial I/O and config.
        command: The command string to send (e.g. ``"AT+TEMP"``).

    Returns:
        Decoded response text with trailing whitespace stripped,
        or ``None`` on timeout.
    """
    encoding = ctx.cfg.get("encoding", "utf-8")
    line_ending = ctx.cfg.get("line_ending", "\r")

    # 1. Drain - discard any stale bytes sitting in the receive buffer
    ctx.serial.drain()

    # 2. Write - send the command with the configured line ending
    payload = (command + line_ending).encode(encoding)
    ctx.serial.write(payload)

    # 3. Read - wait for the device to reply (up to 1 second)
    raw = ctx.serial.read_raw(timeout_ms=1000)
    if not raw:
        return None

    return raw.decode(encoding, errors="replace").strip()


def _survey(ctx: PluginContext) -> None:
    """Run a quick device survey: ID, temperature, and status.

    Sends three commands and formats the results in a compact summary.
    This shows how to call ``_send_cmd`` multiple times and build up
    formatted output.

    Args:
        ctx: Plugin context for I/O and output.
    """
    queries = [
        ("Product ID", "AT+PROD-ID"),
        ("Temperature", "AT+TEMP"),
        ("Status", "AT+STATUS"),
    ]
    ctx.io.write("── device survey ──")
    for label, cmd in queries:
        resp = _send_cmd(ctx, cmd)
        if resp is None:
            ctx.io.write(f"  {label}: (no response)", "red")
        else:
            # Multi-line responses get indented under the label
            lines = resp.splitlines()
            ctx.io.write(f"  {label}: {lines[0]}")
            for line in lines[1:]:
                ctx.io.write(f"    {line}")
    ctx.io.write("── end survey ──")


def _handler(ctx: PluginContext, args: str) -> CmdResult:
    """Query a device over the serial port and display results.

    With no arguments, runs a survey of common device queries
    (product ID, temperature, status) and formats the output.
    With an argument, sends that as a single command.

    This handler demonstrates the essential plugin I/O pattern:
    wrap serial I/O in ``ctx.serial.io()`` so the terminal reader
    doesn't consume your responses, do your I/O, then display
    results.

    Args:
        ctx: Plugin context for serial I/O, config, and output.
        args: Optional AT command to send. Empty = run full survey.
    """
    # ctx.serial.io() suppresses terminal display and guarantees cleanup,
    # even if an exception occurs inside the block.
    with ctx.serial.io():
        command = args.strip()
        if not command:
            _survey(ctx)
            return CmdResult.ok()

        # Single command mode
        resp = _send_cmd(ctx, command)
        if resp is None:
            ctx.io.write(f"{command}: (no response)", "red")
        else:
            for line in resp.splitlines():
                ctx.io.write(f"  {line}")

    return CmdResult.ok()


# ── COMMAND (must be at end of file) ──────────────────────────────────────────
COMMAND = Command(
    "Query the device and display the response.",
    name="probe",
    args="{command}",
    long_help=_LONG_HELP,
    handler=_handler,
    needs=CapabilitySet(serial_connected=True),
)
