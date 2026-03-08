"""Built-in plugin: binary protocol send/expect testing."""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from termapy.protocol import (
    format_hex,
    format_hex_dump,
    format_smart,
    format_spaced,
    match_response,
    parse_data,
    parse_proto_script,
    strip_ansi,
)

if TYPE_CHECKING:
    from termapy.plugins import PluginContext

NAME = "proto"
ARGS = "<subcommand> [args]"
HELP = "Binary protocol tools: send, run, hex, status."

# Subcommand dispatch table (populated at module level)
_SUBCMDS: dict[str, str] = {
    "send": "send <hex|\"text\">  — send raw bytes, show response",
    "run": "run <file.pro>      — run a protocol test script",
    "hex": "hex {on|off}        — toggle hex display mode",
    "status": "status              — show current proto state",
}


def handler(ctx: PluginContext, args: str) -> None:
    """Dispatch proto subcommands for binary protocol testing."""
    parts = args.strip().split(None, 1)
    if not parts:
        _show_usage(ctx)
        return

    subcmd = parts[0].lower()
    sub_args = parts[1] if len(parts) > 1 else ""

    if subcmd == "send":
        _cmd_send(ctx, sub_args)
    elif subcmd == "run":
        _cmd_run(ctx, sub_args)
    elif subcmd == "hex":
        _cmd_hex(ctx, sub_args)
    elif subcmd == "status":
        _cmd_status(ctx)
    else:
        ctx.write(f"Unknown subcommand: {subcmd}", "red")
        _show_usage(ctx)


def _show_usage(ctx: PluginContext) -> None:
    """Display proto subcommand help."""
    prefix = ctx.engine.prefix
    ctx.write(f"Usage: {prefix}proto <subcommand>")
    for name, desc in _SUBCMDS.items():
        ctx.write(f"  {desc}")


def _display_bytes(ctx: PluginContext, direction: str, data: bytes,
                   binary: bool = False) -> None:
    """Display TX or RX data, choosing format by packet size.

    Short packets (<=16 bytes) are shown inline. Binary data is shown
    as hex, text data uses smart format (mixed text/hex). Longer packets
    get a multi-line hex dump with offset and ASCII sidebar.

    Args:
        ctx: Plugin context for output.
        direction: Label prefix — ``"TX"`` (cyan) or ``"RX"`` (yellow).
        data: Raw bytes to display.
        binary: If True, display short packets as hex instead of smart format.
    """
    color = "cyan" if direction == "TX" else "yellow"
    if len(data) <= 16:
        fmt = format_hex(data) if binary else format_smart(data)
        ctx.write(f"  {direction}: {fmt}", color)
    else:
        ctx.write(f"  {direction} {len(data)} bytes:", color)
        for line in format_hex_dump(data):
            ctx.write(f"    {line}")


def _cmd_send(ctx: PluginContext, args: str) -> None:
    """Send raw bytes to the serial port and display the response.

    Parses hex and/or quoted text into bytes, transmits them (no line
    ending appended), then waits up to 1 second for a response frame.
    Displays both TX and RX as hex with byte count and round-trip time.

    Args:
        ctx: Plugin context for serial I/O and output.
        args: Hex bytes and/or quoted strings, e.g. ``'01 03 "OK\\r"'``.
    """
    if not args.strip():
        ctx.write("Usage: !!proto send <hex bytes or \"text\">", "red")
        return
    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    try:
        data = parse_data(args)
    except ValueError as e:
        ctx.write(f"Parse error: {e}", "red")
        return

    ctx.engine.set_proto_active(True)
    ctx.serial_drain()
    _display_bytes(ctx, "TX", data)
    t0 = time.monotonic()
    ctx.serial_write(data)
    response = ctx.serial_read_raw(1000)
    elapsed_ms = (time.monotonic() - t0) * 1000
    ctx.engine.set_proto_active(False)

    if response:
        _display_bytes(ctx, "RX", response)
        ctx.write(f"  ({len(response)} bytes, {elapsed_ms:.0f}ms)")
    else:
        ctx.write("  RX: (no response)", "red")


def _cmd_run(ctx: PluginContext, args: str) -> None:
    """Execute a ``.pro`` test script as a sequence of send/expect steps.

    Resolves the script file (checking the per-config ``proto/`` dir as
    fallback), parses it into steps, then executes each step sequentially:

    - **send**: transmit bytes to the serial port.
    - **expect**: wait for a response and match against the expected pattern
      (with wildcard support). Reports PASS or FAIL per step.
    - **delay**: pause between steps.

    Prints a summary at the end with total pass/fail counts.

    Args:
        ctx: Plugin context for serial I/O, filesystem, and output.
        args: Filename of the ``.pro`` script to run.
    """
    filename = args.strip()
    if not filename:
        ctx.write("Usage: !!proto run <file.pro>", "red")
        return

    # Resolve file path
    path = Path(filename)
    if not path.exists():
        alt = ctx.proto_dir / filename
        if alt.exists():
            path = alt
        else:
            ctx.write(f"File not found: {filename}", "red")
            if ctx.proto_dir != Path("."):
                ctx.write(f"  (also checked {ctx.proto_dir})", "dim")
            return

    if not ctx.is_connected():
        ctx.write("Not connected.", "red")
        return

    try:
        text = path.read_text(encoding="utf-8")
        settings, steps = parse_proto_script(text)
    except (ValueError, OSError) as e:
        ctx.write(f"Script error: {e}", "red")
        return

    if not steps:
        ctx.write("Script has no steps.", "red")
        return

    do_strip_ansi = settings.get("strip_ansi", False)
    quiet = False
    frame_gap = settings.get("frame_gap_ms", 0)

    # Suppress normal serial display and drain stale bytes
    ctx.engine.set_proto_active(True)
    ctx.serial_drain()

    script_name = settings.get("name") or path.name
    ctx.write(f"{'─' * 40}")
    ctx.write(f"  {script_name}", "bold underline bright_white")
    ctx.write(f"  {path.name} — {len(steps)} steps", "dim")
    ctx.write(f"{'─' * 40}")

    pass_count = 0
    fail_count = 0
    step_num = 0
    t_start = time.monotonic()

    for step in steps:
        # Check for stop
        if ctx.engine.in_script() is False and ctx.engine.in_script is not None:
            # Allow !!stop to abort proto scripts too
            pass

        if step.action == "quiet":
            quiet = True
            continue

        if step.action == "loud":
            quiet = False
            continue

        if step.action == "delay":
            time.sleep(step.timeout_ms / 1000.0)
            continue

        if step.action == "flush":
            ctx.serial_drain()
            continue

        if step.action == "cmd":
            line_ending = ctx.cfg.get("line_ending", "\r")
            enc = ctx.cfg.get("encoding", "utf-8")
            text = step.data.decode("utf-8")
            if not quiet:
                ctx.write(f"  CMD: {text}", "dim")
            ctx.serial_write((text + line_ending).encode(enc))
            # Use frame_gap to wait for response, then drain it
            response = ctx.serial_read_raw(1000, frame_gap)
            if not quiet:
                ctx.write(f"  CMD: flushed {len(response)} bytes", "dim")
            continue

        if step.action == "send":
            ctx.serial_drain()
            step_num += 1
            label = step.label or f"Step {step_num}"
            ctx.write(f"[PROTO] {label}")
            ctx.write(f"  TX:       {format_spaced(step.data, step.binary)}", "cyan")
            ctx.serial_write(step.data)

        elif step.action == "expect":
            if not step.label:
                # If expect has no label, it's part of the previous send step
                pass
            else:
                step_num += 1
                ctx.write(f"[PROTO] {step.label}")

            t0 = time.monotonic()
            response = ctx.serial_read_raw(step.timeout_ms, frame_gap)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if do_strip_ansi:
                response = strip_ansi(response)

            ctx.write(f"  Expected: {format_spaced(step.data, step.binary)}", "dim")
            if response:
                ctx.write(f"  Actual:   {format_spaced(response, step.binary)}", "yellow")
                if match_response(step.data, response, step.mask):
                    ctx.write(f"  PASS ({len(response)} bytes, {elapsed_ms:.0f}ms)", "bright_green")
                    pass_count += 1
                else:
                    ctx.write("  FAIL", "red")
                    fail_count += 1
            else:
                ctx.write(f"  Actual:   (timeout after {step.timeout_ms}ms)", "red")
                ctx.write("  FAIL", "red")
                fail_count += 1

    # Summary
    ctx.engine.set_proto_active(False)
    elapsed_s = time.monotonic() - t_start
    total = pass_count + fail_count
    if total > 0:
        color = "bold bright_green" if fail_count == 0 else "bold red"
        ctx.write(f"{'─' * 40}")
        ctx.write(f"  Results: {pass_count}/{total} PASS ({elapsed_s:.3f}s)", color)
        ctx.write(f"{'─' * 40}")


def _cmd_hex(ctx: PluginContext, args: str) -> None:
    """Toggle hex display mode for all serial I/O.

    When enabled, received serial data is shown as hex bytes instead of
    decoded text. Accepts ``on``, ``off``, or no argument to toggle.

    Args:
        ctx: Plugin context for engine API access.
        args: ``"on"``, ``"off"``, or empty string to toggle.
    """
    arg = args.strip().lower()
    if arg == "on":
        ctx.engine.set_hex_mode(True)
        ctx.write("Hex display mode enabled.", "bright_green")
    elif arg == "off":
        ctx.engine.set_hex_mode(False)
        ctx.write("Hex display mode disabled.", "bright_green")
    else:
        # Toggle
        current = ctx.engine.get_hex_mode()
        ctx.engine.set_hex_mode(not current)
        state = "enabled" if not current else "disabled"
        ctx.write(f"Hex display mode {state}.", "bright_green")


def _cmd_status(ctx: PluginContext) -> None:
    """Show current proto state."""
    hex_mode = ctx.engine.get_hex_mode()
    connected = ctx.is_connected()
    ctx.write(f"Hex mode: {'on' if hex_mode else 'off'}")
    ctx.write(f"Connected: {'yes' if connected else 'no'}")
